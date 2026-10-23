"""Customer links — the quote page (``/q/<token>``) and the client portal
(``/e/<token>``).

A link is the only credential a customer holds, so:

* **Unguessable, and only its hash is stored.** A token is
  ``HMAC-SHA256(key, "<kind>:<row id>")`` — 256 bits, base64url (43 chars) —
  where the row id is a uuid4 from the OS CSPRNG and the key is derived from
  the server's ``JWT_SECRET``. ``fn_public_tokens`` keeps only the token's
  SHA-256: a copy of the database alone yields no working link.
* **Stable until revoked.** Because a token can be re-derived from its row,
  minting again for the same enquiry (or event) returns the SAME link — staff
  can copy it twice, a re-send doesn't orphan the first email, and the
  mailbox sync can recognise it in sent mail (``tokens_in_text``). A random
  token stored only as a hash could never be shown twice. Revoking ends a
  link for good; the next mint makes a new one.
* **Expiry.** Quote links last 180 days from the latest mint (each send
  extends them); portal links last until 30 days after the event date (read
  from the event at resolve time too, so moving the event later keeps its
  link alive).
* **Fails closed.** ``resolve`` returns the row or None — unknown, revoked,
  expired, wrong kind and record-gone all look the same, and the public
  routes answer every one of them with the same 404.

If ``JWT_SECRET`` is rotated, existing links keep resolving (resolution is by
hash) until they expire, but can no longer be re-derived: the next mint issues
a new link beside the old one, and that new link is the stable one from then
on. Rotation never breaks a link already in a customer's inbox.
"""

from __future__ import annotations

import datetime as dt
import hmac
import re

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.functions_models import (
    PUBLIC_TOKEN_KINDS,
    FnEvent,
    FnPortalAccess,
    FnPortalSession,
    FnPublicToken,
)
from app.db.models import _now, _uuid
from app.services import public_links
from app.services.functions import enquiries as enquiries_svc
from app.services.functions import events as events_svc
from app.services.functions.contacts import normalise_email
from app.services.functions.ctx import FunctionsCtx, require_live, require_staff
from app.services.functions.errors import Conflict, Invalid, flush

QUOTE_TTL = dt.timedelta(days=180)
#: A portal stays open this long after the event (final numbers, invoices).
PORTAL_GRACE = dt.timedelta(days=30)
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
#: The link shapes, as they appear in an email body (``tokens_in_text``).
_PATHS = {"quote": "q", "portal": "e"}
_IN_TEXT = {
    kind: re.compile(rf"/{path}/([A-Za-z0-9_-]{{43}})(?![A-Za-z0-9_-])")
    for kind, path in _PATHS.items()
}


# ---------------------------------------------------------------------------
# Token derivation — shared with the entertainers' links (services/public_links)
# ---------------------------------------------------------------------------


def _key() -> bytes:
    return public_links.key("functions")


def hash_token(token: str) -> str:
    """What ``fn_public_tokens.token_hash`` stores (SHA-256 hex)."""
    return public_links.hash_token(token)


def _derive(kind: str, row_id: str) -> str:
    return public_links.derive(kind, row_id, purpose="functions")


def link_url(kind: str, token: str) -> str:
    """``{app_url}/q/<token>`` or ``{app_url}/e/<token>`` — the customer page."""
    return public_links.link_url(f"{_PATHS[kind]}/{token}")


def quote_url(token: str) -> str:
    return link_url("quote", token)


def live_url(row: FnPublicToken, *, now: dt.datetime | None = None) -> str | None:
    """The customer URL of a LIVE link (not revoked, not expired), re-derived
    from its row id — None for a dead link, and for one issued under an
    earlier key (it still opens by hash until it expires, but can't be shown
    again; the next mint for the record issues a showable one beside it)."""
    if row.revoked_at is not None:
        return None
    if row.expires_at is not None and row.expires_at <= (now or _now()):
        return None
    token = _derive(row.kind, row.id)
    if not hmac.compare_digest(hash_token(token), row.token_hash):
        return None
    return link_url(row.kind, token)


def portal_url(token: str) -> str:
    return link_url("portal", token)


def tokens_in_text(text: str | None, kind: str = "quote") -> list[str]:
    """Tokens of ``kind`` linked from a message body (for the mailbox sync:
    a quote link in Gmail-sent mail is a quote sent outside Norm)."""
    return list(dict.fromkeys(_IN_TEXT[kind].findall(text or "")))


# ---------------------------------------------------------------------------
# Minting
# ---------------------------------------------------------------------------


def _portal_expiry(event_date: dt.date) -> dt.datetime:
    """Midnight UTC after ``event_date + 30 days`` — generous by up to a day
    in any timezone, which errs on the side of the customer."""
    return dt.datetime.combine(
        event_date + PORTAL_GRACE + dt.timedelta(days=1),
        dt.time.min,
        tzinfo=dt.timezone.utc,
    )


def _current(
    db: Session, ctx: FunctionsCtx, kind: str, **target
) -> FnPublicToken | None:
    """The newest unrevoked token for the record (expired ones included: a
    re-mint revives the same link rather than issuing a second one)."""
    q = db.query(FnPublicToken).filter(
        FnPublicToken.organization_id == ctx.org_id,
        FnPublicToken.kind == kind,
        FnPublicToken.revoked_at.is_(None),
    )
    for column, value in target.items():
        q = q.filter(getattr(FnPublicToken, column) == value)
    return q.order_by(FnPublicToken.created_at.desc()).with_for_update().first()


def _issue(
    db: Session, ctx: FunctionsCtx, kind: str, expires_at: dt.datetime, **target
) -> str:
    row = _current(db, ctx, kind, **target)
    if row is not None:
        token = _derive(row.kind, row.id)
        if hmac.compare_digest(hash_token(token), row.token_hash):
            row.expires_at = (
                max(row.expires_at, expires_at) if row.expires_at else expires_at
            )
            flush(db)
            return token
        # The key changed since this link was issued: it still works (by
        # hash, until it expires) but can't be shown again — issue a new one
        # beside it, which is the record's link from now on.
    row_id = _uuid()  # chosen here: the id is part of the token
    token = _derive(kind, row_id)
    db.add(
        FnPublicToken(
            id=row_id,
            organization_id=ctx.org_id,
            kind=kind,
            token_hash=hash_token(token),
            expires_at=expires_at,
            created_by_user_id=ctx.user_id,
            **target,
        )
    )
    flush(db)
    return token


def mint_quote_link(db: Session, ctx: FunctionsCtx, enquiry_id: str) -> str:
    """The enquiry's quote-page URL (``{app_url}/q/<token>``) — the same one
    every time until it is revoked; each call pushes its expiry to 180 days
    from now."""
    require_live(ctx)
    require_staff(ctx)
    enquiry = enquiries_svc.get_enquiry(db, ctx, enquiry_id, for_update=True)
    token = _issue(db, ctx, "quote", _now() + QUOTE_TTL, enquiry_id=enquiry.id)
    return quote_url(token)


def mint_portal_link(
    db: Session, ctx: FunctionsCtx, event_id: str, emails: list[str] | None = None
) -> str:
    """The event's client-portal URL (``{app_url}/e/<token>``), stable until
    revoked, after allowing ``emails`` in (each verifies by a one-time code
    before seeing anything). Re-allowing a revoked email restores it."""
    require_live(ctx)
    require_staff(ctx)
    event = events_svc.get_event(db, ctx, event_id, for_update=True)
    if event.cancelled_at is not None:
        raise Conflict("This event is cancelled — reinstate it first.")
    for email in emails or []:
        allow_email(db, ctx, event, email, invited_by_user_id=ctx.user_id)
    token = _issue(
        db, ctx, "portal", _portal_expiry(event.event_date), event_id=event.id
    )
    return portal_url(token)


def allow_email(
    db: Session,
    ctx: FunctionsCtx,
    event: FnEvent,
    email: str,
    *,
    invited_by_user_id: str | None = None,
    invited_by_email: str | None = None,
) -> FnPortalAccess:
    """Add ``email`` to the event's allowed portal emails (idempotent,
    case-insensitive). The caller has already checked who may do this."""
    address = normalise_email(email)
    row = (
        db.query(FnPortalAccess)
        .filter(
            FnPortalAccess.organization_id == ctx.org_id,
            FnPortalAccess.event_id == event.id,
            func.lower(FnPortalAccess.email) == address,
        )
        .first()
    )
    if row is None:
        row = FnPortalAccess(
            organization_id=ctx.org_id,
            event_id=event.id,
            email=address,
            invited_by_user_id=invited_by_user_id,
            invited_by_email=invited_by_email,
        )
        db.add(row)
    elif row.revoked_at is not None:
        row.revoked_at = None
    flush(db)
    return row


# ---------------------------------------------------------------------------
# Resolving and revoking
# ---------------------------------------------------------------------------


def lookup(db: Session, token: str | None, kind: str) -> FnPublicToken | None:
    """The row for a token of ``kind`` WITHOUT validity checks — for
    recognising links in sent mail, where an expired link still means a quote
    went out. Public routes use ``resolve``."""
    if kind not in PUBLIC_TOKEN_KINDS or not _TOKEN.fullmatch(token or ""):
        return None
    return (
        db.query(FnPublicToken)
        .filter(
            FnPublicToken.token_hash == hash_token(token),
            FnPublicToken.kind == kind,
        )
        .first()
    )


def resolve(
    db: Session, token: str | None, kind: str, *, now: dt.datetime | None = None
) -> FnPublicToken | None:
    """The live token row, or None (unknown, wrong kind, revoked, expired)."""
    row = lookup(db, token, kind)
    if row is None or row.revoked_at is not None:
        return None
    now = now or _now()
    if row.expires_at is not None and row.expires_at <= now:
        if kind != "portal":
            return None
        event = (
            db.query(FnEvent.event_date)
            .filter(
                FnEvent.organization_id == row.organization_id,
                FnEvent.id == row.event_id,
            )
            .first()
        )
        if event is None or _portal_expiry(event[0]) <= now:
            return None
    return row


def touch(db: Session, row: FnPublicToken, *, now: dt.datetime | None = None) -> None:
    """Record use, at most hourly (a page refresh shouldn't be a write)."""
    now = now or _now()
    if row.last_used_at is None or now - row.last_used_at >= dt.timedelta(hours=1):
        row.last_used_at = now
        db.flush()


def revoke(
    db: Session,
    ctx: FunctionsCtx,
    *,
    token_id: str | None = None,
    enquiry_id: str | None = None,
    event_id: str | None = None,
) -> int:
    """End a link (by id) or every live link of an enquiry or event. Revoking
    a portal link also signs out everyone who came in through it. Returns how
    many links were revoked."""
    require_live(ctx)
    require_staff(ctx)
    q = db.query(FnPublicToken).filter(
        FnPublicToken.organization_id == ctx.org_id,
        FnPublicToken.revoked_at.is_(None),
    )
    if token_id:
        q = q.filter(FnPublicToken.id == token_id)
    elif enquiry_id:
        q = q.filter(FnPublicToken.enquiry_id == enquiry_id)
    elif event_id:
        q = q.filter(FnPublicToken.event_id == event_id)
    else:
        raise Invalid("Name a link, an enquiry or an event.")
    rows = q.all()
    now = _now()
    for row in rows:
        # Visibility: the caller must be able to see the record behind it.
        if row.enquiry_id:
            enquiries_svc.get_enquiry(db, ctx, row.enquiry_id)
        if row.event_id:
            events_svc.get_event(db, ctx, row.event_id)
        row.revoked_at = now
        if row.kind == "portal":
            db.query(FnPortalSession).filter(
                FnPortalSession.organization_id == ctx.org_id,
                FnPortalSession.event_id == row.event_id,
                FnPortalSession.revoked_at.is_(None),
            ).update({FnPortalSession.revoked_at: now}, synchronize_session=False)
    flush(db)
    return len(rows)


def links(
    db: Session,
    ctx: FunctionsCtx,
    *,
    enquiry_id: str | None = None,
    event_id: str | None = None,
) -> list[dict]:
    """The record's links for staff — expiry, last use, revocation, and the
    URL of each live one (``live_url``: re-derived; None once revoked or
    expired)."""
    if enquiry_id:
        enquiries_svc.get_enquiry(db, ctx, enquiry_id)
        flt = FnPublicToken.enquiry_id == enquiry_id
    elif event_id:
        events_svc.get_event(db, ctx, event_id)
        flt = FnPublicToken.event_id == event_id
    else:
        raise Invalid("Name an enquiry or an event.")
    rows = (
        db.query(FnPublicToken)
        .filter(FnPublicToken.organization_id == ctx.org_id, flt)
        .order_by(FnPublicToken.created_at.desc())
        .all()
    )
    now = _now()
    out = []
    for row in rows:
        out.append(
            {
                "id": row.id,
                "kind": row.kind,
                "url": live_url(row, now=now),
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                "last_used_at": row.last_used_at.isoformat()
                if row.last_used_at
                else None,
                "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return out
