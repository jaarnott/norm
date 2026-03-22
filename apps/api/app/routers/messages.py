import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db, SessionLocal
from app.db.models import User
from app.auth.dependencies import get_current_user
from app.services.supervisor import handle_message

router = APIRouter()


class MessageRequest(BaseModel):
    message: str
    task_id: str | None = None
    venue_id: str | None = None


@router.post("/messages")
async def post_message(
    req: MessageRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return handle_message(req.message, db, user_id=user.id, task_id=req.task_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
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
    user: User = Depends(get_current_user),
):
    """SSE endpoint that streams thinking steps as they happen, then the final result."""
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def on_event(event: dict):
        loop.call_soon_threadsafe(queue.put_nowait, event)

    async def generate():
        # Bust proxy buffering (Codespaces, nginx, etc.) with a padding comment
        yield ": " + " " * 2048 + "\n\n"

        def run():
            from app.agents.tool_loop import set_event_callback
            set_event_callback(on_event)
            db = SessionLocal()
            try:
                result = handle_message(req.message, db, user_id=user.id, task_id=req.task_id)
                on_event({"type": "complete", "data": result})
            except Exception as exc:
                from app.services.billing_service import QuotaExceededError
                if isinstance(exc, QuotaExceededError):
                    on_event({"type": "quota_exceeded", "used": exc.used, "quota": exc.quota, "message": "You've used all your tokens for this billing period."})
                else:
                    on_event({"type": "error", "message": str(exc)})
            finally:
                db.close()

        bg = asyncio.ensure_future(asyncio.to_thread(run))
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] in ("complete", "error"):
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
