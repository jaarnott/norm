"""The entertainers' personal links — portal, onboarding, band member and
declaration — and the one-time code that opens the bank and tax steps.

Minted on the shared derivation (``services/public_links``): a token is
``HMAC-SHA256(key("entertainers"), "<purpose>:<row id>")``, only its SHA-256
is stored, and ``public_links.resolve`` answers unknown, revoked, expired and
wrong-purpose alike with None. What differs from Functions' links:

* **14 days**, every purpose (plan §11.4). A portal link is stable — the
  same link in every email to the performer, its expiry pushed out by each
  mint — so a reminder never orphans the first email. An onboarding, band
  member or declaration link is **personal and single-use**: "Resend" revokes
  the old link and mints a new one, and a link that has submitted
  (``submitted_at``) never submits again.
* **Revoked** when the performer is deactivated, and the portal links when a
  performance is marked paid (0 of Orbit's 495 tokens were ever revoked).
* **The code** (plan §11.2, D14b): six digits from the CSPRNG, stored as an
  HMAC, live 10 minutes, burnt after 5 wrong tries, sent at most 3 times per
  15 minutes, and a daily failure cap of 5 kept in ``mcp_rate_limits`` — after
  which even the right code is refused for the day. The numbers and the
  counter are ``functions/portal.py``'s, copied not imported: that code is
  bound to ``FnPortalAccess`` and its events.
"""

from __future__ import annotations

import datetime as dt
import hmac
import secrets

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.entertainers_models import PUBLIC_TOKEN_PURPOSES, EntPublicToken
from app.db.models import _now, _uuid
from app.services import public_links
from app.services.finance_core.errors import Invalid, flush

PURPOSE_KEY = "entertainers"
TTL = dt.timedelta(days=14)
#: The purposes whose link lives at /join/<token>; the portal is /p/<token>.
JOIN_PURPOSES = ("onboarding", "band_member", "declaration")
PATHS = {
    "portal": "p",
    "onboarding": "join",
    "band_member": "join",
    "declaration": "join",
}

OTP_TTL = dt.timedelta(minutes=10)
OTP_MAX_ATTEMPTS = 5
OTP_SENDS_PER_WINDOW = 3
OTP_WINDOW = dt.timedelta(minutes=15)
#: Wrong codes one link may enter in a (UTC) day. The sixth is refused even
#: when it is right (DESIGN-CONTRACT §10) — a link-holder guessing all day is
#: stopped, and the performer asks finance for a fresh link.
FAILS_PER_DAY = 5
#: How long a verified code keeps the bank and tax steps open.
VERIFIED_TTL = dt.timedelta(minutes=60)


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def hash_token(token: str) -> str:
    return public_links.hash_token(token)


def _derive(purpose: str, row_id: str) -> str:
    return public_links.derive(purpose, row_id, purpose=PURPOSE_KEY)


def url_for(purpose: str, token: str) -> str:
    return public_links.link_url(f"{PATHS[purpose]}/{token}")


def live_url(row: EntPublicToken, *, now: dt.datetime | None = None) -> str | None:
    """The URL of a live link, re-derived from its row — None once revoked,
    expired, submitted, or issued under an earlier secret."""
    if row.revoked_at is not None or row.submitted_at is not None:
        return None
    if row.expires_at <= (now or _now()):
        return None
    token = _derive(row.purpose, row.id)
    if not hmac.compare_digest(hash_token(token), row.token_hash):
        return None
    return url_for(row.purpose, token)


# ---------------------------------------------------------------------------
# Minting and revoking
# ---------------------------------------------------------------------------


def _target(purpose: str, performer_id: str | None, band_member_id: str | None) -> dict:
    if purpose not in PUBLIC_TOKEN_PURPOSES:
        raise Invalid(f"Unknown link purpose {purpose!r}.")
    if purpose == "band_member":
        if not band_member_id:
            raise Invalid("A band member link needs the member.")
        return {"band_member_id": band_member_id, "performer_id": performer_id}
    if not performer_id:
        raise Invalid("A link needs the performer.")
    return {"performer_id": performer_id}


def _live_rows(db: Session, org_id: str, purpose: str, target: dict):
    q = db.query(EntPublicToken).filter(
        EntPublicToken.organization_id == org_id,
        EntPublicToken.purpose == purpose,
        EntPublicToken.revoked_at.is_(None),
    )
    if purpose == "band_member":
        q = q.filter(EntPublicToken.band_member_id == target["band_member_id"])
    else:
        q = q.filter(EntPublicToken.performer_id == target["performer_id"])
    return q.order_by(EntPublicToken.created_at.desc()).with_for_update().all()


def mint(
    db: Session,
    *,
    org_id: str,
    purpose: str,
    performer_id: str | None = None,
    band_member_id: str | None = None,
    created_by: str | None = None,
    now: dt.datetime | None = None,
) -> tuple[EntPublicToken, str, str]:
    """``(row, token, url)``. A portal link is re-issued stable (expiry
    pushed to 14 days from now); a join link revokes the earlier live ones
    and is new — the old email's link stops working, which is the point of
    "Resend"."""
    now = now or _now()
    target = _target(purpose, performer_id, band_member_id)
    rows = _live_rows(db, org_id, purpose, target)
    if purpose == "portal":
        for row in rows:
            token = _derive(purpose, row.id)
            if row.expires_at > now and hmac.compare_digest(
                hash_token(token), row.token_hash
            ):
                row.expires_at = now + TTL
                flush(db)
                return row, token, url_for(purpose, token)
    for row in rows:
        row.revoked_at = now
    row_id = _uuid()
    token = _derive(purpose, row_id)
    row = EntPublicToken(
        id=row_id,
        organization_id=org_id,
        purpose=purpose,
        token_hash=hash_token(token),
        expires_at=now + TTL,
        created_by=created_by,
        **target,
    )
    db.add(row)
    flush(db)
    return row, token, url_for(purpose, token)


def current_url(
    db: Session,
    *,
    org_id: str,
    purpose: str,
    performer_id: str | None = None,
    band_member_id: str | None = None,
    now: dt.datetime | None = None,
) -> str | None:
    """The URL of the live link already issued (for a reminder, which must
    carry the link the performer already holds), or None when there is none
    that can be shown again."""
    now = now or _now()
    target = _target(purpose, performer_id, band_member_id)
    for row in _live_rows(db, org_id, purpose, target):
        url = live_url(row, now=now)
        if url:
            return url
    return None


def revoke(db: Session, row: EntPublicToken, *, now: dt.datetime | None = None) -> None:
    if row.revoked_at is None:
        row.revoked_at = now or _now()
        db.flush()


def revoke_for_performer(
    db: Session,
    org_id: str,
    performer_id: str,
    *,
    purposes: tuple[str, ...] | None = None,
    now: dt.datetime | None = None,
) -> int:
    """End a performer's live links — every purpose (deactivation) or the
    ones named (``("portal",)`` after mark-paid)."""
    now = now or _now()
    q = db.query(EntPublicToken).filter(
        EntPublicToken.organization_id == org_id,
        EntPublicToken.performer_id == performer_id,
        EntPublicToken.revoked_at.is_(None),
    )
    if purposes:
        q = q.filter(EntPublicToken.purpose.in_(purposes))
    rows = q.all()
    for row in rows:
        row.revoked_at = now
    db.flush()
    return len(rows)


# ---------------------------------------------------------------------------
# Resolving
# ---------------------------------------------------------------------------


def resolve(
    db: Session,
    token: str | None,
    *,
    purposes: tuple[str, ...],
    now: dt.datetime | None = None,
) -> EntPublicToken | None:
    """The live row for ``token`` if its purpose is one of ``purposes``, else
    None. A submitted join link still resolves (the page shows "received");
    the routes refuse its writes."""
    row = public_links.resolve(db, EntPublicToken, token, now=now)
    if row is None or row.purpose not in purposes:
        return None
    return row


def touch(db: Session, row: EntPublicToken, *, now: dt.datetime | None = None) -> None:
    """Record use, at most hourly (the Emails tab's "link opened")."""
    now = now or _now()
    if row.last_used_at is None or now - row.last_used_at >= dt.timedelta(hours=1):
        row.last_used_at = now
        db.flush()


# ---------------------------------------------------------------------------
# The one-time code
# ---------------------------------------------------------------------------


def _otp_key() -> bytes:
    return hmac.new(
        settings.JWT_SECRET.encode(), b"norm-entertainers/otp/v1", "sha256"
    ).digest()


def _otp_hash(row_id: str, code: str) -> str:
    return hmac.new(_otp_key(), f"{row_id}:{code}".encode(), "sha256").hexdigest()


def request_code(
    db: Session, row: EntPublicToken, *, now: dt.datetime | None = None
) -> str | None:
    """A fresh code for the link — or None when the send window is used up
    (three per fifteen minutes), which the caller answers exactly as it
    answers success. The caller emails it after committing."""
    now = now or _now()
    if (
        row.otp_window_started_at is None
        or now - row.otp_window_started_at >= OTP_WINDOW
    ):
        row.otp_window_started_at = now
        row.otp_sends_in_window = 0
    if row.otp_sends_in_window >= OTP_SENDS_PER_WINDOW:
        db.flush()
        return None
    code = f"{secrets.randbelow(1_000_000):06d}"
    row.otp_hash = _otp_hash(row.id, code)
    row.otp_expires_at = now + OTP_TTL
    row.otp_attempts = 0
    row.otp_sends_in_window += 1
    db.flush()
    return code


def _failure_key(row_id: str) -> str:
    return f"entpub:code-fails:{row_id}"


def _day_end(now: dt.datetime) -> dt.datetime:
    return dt.datetime.combine(
        now.astimezone(dt.timezone.utc).date() + dt.timedelta(days=1),
        dt.time.min,
        tzinfo=dt.timezone.utc,
    )


def _count_failure(db: Session, row: EntPublicToken, now: dt.datetime) -> int:
    """Count a wrong code against today's allowance. Lives in
    ``mcp_rate_limits`` keyed to the END of the day so the hourly GC keeps it
    all day (``functions/portal.py``'s trick)."""
    found = db.execute(
        text(
            "INSERT INTO mcp_rate_limits (id, bucket_key, window_start, count) "
            "VALUES (gen_random_uuid()::text, :key, :until, 1) "
            "ON CONFLICT (bucket_key, window_start) "
            "DO UPDATE SET count = mcp_rate_limits.count + 1 "
            "RETURNING count"
        ),
        {"key": _failure_key(row.id), "until": _day_end(now)},
    ).first()
    return found[0] if found else 1


def failures_today(db: Session, row_id: str, now: dt.datetime | None = None) -> int:
    found = db.execute(
        text(
            "SELECT count FROM mcp_rate_limits "
            "WHERE bucket_key = :key AND window_start = :until"
        ),
        {"key": _failure_key(row_id), "until": _day_end(now or _now())},
    ).first()
    return found[0] if found else 0


def verify_code(
    db: Session, row: EntPublicToken, code: str, *, now: dt.datetime | None = None
) -> bool:
    """True for a right code inside its life, with attempts left and the day's
    allowance not spent. A wrong code counts (five burn the code); the sixth
    wrong code of the day locks the link until tomorrow — the caller must
    COMMIT before answering so a refused attempt still counts."""
    now = now or _now()
    if failures_today(db, row.id, now) >= FAILS_PER_DAY:
        return False
    if (
        not row.otp_hash
        or row.otp_expires_at is None
        or row.otp_expires_at <= now
        or row.otp_attempts >= OTP_MAX_ATTEMPTS
    ):
        return False
    given = (code or "").strip()
    if not hmac.compare_digest(_otp_hash(row.id, given), row.otp_hash):
        row.otp_attempts += 1
        if row.otp_attempts >= OTP_MAX_ATTEMPTS:
            row.otp_hash = None
            row.otp_expires_at = None
        _count_failure(db, row, now)
        db.flush()
        return False
    row.otp_hash = None
    row.otp_expires_at = None
    row.otp_attempts = 0
    row.otp_verified_at = now
    db.flush()
    return True


def code_verified(row: EntPublicToken, *, now: dt.datetime | None = None) -> bool:
    """Whether a code was verified on this link recently enough to open the
    bank and tax steps."""
    if row.otp_verified_at is None:
        return False
    return (now or _now()) - row.otp_verified_at <= VERIFIED_TTL
