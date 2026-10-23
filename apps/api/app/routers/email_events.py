"""Resend's delivery webhook — ``POST /api/public/email-events/resend``
(plan §5.2 item 15, D32).

Resend signs webhooks the Svix way: headers ``svix-id``, ``svix-timestamp``
and ``svix-signature`` (``v1,<base64>``; several space-separated when a
secret was rotated), the signed content ``"{id}.{timestamp}.{raw body}"``,
HMAC-SHA256 with the secret's base64 part (after ``whsec_``), base64-encoded.
The timestamp must be within five minutes. A bad or missing signature is a
400 and nothing changes; with no ``RESEND_WEBHOOK_SECRET`` configured the
route answers 503, so a webhook can never be accepted unsigned.

The event's ``data.email_id`` is the id ``send_system_email`` stored as
``provider_message_id``; ``email.delivered`` / ``email.bounced`` /
``email.complained`` move that ``email_logs`` row. Other event types are
acknowledged and ignored. Every Norm email gains the status, not only the
entertainers' — the Emails page and the Register read it.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.db.engine import get_db
from app.services.email_service import update_delivery_status

logger = logging.getLogger(__name__)

PUBLIC_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
    "X-Content-Type-Options": "nosniff",
}
TOLERANCE_SECONDS = 5 * 60
EVENT_STATUS = {
    "email.delivered": "delivered",
    "email.bounced": "bounced",
    "email.complained": "complained",
}


class _PublicRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request: Request) -> Response:
            try:
                response = await handler(request)
            except StarletteHTTPException as exc:
                response = JSONResponse(
                    {"detail": exc.detail}, status_code=exc.status_code
                )
            for key, value in PUBLIC_HEADERS.items():
                response.headers.setdefault(key, value)
            return response

        return wrapped


router = APIRouter(
    prefix="/public/email-events", route_class=_PublicRoute, tags=["public"]
)


def verify_signature(
    secret: str,
    *,
    msg_id: str | None,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    now: float | None = None,
) -> bool:
    """True when one of the ``v1`` signatures matches and the timestamp is
    fresh. Pure, so the router test can sign its own payloads."""
    if not (secret and msg_id and timestamp and signature):
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > TOLERANCE_SECONDS:
        return False
    raw = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
    try:
        key = base64.b64decode(raw)
    except Exception:  # noqa: BLE001 — a malformed secret verifies nothing
        return False
    signed = f"{msg_id}.{ts}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, "sha256").digest()).decode()
    for part in signature.split():
        version, _, value = part.partition(",")
        if version == "v1" and hmac.compare_digest(value, expected):
            return True
    return False


@router.post("/resend")
async def resend_event(request: Request, db: Session = Depends(get_db)):
    if not settings.RESEND_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=503, detail="Email delivery webhook is not configured."
        )
    body = await request.body()
    if not verify_signature(
        settings.RESEND_WEBHOOK_SECRET,
        msg_id=request.headers.get("svix-id"),
        timestamp=request.headers.get("svix-timestamp"),
        signature=request.headers.get("svix-signature"),
        body=body,
    ):
        raise HTTPException(status_code=400, detail="Bad signature.")
    try:
        event = json.loads(body or b"{}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Bad payload.") from exc
    kind = str((event or {}).get("type") or "")
    status = EVENT_STATUS.get(kind)
    if status is None:
        return {"ok": True, "updated": False, "ignored": kind or None}
    data = (event or {}).get("data") or {}
    email_id = data.get("email_id") or data.get("id")
    detail = None
    if status == "bounced":
        bounce = data.get("bounce") or {}
        detail = bounce.get("message") or bounce.get("type") or "bounced"
    elif status == "complained":
        detail = "complained"
    log = update_delivery_status(db, email_id, status, detail=detail)
    db.commit()
    return {"ok": True, "updated": log is not None}
