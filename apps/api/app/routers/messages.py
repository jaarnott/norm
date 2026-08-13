import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db, get_config_db, SessionLocal, _ConfigSessionLocal
from app.db.models import User
from app.auth.dependencies import get_current_user
from app.services.supervisor import handle_message
from app.services.venue_service import user_can_access_venue

logger = logging.getLogger(__name__)

router = APIRouter()

TURN_FAILED_TEXT = (
    "Something went wrong while I was working on this, so I had to stop. "
    "Your message is saved — please try again."
)


def _persist_failed_turn(
    thread_id: str | None,
    message: str,
    user_id: str | None,
    venue_id: str | None,
    error_text: str = TURN_FAILED_TEXT,
) -> str | None:
    """Make sure a failed turn still leaves the user's message in a thread.

    A turn that dies mid-flight rolls back its transaction; before this net,
    that took the user's message — and for a new conversation the entire
    thread — with it (production, 05 Aug 2026: two questions vanished when a
    usage-row collision aborted the turn). Runs on a fresh session because the
    turn's own session may be poisoned by the very error being handled.
    Returns the thread id the message now lives in, or None if persisting
    itself failed. Never raises.
    """
    from sqlalchemy import text as sa_text

    from app.db.models import Thread, Message

    session = SessionLocal()
    try:
        # Critical transaction: inserts only. The crashed turn's session may
        # still hold row locks (it isn't closed until after this net runs), and
        # inserts are the one write that can't block behind them.
        thread = None
        if thread_id:
            thread = session.query(Thread).filter(Thread.id == thread_id).first()
        if thread is None:
            thread = Thread(
                user_id=user_id,
                venue_id=venue_id,
                domain="unknown",
                intent="error",
                status="completed",
                raw_prompt=message,
                extracted_fields={},
                missing_fields=[],
            )
            session.add(thread)
            session.flush()
        # The tool-loop path commits the user message up front, so it may
        # already be there — only add it when it isn't the latest user message.
        last_user = (
            session.query(Message)
            .filter(Message.thread_id == thread.id, Message.role == "user")
            .order_by(Message.created_at.desc())
            .first()
        )
        if last_user is None or last_user.content != message:
            session.add(Message(thread_id=thread.id, role="user", content=message))
        session.add(Message(thread_id=thread.id, role="assistant", content=error_text))
        session.commit()
        saved_id = thread.id
    except Exception:
        logger.exception("Failed to persist failed turn (thread_id=%s)", thread_id)
        session.rollback()
        session.close()
        return None

    # Best-effort, separate transaction: a thread the tool loop early-committed
    # as "in_progress" would otherwise sit spinning forever. The dying turn may
    # still hold a lock on the row, so cap the wait instead of hanging the
    # error path — the messages above are already safe either way.
    try:
        session.execute(sa_text("SET LOCAL lock_timeout = '2s'"))
        session.execute(
            sa_text(
                "UPDATE threads SET status = 'completed' "
                "WHERE id = :tid AND status = 'in_progress'"
            ),
            {"tid": saved_id},
        )
        session.commit()
    except Exception:
        logger.warning("Could not settle thread status (thread_id=%s)", saved_id)
        session.rollback()
    finally:
        session.close()
    return saved_id


def _assert_venue_access(db: Session, user: User, venue_id: str | None) -> None:
    """Reject a caller-supplied venue the user has no access to.

    The venue picker is already scoped by get_user_venues, but the send path
    trusted req.venue_id verbatim — so a stale or hand-supplied id could reach a
    venue the user doesn't hold. No admin bypass: an admin is scoped to their
    venues list too, same as the picker.
    """
    if not user_can_access_venue(db, user.id, venue_id):
        raise HTTPException(
            status_code=403, detail="You don't have access to that venue."
        )


class PageContext(BaseModel):
    page_id: str
    agent: str
    # The document the user has open on the page (e.g. the recipe being edited:
    # recipe_id, venue_id, name, lines), so the agent can act on exactly that.
    document: dict | None = None


class MessageRequest(BaseModel):
    message: str
    thread_id: str | None = None
    venue_id: str | None = None
    page_context: PageContext | None = None


@router.post("/messages")
async def post_message(
    req: MessageRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    _assert_venue_access(db, user, req.venue_id)
    try:
        return handle_message(
            req.message,
            db,
            config_db=config_db,
            user_id=user.id,
            thread_id=req.thread_id,
            venue_id=req.venue_id,
            page_context=req.page_context.model_dump() if req.page_context else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        _persist_failed_turn(req.thread_id, req.message, user.id, req.venue_id)
        # Surface Anthropic auth / connection errors clearly
        exc_name = type(exc).__name__
        if "AuthenticationError" in exc_name:
            raise HTTPException(
                status_code=502,
                detail="Anthropic API key is invalid or missing. Check your ANTHROPIC_API_KEY setting.",
            )
        if "APIConnectionError" in exc_name:
            raise HTTPException(
                status_code=502,
                detail="Could not connect to Anthropic API. Check your network and API configuration.",
            )
        raise


@router.post("/messages/stream")
async def post_message_stream(
    req: MessageRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """SSE endpoint that streams thinking steps as they happen, then the final result."""
    # Validate before the stream opens — a 403 here is a clean HTTP error, not
    # an error event mid-stream. The worker below uses its own session.
    _assert_venue_access(db, user, req.venue_id)

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()
    # The real thread id, captured from the turn's own thread_created event so
    # the failure net below can find the thread even for a brand-new
    # conversation (req.thread_id is None until the turn creates one).
    turn_thread: dict = {}

    def on_event(event: dict):
        if event.get("type") == "thread_created" and event.get("thread_id"):
            turn_thread["id"] = event["thread_id"]
        loop.call_soon_threadsafe(queue.put_nowait, event)

    async def generate():
        # Bust proxy buffering (Codespaces, nginx, etc.) with a padding comment
        yield ": " + " " * 2048 + "\n\n"

        def run():
            from app.agents.tool_loop import set_event_callback

            set_event_callback(on_event)
            db = SessionLocal()
            config_db = _ConfigSessionLocal()
            try:
                result = handle_message(
                    req.message,
                    db,
                    config_db=config_db,
                    user_id=user.id,
                    thread_id=req.thread_id,
                    venue_id=req.venue_id,
                    page_context=req.page_context.model_dump()
                    if req.page_context
                    else None,
                )
                on_event({"type": "complete", "data": result})
            except Exception as exc:
                from app.services.billing_service import QuotaExceededError

                err_msg = str(exc).lower()
                if isinstance(exc, QuotaExceededError):
                    quota_text = "You've used all your tokens for this billing period."
                    persisted_id = _persist_failed_turn(
                        req.thread_id, req.message, user.id, req.venue_id, quota_text
                    )
                    if persisted_id and persisted_id != req.thread_id:
                        on_event({"type": "thread_created", "thread_id": persisted_id})
                    on_event(
                        {
                            "type": "quota_exceeded",
                            "used": exc.used,
                            "quota": exc.quota,
                            "message": quota_text,
                        }
                    )
                elif "prompt is too long" in err_msg or "too many tokens" in err_msg:
                    long_text = "This conversation has grown too long. Please start a new conversation to continue."
                    persisted_id = _persist_failed_turn(
                        req.thread_id or turn_thread.get("id"),
                        req.message,
                        user.id,
                        req.venue_id,
                        long_text,
                    )
                    if persisted_id and persisted_id != req.thread_id:
                        on_event({"type": "thread_created", "thread_id": persisted_id})
                    on_event({"type": "error", "message": long_text})
                else:
                    logger.exception("Stream error: %s", exc)
                    # Persist BEFORE emitting the error event: the frontend
                    # reacts to the event by re-fetching the thread, and that
                    # fetch must already see the user's message and this
                    # failure note or the refetch wipes the message on screen.
                    persisted_id = _persist_failed_turn(
                        req.thread_id or turn_thread.get("id"),
                        req.message,
                        user.id,
                        req.venue_id,
                    )
                    if persisted_id and persisted_id != req.thread_id:
                        on_event({"type": "thread_created", "thread_id": persisted_id})
                    on_event({"type": "error", "message": str(exc)})
            finally:
                db.close()
                config_db.close()

        bg = asyncio.ensure_future(asyncio.to_thread(run))
        try:
            while True:
                # Heartbeat: a long tool call (e.g. the invoice review's
                # per-invoice PDF extractions) can go a minute+ without emitting
                # an event. Without periodic bytes the browser / Codespaces
                # proxy drops the idle SSE connection and the client sees a
                # "network error". Send an SSE comment every 15s while waiting.
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
                # quota_exceeded is terminal too: the worker emits it and
                # returns, so without a break here the stream would keepalive
                # forever and the client's send-await would never resolve.
                if event["type"] in ("complete", "error", "quota_exceeded"):
                    break
                # Yield control so the ASGI server can flush the chunk to the client
                await asyncio.sleep(0)
        finally:
            await bg

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
