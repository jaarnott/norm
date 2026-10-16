"""Norm Functions — the Inbox API: venue mailboxes, threads, the composer,
drafts, and the sends that move enquiries and events along
(``/api/functions/inbox/...``, ``/api/functions/proposals/{id}/send``,
``/api/functions/events/{id}/send-to-client``).

The rules live in ``app/services/functions/mailbox/``; this router decides
who is asking (``_ctx``: signed in, Functions switched on for their
organisation, then ``build_ctx`` — venues and mode), shapes requests and
responses, and turns refusals into HTTP:

* domain errors → their status with ``{"detail": {"code", "message"}}``
  (404 not found / not yours, 403, 409 stale / ``not_live`` in shadow mode /
  ``pricing_warning`` / ``mailbox_unavailable`` / ``google_not_configured``,
  422 bad input, 502 ``send_failed``);
* Gmail trouble while reading → 503 ``gmail_unavailable`` (try again) or
  409 ``mailbox_unavailable`` (reconnect).

A send answers 200 when Gmail confirmed it and 202 while it is still
``sending`` (Gmail's answer was lost — the outbox reconciler settles it;
poll ``GET /functions/inbox/outbox/{id}``). Every send needs an
``idempotency_key`` the client keeps across retries: the same key is the
same email, however many times it is posted.

Endpoints that read or write Gmail release their database connection first
(the services commit before calling Google).
"""

from __future__ import annotations

import datetime as dt
import html
from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_permission
from app.db.engine import get_config_db, get_db
from app.db.functions_models import FnOutbox
from app.db.models import User
from app.services.entitlements import org_id_for_user
from app.services.functions import entitlement
from app.services.functions.ctx import FunctionsCtx, build_ctx
from app.services.functions.errors import FunctionsError, NotFound, http_exception
from app.services.functions.mailbox import accounts as accounts_svc
from app.services.functions.mailbox import drafts as drafts_svc
from app.services.functions.mailbox import inbox as inbox_svc
from app.services.functions.mailbox import outbox as outbox_svc
from app.services.functions.mailbox import sending as sending_svc
from app.services.functions.mailbox import sync as sync_svc
from app.services.functions.mailbox.gmail_client import (
    GmailError,
    GmailNotFound,
    GmailTransient,
    MailboxAuthError,
)

router = APIRouter(prefix="/functions", tags=["functions-inbox"])


# ===========================================================================
# Plumbing
# ===========================================================================


def _not_enabled() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": "not_found",
            "message": "Functions is not switched on for your organisation.",
        },
    )


def _ctx_for(db: Session, config_db: Session, user: User) -> FunctionsCtx:
    org_id = org_id_for_user(user.id, db)
    if not entitlement.functions_enabled(db, config_db, org_id):
        raise _not_enabled()
    try:
        return build_ctx(db, user, org_id=org_id)
    except FunctionsError as exc:
        raise http_exception(exc) from exc


def _ctx(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
) -> FunctionsCtx:
    return _ctx_for(db, config_db, user)


@contextmanager
def _errors(db: Session):
    """Domain and Gmail refusals as HTTP. Nothing is rolled back here: the
    services write refusable steps in savepoints, and whatever a refused
    request left pending is discarded when its session closes."""
    try:
        yield
    except FunctionsError as exc:
        raise http_exception(exc) from exc
    except MailboxAuthError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "mailbox_unavailable", "message": exc.message},
        ) from exc
    except GmailNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Not found in the mailbox."},
        ) from exc
    except GmailTransient as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "gmail_unavailable",
                "message": "Gmail is busy or unreachable — try again in a moment.",
            },
        ) from exc
    except GmailError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "gmail_error", "message": f"Gmail said: {exc.message}"},
        ) from exc


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _sent(response: Response, result: dict) -> dict:
    if result.get("status") == "sending":
        response.status_code = 202
    return result


# ===========================================================================
# Mailboxes
# ===========================================================================


@router.get("/inbox/mailboxes")
def list_mailboxes(ctx: FunctionsCtx = Depends(_ctx), db: Session = Depends(get_db)):
    """Visible venues and their mailboxes, each with its health."""
    with _errors(db):
        return accounts_svc.list_mailboxes(db, ctx)


class ConnectBody(_Body):
    venue_id: str


@router.post("/inbox/mailboxes/connect")
def connect_mailbox(
    body: ConnectBody,
    request: Request,
    user: User = Depends(require_permission("settings:connectors")),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    """Start connecting a venue's mailbox: returns Google's consent URL.
    Needs ``settings:connectors`` and the venue (allowed in shadow mode)."""
    ctx = _ctx_for(db, config_db, user)
    with _errors(db):
        url = accounts_svc.start_connect(
            db,
            config_db,
            ctx,
            venue_id=body.venue_id,
            redirect_uri=accounts_svc.callback_uri(str(request.base_url)),
        )
    return {"authorize_url": url}


@router.get("/inbox/mailboxes/oauth/callback", response_class=HTMLResponse)
def connect_callback(
    request: Request,
    state: str = "",
    code: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    """Google's redirect. No login (the provider can't carry Norm's token):
    the single-use, unguessable state the connect call stored is the proof."""
    from app.routers.oauth import _result_page

    if error or not code:
        outcome = accounts_svc.ConnectOutcome(
            False,
            "Google didn't connect the mailbox"
            + (f" ({error})." if error else ".")
            + " Start again from Norm.",
        )
    else:
        outcome = accounts_svc.finish_connect(
            db,
            config_db,
            code=code,
            state=state,
            redirect_uri=accounts_svc.callback_uri(str(request.base_url)),
        )
    return HTMLResponse(
        content=_result_page(
            success=outcome.ok,
            connector="Venue mailbox",
            message=html.escape(outcome.message),
        ),
        status_code=200 if outcome.ok else 400,
    )


@router.post("/inbox/mailboxes/{account_id}/sync")
def sync_mailbox_now(
    account_id: str, ctx: FunctionsCtx = Depends(_ctx), db: Session = Depends(get_db)
):
    """ "Sync now" for one mailbox (skipped when another worker is syncing it
    or it synced in the last few seconds)."""
    with _errors(db):
        accounts_svc.get_account(db, ctx, account_id)
        return {"results": sync_svc.sync_now(db, ctx, account_ids=[account_id])}


class SyncBody(_Body):
    account_ids: list[str] | None = None


@router.post("/inbox/sync")
def sync_inbox_now(
    body: SyncBody | None = None,
    ctx: FunctionsCtx = Depends(_ctx),
    db: Session = Depends(get_db),
):
    """ "Sync now" for every visible connected mailbox — the Inbox calls this
    when it opens."""
    with _errors(db):
        return {
            "results": sync_svc.sync_now(
                db, ctx, account_ids=(body.account_ids if body else None)
            )
        }


# ===========================================================================
# Threads
# ===========================================================================


@router.get("/inbox/threads")
def list_threads(
    venue_id: str | None = None,
    account_id: str | None = None,
    folder: Literal["enquiry", "reservation", "other", "unfiled"] | None = None,
    linked: bool | None = None,
    unread: bool | None = None,
    q: str | None = None,
    since: dt.datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: FunctionsCtx = Depends(_ctx),
    db: Session = Depends(get_db),
):
    with _errors(db):
        return inbox_svc.list_threads(
            db,
            ctx,
            venue_id=venue_id,
            account_id=account_id,
            folder=folder,
            linked=linked,
            unread=unread,
            q=q,
            since=since,
            limit=limit,
            offset=offset,
        )


@router.get("/inbox/threads/{account_id}/{gmail_thread_id}")
def read_thread(
    account_id: str,
    gmail_thread_id: str,
    ctx: FunctionsCtx = Depends(_ctx),
    db: Session = Depends(get_db),
):
    """The thread with live, sanitised bodies, links, folder and drafts."""
    with _errors(db):
        return inbox_svc.read_thread(db, ctx, account_id, gmail_thread_id)


def _disposition(filename: str) -> str:
    """``attachment`` with the original name: an ASCII fallback plus the
    UTF-8 name (RFC 5987/6266), no characters that could break the header."""
    from urllib.parse import quote

    name = (
        "".join(
            ch for ch in filename if ch.isprintable() and ch not in '"\\/\r\n'
        ).strip()[:200]
        or "attachment"
    )
    fallback = "".join(ch if ch.isascii() else "_" for ch in name)
    return (
        f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"
    )


@router.get(
    "/inbox/threads/{account_id}/{gmail_thread_id}/messages/{gmail_message_id}"
    "/attachments/{attachment_id}"
)
def download_attachment(
    account_id: str,
    gmail_thread_id: str,
    gmail_message_id: str,
    attachment_id: str,
    ctx: FunctionsCtx = Depends(_ctx),
    db: Session = Depends(get_db),
):
    """Download an attachment (live from Gmail, ≤ 25 MB). ``attachment_id``
    may be the thread view's ``attachment_id`` or its stable ``part_id``.
    Always a download (never rendered here); active types (HTML, SVG,
    script…) come back as ``application/octet-stream``."""
    with _errors(db):
        f = inbox_svc.download_attachment(
            db, ctx, account_id, gmail_thread_id, gmail_message_id, attachment_id
        )
    return Response(
        content=f.data,
        media_type=f.mime_type,
        headers={
            "Content-Disposition": _disposition(f.filename),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, no-store",
        },
    )


class FolderBody(_Body):
    folder: Literal["enquiry", "reservation", "other"] | None = None


@router.put("/inbox/threads/{account_id}/{gmail_thread_id}/folder")
def set_folder(
    account_id: str,
    gmail_thread_id: str,
    body: FolderBody,
    ctx: FunctionsCtx = Depends(_ctx),
    db: Session = Depends(get_db),
):
    """File the thread (``null`` = unfiled); mirrored to the Actioned label."""
    with _errors(db):
        return inbox_svc.set_folder(db, ctx, account_id, gmail_thread_id, body.folder)


class LinkBody(_Body):
    enquiry_id: str | None = None
    event_id: str | None = None


@router.post("/inbox/threads/{account_id}/{gmail_thread_id}/links", status_code=201)
def link_thread(
    account_id: str,
    gmail_thread_id: str,
    body: LinkBody,
    ctx: FunctionsCtx = Depends(_ctx),
    db: Session = Depends(get_db),
):
    with _errors(db):
        return inbox_svc.link_thread(
            db,
            ctx,
            account_id,
            gmail_thread_id,
            enquiry_id=body.enquiry_id,
            event_id=body.event_id,
        )


@router.delete("/inbox/links/{link_id}")
def unlink_thread(
    link_id: str, ctx: FunctionsCtx = Depends(_ctx), db: Session = Depends(get_db)
):
    with _errors(db):
        out = inbox_svc.unlink(db, ctx, link_id)
        db.commit()
        return out


class EnquiryFromThreadBody(_Body):
    booking_type: str | None = None
    subject: str | None = None
    venue_id: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None


@router.post("/inbox/threads/{account_id}/{gmail_thread_id}/enquiry", status_code=201)
def create_enquiry_from_thread(
    account_id: str,
    gmail_thread_id: str,
    body: EnquiryFromThreadBody,
    ctx: FunctionsCtx = Depends(_ctx),
    db: Session = Depends(get_db),
):
    """A new enquiry for whoever wrote in (matched to a contact by email),
    with the thread linked to it."""
    with _errors(db):
        return inbox_svc.create_enquiry_from_thread(
            db, ctx, account_id, gmail_thread_id, **body.model_dump()
        )


@router.get("/inbox/search")
def search_gmail(
    account_id: str,
    q: str,
    limit: int = Query(20, ge=1, le=50),
    ctx: FunctionsCtx = Depends(_ctx),
    db: Session = Depends(get_db),
):
    """Live Gmail search for mail older than the mirror."""
    with _errors(db):
        return {"threads": inbox_svc.search_gmail(db, ctx, account_id, q, limit=limit)}


# ===========================================================================
# Composer
# ===========================================================================

Mode = Literal["reply", "reply_all", "forward", "new"]


class PrepareBody(_Body):
    account_id: str
    mode: Mode
    gmail_thread_id: str | None = None
    message_id: str | None = None


@router.post("/inbox/compose/prepare")
def prepare_compose(
    body: PrepareBody, ctx: FunctionsCtx = Depends(_ctx), db: Session = Depends(get_db)
):
    """Recipients, subject and the quoted history for reply / reply all /
    forward (from the live thread)."""
    with _errors(db):
        return inbox_svc.prepare_compose(
            db,
            ctx,
            body.account_id,
            mode=body.mode,
            gmail_thread_id=body.gmail_thread_id,
            message_id=body.message_id,
        )


class ComposeSendBody(_Body):
    account_id: str
    mode: Mode
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str | None = None
    body_html: str
    gmail_thread_id: str | None = None
    in_reply_to_gmail_message_id: str | None = None
    enquiry_id: str | None = None
    event_id: str | None = None
    idempotency_key: str
    price_override_reason: str | None = None
    quote: bool = True


@router.post("/inbox/compose/send")
def compose_send(
    body: ComposeSendBody,
    response: Response,
    ctx: FunctionsCtx = Depends(_ctx),
    db: Session = Depends(get_db),
):
    """Send through the outbox. 409 ``pricing_warning`` when a reply quotes
    a price on an enquiry with no proposal sent: post again with
    ``price_override_reason`` to send anyway (recorded)."""
    data = body.model_dump()
    account_id = data.pop("account_id")
    with _errors(db):
        result = inbox_svc.compose_send(db, ctx, account_id, **data)
    return _sent(response, result.as_dict())


@router.get("/inbox/outbox/{outbox_id}")
def outbox_status(
    outbox_id: str, ctx: FunctionsCtx = Depends(_ctx), db: Session = Depends(get_db)
):
    """A send's state (for a 202 still ``sending``)."""
    with _errors(db):
        row = (
            db.query(FnOutbox)
            .filter(FnOutbox.organization_id == ctx.org_id, FnOutbox.id == outbox_id)
            .first()
        )
        if row is None:
            raise NotFound("Send not found.")
        accounts_svc.get_account(db, ctx, row.account_id)
        return {
            **outbox_svc.OutboxResult.of(row).as_dict(),
            "purpose": row.purpose,
            "to": list(row.to_emails or []),
            "subject": row.subject,
            "sent_at": row.sent_at.isoformat() if row.sent_at else None,
            "enquiry_id": row.enquiry_id,
            "event_id": row.event_id,
        }


# ===========================================================================
# Drafts
# ===========================================================================


@router.get("/inbox/drafts")
def list_drafts(
    enquiry_id: str | None = None,
    event_id: str | None = None,
    account_id: str | None = None,
    gmail_thread_id: str | None = None,
    include_closed: bool = False,
    ctx: FunctionsCtx = Depends(_ctx),
    db: Session = Depends(get_db),
):
    with _errors(db):
        rows = drafts_svc.list_drafts(
            db,
            ctx,
            enquiry_id=enquiry_id,
            event_id=event_id,
            account_id=account_id,
            gmail_thread_id=gmail_thread_id,
            include_closed=include_closed,
        )
        return {"drafts": [drafts_svc.draft_dict(d) for d in rows]}


class DraftCreateBody(_Body):
    account_id: str | None = None
    enquiry_id: str | None = None
    event_id: str | None = None
    gmail_thread_id: str | None = None
    in_reply_to_gmail_message_id: str | None = None
    kind: Literal["email", "proposal"] = "email"
    mode: Mode | None = None
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str = ""
    body_html: str = ""


@router.post("/inbox/drafts", status_code=201)
def create_draft(
    body: DraftCreateBody,
    ctx: FunctionsCtx = Depends(_ctx),
    db: Session = Depends(get_db),
):
    with _errors(db):
        draft = drafts_svc.create_draft(db, ctx, **body.model_dump())
        db.commit()
        return drafts_svc.draft_dict(draft)


@router.get("/inbox/drafts/{draft_id}")
def get_draft(
    draft_id: str, ctx: FunctionsCtx = Depends(_ctx), db: Session = Depends(get_db)
):
    with _errors(db):
        return drafts_svc.draft_dict(drafts_svc.get_draft(db, ctx, draft_id))


class DraftUpdateBody(_Body):
    expected_version: int | None = None
    mode: Mode | None = None
    account_id: str | None = None
    gmail_thread_id: str | None = None
    in_reply_to_gmail_message_id: str | None = None
    to: list[str] | None = None
    cc: list[str] | None = None
    bcc: list[str] | None = None
    subject: str | None = None
    body_html: str | None = None


_DRAFT_RENAMES = {"to": "to_emails", "cc": "cc_emails", "bcc": "bcc_emails"}


@router.patch("/inbox/drafts/{draft_id}")
def update_draft(
    draft_id: str,
    body: DraftUpdateBody,
    ctx: FunctionsCtx = Depends(_ctx),
    db: Session = Depends(get_db),
):
    given = body.model_dump(exclude_unset=True)
    expected = given.pop("expected_version", None)
    fields = {_DRAFT_RENAMES.get(k, k): v for k, v in given.items()}
    with _errors(db):
        draft = drafts_svc.update_draft(
            db, ctx, draft_id, expected_version=expected, **fields
        )
        db.commit()
        return drafts_svc.draft_dict(draft)


@router.delete("/inbox/drafts/{draft_id}")
def discard_draft(
    draft_id: str,
    expected_version: int | None = None,
    ctx: FunctionsCtx = Depends(_ctx),
    db: Session = Depends(get_db),
):
    with _errors(db):
        return drafts_svc.draft_dict(
            drafts_svc.discard_draft(
                db, ctx, draft_id, expected_version=expected_version
            )
        )


class DraftSendBody(_Body):
    idempotency_key: str
    price_override_reason: str | None = None


@router.post("/inbox/drafts/{draft_id}/send")
def send_draft(
    draft_id: str,
    body: DraftSendBody,
    response: Response,
    ctx: FunctionsCtx = Depends(_ctx),
    db: Session = Depends(get_db),
):
    with _errors(db):
        result = drafts_svc.send_draft(
            db,
            ctx,
            draft_id,
            idempotency_key=body.idempotency_key,
            price_override_reason=body.price_override_reason,
        )
    return _sent(response, result if isinstance(result, dict) else result.as_dict())


class DraftHandBody(_Body):
    expected_version: int | None = None


@router.post("/inbox/drafts/{draft_id}/send-to-gmail")
def send_draft_to_gmail(
    draft_id: str,
    body: DraftHandBody | None = None,
    ctx: FunctionsCtx = Depends(_ctx),
    db: Session = Depends(get_db),
):
    """Hand the draft to the venue mailbox (Gmail owns it until it is sent
    there, deleted there, or brought back)."""
    with _errors(db):
        draft = drafts_svc.send_to_gmail(
            db, ctx, draft_id, expected_version=body.expected_version if body else None
        )
        return drafts_svc.draft_dict(draft)


@router.post("/inbox/drafts/{draft_id}/bring-back")
def bring_back_draft(
    draft_id: str, ctx: FunctionsCtx = Depends(_ctx), db: Session = Depends(get_db)
):
    with _errors(db):
        return drafts_svc.draft_dict(drafts_svc.bring_back(db, ctx, draft_id))


# ===========================================================================
# Sends that move enquiries and events
# ===========================================================================


class ProposalSendBody(_Body):
    mode: Literal["email", "proposal"]
    body_html: str
    subject: str | None = None
    to: list[str] | None = None
    cc: list[str] | None = None
    account_id: str | None = None
    gmail_thread_id: str | None = None
    idempotency_key: str
    price_override_reason: str | None = None


@router.post("/proposals/{enquiry_id}/send")
def send_proposal(
    enquiry_id: str,
    body: ProposalSendBody,
    response: Response,
    ctx: FunctionsCtx = Depends(_ctx),
    db: Session = Depends(get_db),
):
    """``proposal``: freeze the proposal + options as a version, email the
    quote link (``{{quote_link}}`` in the body, or appended) — the enquiry is
    quoted once sent. ``email``: a plain reply on the enquiry's thread."""
    with _errors(db):
        result = sending_svc.send_enquiry_email(
            db, ctx, enquiry_id, **body.model_dump()
        )
    return _sent(response, result)


class SendToClientBody(_Body):
    to: list[str] | None = None
    cc: list[str] | None = None
    subject: str | None = None
    body_html: str | None = None
    account_id: str | None = None
    gmail_thread_id: str | None = None
    idempotency_key: str


@router.post("/events/{event_id}/send-to-client")
def send_to_client(
    event_id: str,
    body: SendToClientBody,
    response: Response,
    ctx: FunctionsCtx = Depends(_ctx),
    db: Session = Depends(get_db),
):
    """Allow the client's emails into the event portal and email them the
    link from the venue mailbox (``{{portal_link}}`` in a custom body)."""
    with _errors(db):
        result = sending_svc.send_event_portal_link(
            db, ctx, event_id, **body.model_dump()
        )
    return _sent(response, result)
