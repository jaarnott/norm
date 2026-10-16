"""Customer-facing routes — no login. The quote page, the client portal, web
forms, and the images they show (``/api/public/...``).

* **One 404 for every dead end.** An unknown, revoked or expired link, a
  record that's gone, an organisation that isn't live (forms: shadow or
  live) — all the same answer, so a link can't be probed.
* **Rate limits** are Postgres fixed-window counters (``app/mcp/ratelimit.py``,
  correct across Cloud Run instances), keyed by a hash of the client's IP from
  the right ``X-Forwarded-For`` hop (``client_ip``) and, where it matters, by
  link. Windows stay at or under an hour: ``/internal/mcp-gc`` deletes
  windows older than that.
* **Headers** on every response, errors included (``PublicRoute``):
  ``Cache-Control: no-store`` (images: immutable), ``Referrer-Policy:
  no-referrer``, ``X-Robots-Tag: noindex``, ``nosniff``. Form routes also
  answer ``Access-Control-Allow-Origin: *`` — ``forms.js`` runs on venue
  websites and posts as a CORS-simple request (``text/plain`` JSON, no
  credentials), so no preflight meets the app's CORS allowlist.
* **Portal sessions ride an httpOnly cookie**, path-scoped to the link
  (``/api/public/portal/<token>``), ``SameSite=Lax``, ``Secure`` outside
  local. A cookie works because the browser reaches the API on the page's
  own origin everywhere: the load balancer path-routes ``/api`` on the app
  domain in deployed environments, and Next's dev rewrite proxies ``/api``
  locally. Script on the page can't read it (unlike a header token kept in
  localStorage); writes also require a same-origin ``Origin`` when one is
  sent, outside local.

Business rules live in the services (``quote_page``, ``portal``, ``forms``);
this module checks limits, resolves links, commits, then sends the emails.
"""

from __future__ import annotations

import hashlib
import json
import logging
from urllib.parse import urlsplit

import sentry_sdk

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.db.engine import get_db
from app.mcp.ratelimit import check_rate_limit
from app.services.functions import forms as forms_svc
from app.services.functions import notify, portal, quote_page
from app.services.functions.errors import FunctionsError, http_exception

logger = logging.getLogger(__name__)

PUBLIC_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
    "X-Content-Type-Options": "nosniff",
}
IMAGE_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
    # Inert even if opened directly: no script, no plugins, no subresources.
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
}
MAX_FORM_BODY = 64 * 1024


class PublicRoute(APIRoute):
    """Puts the public headers (and, on form routes, CORS) on every response
    — including errors, and an unexpected failure, which is answered as a
    JSON 500 instead of escaping to a bare server error."""

    def get_route_handler(self):
        handler = super().get_route_handler()
        embeddable = "/public/forms/" in self.path

        async def public_handler(request: Request) -> Response:
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
                # A bare 500 from the server middleware would carry none of the
                # headers below — on a venue's website that reads as a network
                # error (no CORS header), with no message to show.
                logger.exception("public route failed: %s", self.path)
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
            if embeddable:
                response.headers["Access-Control-Allow-Origin"] = "*"
            return response

        return public_handler


router = APIRouter(prefix="/public", route_class=PublicRoute, tags=["public"])


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


def client_ip(request: Request) -> str:
    """The visitor's IP. Google's external load balancer APPENDS
    ``<client>, <load balancer>`` to ``X-Forwarded-For``, so the client is the
    second entry from the right — anything to its left was sent by the client
    and can be forged (``mcp_oauth``'s first-hop rule trusts exactly that
    part). A single entry is a direct hit or the local dev proxy.

    One gap remains outside this code: the API's run.app URL also accepts
    traffic (ingress "all"), where the client's own forged entry would sit
    second from the right. Restricting ingress to the load balancer closes it.
    """
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
    # Hashed: rate-limit rows outlive the request, and need not hold an IP.
    return hashlib.sha256(client_ip(request).encode()).hexdigest()[:32]


def _limit(db: Session, bucket: str, limit: int, window: int) -> None:
    """429 once ``bucket`` exceeds ``limit`` in ``window`` seconds. Commits
    (the counter must survive the request) — call before any other write."""
    check_rate_limit(db, f"fnpub:{bucket}", limit, window)


def _quietly_over(db: Session, bucket: str, limit: int, window: int) -> bool:
    """A limit the caller must not reveal (code sends): True when over."""
    try:
        _limit(db, bucket, limit, window)
    except HTTPException:
        return True
    return False


def _fail(err: FunctionsError) -> HTTPException:
    exc = http_exception(err)
    errors = getattr(err, "errors", None)
    if errors:
        exc.detail = {**exc.detail, "errors": errors}
    return exc


def _own_origin() -> str:
    parts = urlsplit(settings.app_url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def _require_same_origin(request: Request) -> None:
    """Cookie-carrying writes must come from Norm's own pages (SameSite=Lax
    already stops cross-site posts; this is the second lock)."""
    origin = request.headers.get("origin")
    if origin and not settings.is_local and origin.rstrip("/").lower() != _own_origin():
        raise HTTPException(status_code=403, detail="Cross-site request refused.")


class NoteBody(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class EmailBody(BaseModel):
    email: str = Field(max_length=320)


class VerifyBody(BaseModel):
    email: str = Field(max_length=320)
    code: str = Field(max_length=12)


class ConfirmBody(BaseModel):
    sections: list[str] = Field(max_length=3)
    note: str | None = Field(default=None, max_length=2000)
    changes: dict | None = None


# ---------------------------------------------------------------------------
# Quote page
# ---------------------------------------------------------------------------


@router.get("/quote/{token}")
def get_quote(token: str, request: Request, db: Session = Depends(get_db)):
    """The quote page: the latest sent proposal (a ready ``srcdoc``
    document) and the offered options with their current state."""
    _limit(db, f"quote-view:{_ip_key(request)}", 120, 60)
    try:
        row, ctx = quote_page.resolve_quote(db, token)
        payload = quote_page.quote_payload(db, ctx, row)
    except FunctionsError as err:
        raise _fail(err) from err
    db.commit()
    return payload


def _respond(token, option_id, kind, body, request, db):
    _limit(db, f"quote-act:{_ip_key(request)}", 20, 60)
    _limit(
        db,
        f"quote-act-token:{hashlib.sha256(token.encode()).hexdigest()[:32]}",
        20,
        300,
    )
    try:
        row, ctx = quote_page.resolve_quote(db, token)
        payload, notices = quote_page.respond(
            db, ctx, row, option_id, kind, note=body.note
        )
    except FunctionsError as err:
        raise _fail(err) from err
    db.commit()
    notify.run_after_commit(db, notices)
    return payload


@router.post("/quote/{token}/options/{option_id}/confirm")
def confirm_option(
    token: str,
    option_id: str,
    request: Request,
    body: NoteBody = Body(default_factory=NoteBody),
    db: Session = Depends(get_db),
):
    """The customer confirms an option — creating the event (once)."""
    return _respond(token, option_id, "confirm", body, request, db)


@router.post("/quote/{token}/options/{option_id}/decline")
def decline_option(
    token: str,
    option_id: str,
    request: Request,
    body: NoteBody = Body(default_factory=NoteBody),
    db: Session = Depends(get_db),
):
    return _respond(token, option_id, "decline", body, request, db)


# ---------------------------------------------------------------------------
# Client portal
# ---------------------------------------------------------------------------


def _cookie_path(request: Request, token: str) -> str:
    # Wherever this router is mounted, scope the cookie to this link's routes.
    path = request.url.path
    return path[: path.index(token) + len(token)]


def _portal(token: str, request: Request, db: Session):
    try:
        row, event = portal.resolve_portal(db, token)
    except FunctionsError as err:
        raise _fail(err) from err
    session = portal.session_for(db, event, request.cookies.get(portal.SESSION_COOKIE))
    return row, event, session


def _signed_in(token: str, request: Request, db: Session):
    row, event, session = _portal(token, request, db)
    if session is None:
        raise _fail(portal.SessionRequired("Sign in with the code we email you."))
    return row, event, session, portal.client_ctx(db, event, session)


def _page(db, ctx, event, session) -> dict:
    db.refresh(event)
    return portal.payload(db, ctx, event, email=session.email)


@router.get("/portal/{token}")
def get_portal(token: str, request: Request, db: Session = Depends(get_db)):
    """Signed in: the whole event. Otherwise only who the link is from."""
    _limit(db, f"portal-view:{_ip_key(request)}", 120, 60)
    _row, event, session = _portal(token, request, db)
    try:
        if session is None:
            out = portal.public_payload(db, event)
        else:
            out = portal.payload(
                db, portal.client_ctx(db, event, session), event, email=session.email
            )
    except FunctionsError as err:
        raise _fail(err) from err
    db.commit()
    return out


@router.post("/portal/{token}/code", status_code=202)
def request_code(
    token: str, body: EmailBody, request: Request, db: Session = Depends(get_db)
):
    """Email a sign-in code to an allowed address. The answer is the same
    whether or not the address is allowed (or throttled), and the email goes
    out after the response, so neither content nor timing tells."""
    _limit(db, f"portal-code:{_ip_key(request)}", 10, 900)
    row, event, _session = _portal(token, request, db)
    over = _quietly_over(db, f"portal-code-link:{row.id}", 10, 900)
    issued = None if over else portal.request_code(db, event, body.email)
    db.commit()
    if issued is not None:
        venue = (
            quote_page.venue_summary(db, event.organization_id, event.venue_id) or {}
        )
        address, code = issued
        return JSONResponse(
            {"sent": True},
            status_code=202,
            background=BackgroundTask(
                notify.portal_code,
                to=address,
                code=code,
                venue_name=venue.get("name") or "the venue",
                event_id=event.id,
            ),
        )
    return {"sent": True}


@router.post("/portal/{token}/verify")
def verify_code(
    token: str,
    body: VerifyBody,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Check a code; on success set the session cookie and return the event."""
    _limit(db, f"portal-verify:{_ip_key(request)}", 20, 900)
    row, event, _session = _portal(token, request, db)
    _limit(db, f"portal-verify-link:{row.id}", 30, 900)
    result = portal.verify_code(db, event, body.email, body.code)
    db.commit()  # a wrong guess is counted even though we refuse it
    if result is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "bad_code",
                "message": "That code isn't right or has expired — request a new one.",
            },
        )
    session, raw = result
    response.set_cookie(
        portal.SESSION_COOKIE,
        raw,
        max_age=int(portal.SESSION_TTL.total_seconds()),
        path=_cookie_path(request, token),
        httponly=True,
        secure=not settings.is_local,
        samesite="lax",
    )
    try:
        out = portal.payload(
            db, portal.client_ctx(db, event, session), event, email=session.email
        )
    except FunctionsError as err:
        raise _fail(err) from err
    db.commit()
    return out


@router.post("/portal/{token}/logout")
def logout(
    token: str, request: Request, response: Response, db: Session = Depends(get_db)
):
    _require_same_origin(request)
    _limit(db, f"portal-write:{_ip_key(request)}", 120, 60)
    _row, _event, session = _portal(token, request, db)
    if session is not None:
        portal.end_session(db, session)
        db.commit()
    response.delete_cookie(portal.SESSION_COOKIE, path=_cookie_path(request, token))
    return {"signed_out": True}


@router.put("/portal/{token}/event")
def save_event(
    token: str,
    request: Request,
    changes: dict = Body(...),
    db: Session = Depends(get_db),
):
    """Autosave: ``{expected_version, fields, contact, lists, menus, note}``
    (all optional). Returns the refreshed page — carry its versions forward."""
    _require_same_origin(request)
    _limit(db, f"portal-write:{_ip_key(request)}", 120, 60)
    _row, event, session, ctx = _signed_in(token, request, db)
    try:
        rows = portal.apply_changes(db, ctx, event, changes)
        notices = portal.edit_notices(db, ctx, event, rows)
        out = _page(db, ctx, event, session)
    except FunctionsError as err:
        raise _fail(err) from err
    db.commit()
    notify.run_after_commit(db, notices)
    return out


@router.post("/portal/{token}/confirm")
def confirm_sections(
    token: str, body: ConfirmBody, request: Request, db: Session = Depends(get_db)
):
    """Confirm (or re-confirm) sections, optionally saving ``changes`` first
    in the same transaction."""
    _require_same_origin(request)
    _limit(db, f"portal-write:{_ip_key(request)}", 120, 60)
    _row, event, session, ctx = _signed_in(token, request, db)
    try:
        notices = []
        if body.changes:
            rows = portal.apply_changes(db, ctx, event, body.changes)
            notices += portal.edit_notices(db, ctx, event, rows)
        notices += portal.confirm_sections(
            db, ctx, event, body.sections, note=body.note
        )
        out = _page(db, ctx, event, session)
    except FunctionsError as err:
        raise _fail(err) from err
    db.commit()
    notify.run_after_commit(db, notices)
    return out


@router.post("/portal/{token}/invite")
def invite(
    token: str, body: EmailBody, request: Request, db: Session = Depends(get_db)
):
    """Give a colleague access; they are emailed the link."""
    _require_same_origin(request)
    _limit(db, f"portal-write:{_ip_key(request)}", 120, 60)
    row, event, session, ctx = _signed_in(token, request, db)
    _limit(db, f"portal-invite-link:{row.id}", 10, 3600)
    try:
        notices = portal.invite(db, ctx, event, body.email, link_token=token)
        out = _page(db, ctx, event, session)
    except FunctionsError as err:
        raise _fail(err) from err
    db.commit()
    notify.run_after_commit(db, notices)
    return out


@router.get("/portal/{token}/print")
def print_event(token: str, request: Request, db: Session = Depends(get_db)):
    """The printable event — a session is required (Orbit's print page
    needed only the link)."""
    _limit(db, f"portal-view:{_ip_key(request)}", 120, 60)
    _row, event, session, ctx = _signed_in(token, request, db)
    try:
        out = portal.payload(db, ctx, event, email=session.email)
    except FunctionsError as err:
        raise _fail(err) from err
    db.commit()
    return out


# ---------------------------------------------------------------------------
# Web forms
# ---------------------------------------------------------------------------


@router.get("/forms/{slug}")
def get_form(slug: str, request: Request, db: Session = Depends(get_db)):
    """The form to render, with the signed ``form_token`` the submit needs."""
    _limit(db, f"form-view:{_ip_key(request)}", 60, 60)
    try:
        form = forms_svc.public_form(db, slug)
        return forms_svc.schema(db, form)
    except FunctionsError as err:
        raise _fail(err) from err


async def _form_body(request: Request) -> bytes:
    raw = await request.body()
    if len(raw) > MAX_FORM_BODY:
        raise HTTPException(status_code=413, detail="That's too much to send.")
    return raw


@router.post("/forms/{slug}/submit")
def submit_form(
    slug: str,
    request: Request,
    raw: bytes = Depends(_form_body),
    db: Session = Depends(get_db),
):
    """``{payload: {key: answer}, form_token, tracking: {utm_*, gclid, gbraid,
    wbraid, referrer, landing_page}}`` — read as JSON whatever the
    Content-Type, because ``forms.js`` posts ``text/plain`` to stay a simple
    cross-origin request."""
    try:
        body = json.loads(raw or b"{}")
    except (ValueError, RecursionError) as exc:
        raise HTTPException(status_code=400, detail="Send the form as JSON.") from exc
    try:
        form = forms_svc.public_form(db, slug)
    except FunctionsError as err:
        raise _fail(err) from err
    hashed = forms_svc.ip_hash(form.id, client_ip(request))
    _limit(db, f"form-submit:{form.id}:{hashed[:32]}", 5, 60)
    # And a ceiling for the whole form: the IP above can be forged on a
    # direct run.app request; this can't (the day ceiling is the service's).
    _limit(db, f"form-total:{form.id}", forms_svc.FORM_MINUTE_LIMIT, 60)
    try:
        result = forms_svc.submit(
            db,
            form,
            body,
            ip_hash_value=hashed,
            user_agent=request.headers.get("user-agent"),
        )
    except FunctionsError as err:
        raise _fail(err) from err
    db.commit()
    notify.run_after_commit(db, result.notices)
    return {"ok": True, "success_message": result.success_message}


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


@router.get("/files/{file_id}")
def get_file(file_id: str, request: Request, db: Session = Depends(get_db)):
    """An image something public references (proposal, brand, library, area
    photo), cached forever: a file id's bytes never change."""
    _limit(db, f"files:{_ip_key(request)}", 600, 60)
    row = quote_page.public_file(db, file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return Response(
        content=row.data, media_type=row.content_type, headers=IMAGE_HEADERS
    )
