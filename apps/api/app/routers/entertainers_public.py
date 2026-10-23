"""The performers' public routes — no login (plan §11.2–11.4, D14).

* ``/api/public/entertainers/{token}/...`` — the portal (purpose ``portal``).
* ``/api/public/join/{token}/...`` — the onboarding form (purposes
  ``onboarding``, ``band_member``, ``declaration``) with its code step.

Every response — errors included — carries ``Cache-Control: no-store``,
``Referrer-Policy: no-referrer``, ``X-Robots-Tag: noindex, nofollow`` and
``nosniff`` (``_PublicRoute``). One 404 answers an unknown, revoked, expired
or wrong-purpose link, a performer outside the organisation and a module
that is off, so a link can't be probed; the portal in shadow is a 404 too
(``not_yet``). Rate limits are Postgres fixed-window counters
(``app/mcp/ratelimit.py``), by a hash of the client IP and by link.

Writes commit, then send: a code's email and the confirmation-triggered
emails go out after the request's facts are committed (``emails.collecting``).
A wrong code is COMMITTED before the refusal — a rolled-back attempt would
be a free guess.
"""

from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager

import sentry_sdk
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db.engine import get_db
from app.mcp.ratelimit import check_rate_limit
from app.services.entertainers import emails, onboarding, portal
from app.services.entertainers import tokens as tokens_svc
from app.services.finance_core.errors import FinanceError, http_exception

logger = logging.getLogger(__name__)

PUBLIC_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
    "X-Content-Type-Options": "nosniff",
}
NOT_FOUND = {"code": "not_found", "message": "This link isn't valid."}
MAX_UPLOAD = 10 * 1024 * 1024


class _PublicRoute(APIRoute):
    """The public headers on every response, including errors and an
    unexpected failure (answered as a JSON 500, never a bare one)."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request: Request) -> Response:
            try:
                response = await handler(request)
            except StarletteHTTPException as exc:
                response = JSONResponse(
                    {"detail": exc.detail},
                    status_code=exc.status_code,
                    headers=getattr(exc, "headers", None),
                )
            except RequestValidationError as exc:
                response = JSONResponse(
                    {"detail": jsonable_encoder(exc.errors())}, status_code=422
                )
            except Exception as exc:  # noqa: BLE001 — answered, logged, reported
                logger.exception("entertainers public route failed: %s", self.path)
                sentry_sdk.capture_exception(exc)
                response = JSONResponse(
                    {
                        "detail": {
                            "code": "server_error",
                            "message": "Something went wrong on our side — please try again.",
                        }
                    },
                    status_code=500,
                )
            for key, value in PUBLIC_HEADERS.items():
                response.headers.setdefault(key, value)
            return response

        return wrapped


router = APIRouter(prefix="/public", route_class=_PublicRoute, tags=["public"])


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


def client_ip(request: Request) -> str:
    """Google's load balancer appends ``<client>, <lb>``: the client is the
    second from the right (``routers/public.py``'s rule)."""
    hops = [
        h.strip()
        for h in request.headers.get("x-forwarded-for", "").split(",")
        if h.strip()
    ]
    if len(hops) >= 2:
        return hops[-2]
    if hops:
        return hops[0]
    return request.client.host if request.client else "unknown"


def _ip_key(request: Request) -> str:
    return hashlib.sha256(client_ip(request).encode()).hexdigest()[:32]


def _limit(db: Session, bucket: str, limit: int, window: int) -> None:
    check_rate_limit(db, f"entpub:{bucket}", limit, window)


def _quietly_over(db: Session, bucket: str, limit: int, window: int) -> bool:
    try:
        _limit(db, bucket, limit, window)
    except HTTPException:
        return True
    return False


def _fail(err: FinanceError) -> HTTPException:
    exc = http_exception(err)
    errors = getattr(err, "errors", None)
    if errors:
        exc.detail = {**exc.detail, "errors": errors}
    return exc


@contextmanager
def _writing(db: Session):
    """Savepoint, commit, then the queued emails."""
    with emails.collecting() as pending:
        try:
            with db.begin_nested():
                yield
        except FinanceError as exc:
            raise _fail(exc) from exc
        db.commit()
    emails.run_pending(db, pending)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=NOT_FOUND)


# ===========================================================================
# The portal — /api/public/entertainers/{token}
# ===========================================================================


def _portal(db: Session, token: str):
    try:
        return portal.resolve(db, token)
    except FinanceError as err:
        raise _fail(err) from err


@router.get("/entertainers/{token}")
def portal_page(token: str, request: Request, db: Session = Depends(get_db)):
    _limit(db, f"portal-view:{_ip_key(request)}", 120, 60)
    row, performer = _portal(db, token)
    tokens_svc.touch(db, row)
    out = portal.page(db, row, performer)
    db.commit()
    return out


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actual_start_time: str = Field(max_length=8)
    actual_end_time: str = Field(max_length=8)
    actual_ends_next_day: bool = False


class DnpBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=2000)


@router.post("/entertainers/{token}/performances/{performance_id}/confirm")
def portal_confirm(
    token: str,
    performance_id: str,
    body: ConfirmBody,
    request: Request,
    db: Session = Depends(get_db),
):
    _limit(db, f"portal-write:{_ip_key(request)}", 60, 60)
    row, performer = _portal(db, token)
    with _writing(db):
        portal.confirm(
            db,
            row,
            performance_id,
            actual_start_time=body.actual_start_time,
            actual_end_time=body.actual_end_time,
            actual_ends_next_day=body.actual_ends_next_day,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    out = portal.page(db, row, performer)
    db.commit()
    return out


@router.post("/entertainers/{token}/performances/{performance_id}/did-not-perform")
def portal_dnp(
    token: str,
    performance_id: str,
    body: DnpBody,
    request: Request,
    db: Session = Depends(get_db),
):
    _limit(db, f"portal-write:{_ip_key(request)}", 60, 60)
    row, performer = _portal(db, token)
    with _writing(db):
        portal.confirm(
            db,
            row,
            performance_id,
            did_not_perform=True,
            reason=body.reason,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    out = portal.page(db, row, performer)
    db.commit()
    return out


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(
            status_code=413,
            detail={"code": "invalid", "message": "The file is larger than 10 MB."},
        )
    return data


@router.post("/entertainers/{token}/invoices")
async def portal_upload(
    token: str,
    request: Request,
    file: UploadFile = File(...),
    performance_ids: str = Form(...),
    invoice_number: str | None = Form(default=None),
    invoice_amount: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    _limit(db, f"portal-upload:{_ip_key(request)}", 20, 3600)
    row, performer = _portal(db, token)
    data = await _read_upload(file)
    try:
        ids = (
            json.loads(performance_ids)
            if performance_ids.strip().startswith("[")
            else [p for p in performance_ids.split(",")]
        )
    except ValueError:
        ids = []
    with _writing(db):
        portal.upload_invoice(
            db,
            row,
            performer,
            performance_ids=[str(i).strip() for i in ids if str(i).strip()],
            data=data,
            filename=file.filename,
            content_type=file.content_type,
            invoice_number=invoice_number,
            invoice_amount=invoice_amount,
        )
    out = portal.page(db, row, performer)
    db.commit()
    return out


# ===========================================================================
# Onboarding — /api/public/join/{token}
# ===========================================================================


def _join(db: Session, token: str):
    try:
        return onboarding.resolve(db, token)
    except FinanceError as err:
        raise _fail(err) from err


@router.get("/join/{token}")
def join_form(token: str, request: Request, db: Session = Depends(get_db)):
    _limit(db, f"join-view:{_ip_key(request)}", 120, 60)
    row = _join(db, token)
    tokens_svc.touch(db, row)
    try:
        out = onboarding.form(db, row)
    except FinanceError as err:
        raise _fail(err) from err
    db.commit()
    return out


@router.post("/join/{token}/code/request", status_code=202)
def join_request_code(token: str, request: Request, db: Session = Depends(get_db)):
    """Always ``{"sent": true}``: the code goes to the invited address only,
    and the answer never says whether it went."""
    _limit(db, f"join-code:{_ip_key(request)}", 10, 900)
    row = _join(db, token)
    over = _quietly_over(db, f"join-code-link:{row.id}", 3, 900)
    if not over:
        with _writing(db):
            onboarding.request_code(db, row)
    return JSONResponse({"sent": True}, status_code=202)


class CodeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(max_length=12)


@router.post("/join/{token}/code/verify")
def join_verify_code(
    token: str, body: CodeBody, request: Request, db: Session = Depends(get_db)
):
    _limit(db, f"join-verify:{_ip_key(request)}", 20, 900)
    row = _join(db, token)
    _limit(db, f"join-verify-link:{row.id}", 30, 900)
    ok = onboarding.verify_code(db, row, body.code)
    db.commit()  # a wrong guess is counted even though we refuse it
    if not ok:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "bad_code",
                "message": "That code isn't right or has expired — request a new one.",
            },
        )
    return {"verified": True}


@router.post("/join/{token}/steps/{step}")
def join_step(
    token: str, step: str, body: dict, request: Request, db: Session = Depends(get_db)
):
    _limit(db, f"join-write:{_ip_key(request)}", 120, 900)
    row = _join(db, token)
    with _writing(db):
        out = onboarding.validate_step(db, row, step, body or {})
    return out


@router.post("/join/{token}/submit")
def join_submit(
    token: str, body: dict, request: Request, db: Session = Depends(get_db)
):
    _limit(db, f"join-submit:{_ip_key(request)}", 20, 900)
    row = _join(db, token)
    with _writing(db):
        out = onboarding.submit(
            db,
            row,
            body or {},
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    return out


@router.post("/join/{token}/documents", status_code=201)
async def join_upload(
    token: str,
    request: Request,
    kind: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    _limit(db, f"join-upload:{_ip_key(request)}", 20, 3600)
    row = _join(db, token)
    data = await _read_upload(file)
    with _writing(db):
        stored = onboarding.upload_document(
            db,
            row,
            kind=kind,
            data=data,
            filename=file.filename,
            content_type=file.content_type,
        )
    return {"file_id": stored.id}
