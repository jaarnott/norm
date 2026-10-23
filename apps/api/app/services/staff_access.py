"""Venue staff signing in with an emailed code, and leaving
(docs/plans/staff-access.md, sections 6 and 11.5).

Nobody invites venue staff: being an active person in the organisation's
staff directory (``StaffMember``, copied from Loaded or added by hand) in an
organisation that has switched staff access on is the approval. The flow:

1. ``request_code`` — a six-digit code for an eligible email, kept as an HMAC
   in ``staff_login_codes`` (one row per address, rewritten each send). It
   answers None for an address that is not eligible, is throttled or is
   locked, and the router answers exactly as it answers success, so nobody
   can test which emails are on the list.
2. ``verify_code`` — five tries, ten minutes; the fifth wrong try burns the
   code, and ten wrong codes in a day lock the address until the next day.
   A wrong try is written in the caller's transaction, which must COMMIT even
   though it refuses — a rolled-back attempt would be a free guess.
3. ``claim_account`` — the Norm account for the email (made with no password
   on the first sign-in, linked when one exists), a membership in every
   organisation where the email is an active staff member, and access to the
   venues the directory lists. An existing membership keeps its role.

``revoke`` is the other direction: the directory says someone has gone, so
the membership and venue access the directory GAVE them in that organisation
go (``StaffMember.granted_membership`` says whether it did — an owner or
manager who was already a member before they appeared on the roster keeps
their membership), and an account Norm made for them (no password, not a
tablet) with no membership left anywhere is deactivated, which ends their
session at its next request.

A tablet PIN never resolves an account by email (``routers/tablet.py``): a
PIN proves nothing about the address a manager typed, so the person gets a
placeholder account (``staff-<id>@staff.norm.local``) until they sign in with
a code, when ``claim_account`` adopts that placeholder as their account.

Timing: ``request_code`` answers None for an unknown address before any
write, while a listed address costs a code row and a flush — a few
milliseconds of database work, nothing in the response. The per-address and
per-IP throttles keep that from being sampled; it is accepted, not hidden.

The code mechanics mirror ``services/functions/portal.py``, which does the
same for Functions customers.
"""

from __future__ import annotations

import datetime as dt
import hmac
import logging
import secrets
from dataclasses import dataclass

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.config import settings
from app.db.models import (
    Organization,
    OrganizationMembership,
    StaffLoginCode,
    StaffMember,
    User,
    UserVenueAccess,
    Venue,
)
from app.services import active_org
from app.services.functions import addresses
from app.services.org_admin import system_role
from app.services.role_access import role_for_loaded_roles
from app.services.staff_directory import (  # noqa: F401 — re-exported
    is_placeholder,
    placeholder_email,
    staff_record_for,
)

logger = logging.getLogger(__name__)

CODE_TTL = dt.timedelta(minutes=10)
CODE_MAX_ATTEMPTS = 5
#: Sends allowed per address in one window (the router also counts these in
#: ``mcp_rate_limits``; this one holds for any other caller of request_code).
CODE_SENDS_PER_WINDOW = 3
CODE_SEND_WINDOW = dt.timedelta(minutes=15)
#: Wrong codes in a UTC day before the address is locked until the next day.
FAILS_PER_DAY = 10

#: The role a claimed staff member gets when nothing else chooses one.
DEFAULT_ROLE_NAME = "team_member"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def normalise_email(email: str | None) -> str | None:
    """The address trimmed and lower-cased, or None when it isn't one."""
    return addresses.normalise(email)


# ── Codes ───────────────────────────────────────────────────────────────────


def _code_key() -> bytes:
    return hmac.new(
        settings.JWT_SECRET.encode(), b"norm-staff/login-code/v1", "sha256"
    ).digest()


def _code_hash(email: str, code: str) -> str:
    return hmac.new(_code_key(), f"{email}:{code}".encode(), "sha256").hexdigest()


def _failure_key(email: str) -> str:
    return f"staff-code-fails:{email}"


def _day_end(now: dt.datetime) -> dt.datetime:
    return dt.datetime.combine(
        now.astimezone(dt.timezone.utc).date() + dt.timedelta(days=1),
        dt.time.min,
        tzinfo=dt.timezone.utc,
    )


def _count_failure(db: Session, email: str, now: dt.datetime) -> int:
    """Count a wrong code against today's allowance. The counter lives in
    ``mcp_rate_limits`` keyed to the END of the day, so the hourly GC keeps
    it for the whole day (the same trick as the Functions portal)."""
    row = db.execute(
        text(
            "INSERT INTO mcp_rate_limits (id, bucket_key, window_start, count) "
            "VALUES (gen_random_uuid()::text, :key, :until, 1) "
            "ON CONFLICT (bucket_key, window_start) "
            "DO UPDATE SET count = mcp_rate_limits.count + 1 "
            "RETURNING count"
        ),
        {"key": _failure_key(email), "until": _day_end(now)},
    ).first()
    return row[0] if row else 1


def failures_today(db: Session, email: str, now: dt.datetime | None = None) -> int:
    row = db.execute(
        text(
            "SELECT count FROM mcp_rate_limits "
            "WHERE bucket_key = :key AND window_start = :until"
        ),
        {"key": _failure_key(email), "until": _day_end(now or _now())},
    ).first()
    return row[0] if row else 0


def is_locked(db: Session, email: str, now: dt.datetime | None = None) -> bool:
    """Ten wrong codes today: no code is sent or accepted until tomorrow."""
    return failures_today(db, email, now) >= FAILS_PER_DAY


def eligible_staff(db: Session, email: str) -> list[StaffMember]:
    """The active staff records for ``email`` in organisations that have
    staff access switched on (and are themselves active), oldest first."""
    return (
        db.query(StaffMember)
        .join(Organization, Organization.id == StaffMember.organization_id)
        .filter(
            StaffMember.email == email,
            StaffMember.is_active.is_(True),
            Organization.staff_access_enabled.is_(True),
            Organization.is_active.is_(True),
        )
        .order_by(StaffMember.created_at.asc().nulls_last(), StaffMember.id)
        .all()
    )


@dataclass(frozen=True)
class IssuedCode:
    email: str
    code: str
    #: The person's name from the directory, for the email's greeting.
    name: str


def _code_row(db: Session, email: str) -> StaffLoginCode | None:
    return (
        db.query(StaffLoginCode)
        .filter(StaffLoginCode.email == email)
        .with_for_update()
        .first()
    )


def request_code(
    db: Session, email: str | None, *, now: dt.datetime | None = None
) -> IssuedCode | None:
    """A fresh code for an eligible email, for the caller to send once it has
    committed — or None: not an address, not on any list, locked for the day
    or throttled. The caller must answer None exactly as it answers a code."""
    address = normalise_email(email)
    if address is None:
        return None
    now = now or _now()
    staff = eligible_staff(db, address)
    if not staff or is_locked(db, address, now):
        return None
    row = _code_row(db, address)
    if row is None:
        row = StaffLoginCode(email=address)
        db.add(row)
    if row.window_started_at is None or now - row.window_started_at >= CODE_SEND_WINDOW:
        row.window_started_at = now
        row.sends_in_window = 0
    if (row.sends_in_window or 0) >= CODE_SENDS_PER_WINDOW:
        db.flush()
        return None
    code = f"{secrets.randbelow(1_000_000):06d}"
    row.code_hash = _code_hash(address, code)
    row.expires_at = now + CODE_TTL
    row.attempts = 0
    row.sends_in_window = (row.sends_in_window or 0) + 1
    db.flush()
    return IssuedCode(email=address, code=code, name=staff[0].name)


def verify_code(
    db: Session, email: str | None, code: str, *, now: dt.datetime | None = None
) -> bool:
    """True for the right code, which is then spent. A wrong code counts
    against it (five and it is burnt; ten in a day and the address is locked),
    so the caller must COMMIT before answering."""
    address = normalise_email(email)
    if address is None:
        return False
    now = now or _now()
    row = _code_row(db, address)
    if (
        row is None
        or not row.code_hash
        or row.expires_at is None
        or row.expires_at <= now
        or (row.attempts or 0) >= CODE_MAX_ATTEMPTS
        or is_locked(db, address, now)
    ):
        return False
    given = (code or "").strip()
    if not hmac.compare_digest(_code_hash(address, given), row.code_hash):
        row.attempts = (row.attempts or 0) + 1
        if row.attempts >= CODE_MAX_ATTEMPTS:
            row.code_hash = None
            row.expires_at = None
        if _count_failure(db, address, now) >= FAILS_PER_DAY:
            row.code_hash = None
            row.expires_at = None
            logger.warning(
                "staff sign-in for %s locked after %d wrong codes today",
                address,
                FAILS_PER_DAY,
            )
        db.flush()
        return False
    row.code_hash = None
    row.expires_at = None
    row.attempts = 0
    db.flush()
    return True


# ── Accounts ────────────────────────────────────────────────────────────────


def _user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(func.lower(User.email) == email).first()


def _new_staff_user(db: Session, email: str, name: str) -> User:
    """An account with no password: it holds a random hash because
    ``hashed_password`` is NOT NULL, and ``password_set`` says so."""
    user = User(
        email=email,
        hashed_password=hash_password(secrets.token_urlsafe(24)),
        full_name=name or email.split("@")[0],
        role="user",
        is_active=True,
        password_set=False,
    )
    db.add(user)
    db.flush()
    return user


def role_for_staff(db: Session, staff: StaffMember):
    """The role a new membership gets: chosen by hand on the staff record,
    else the role the Loaded role names map to, else the system Team Member."""
    if staff.role_id and staff.role is not None:
        return staff.role
    mapped = role_for_loaded_roles(db, staff.organization_id, staff.loaded_roles or [])
    return mapped or system_role(db, DEFAULT_ROLE_NAME)


def ensure_membership(
    db: Session, user: User, staff: StaffMember, *, now: dt.datetime | None = None
) -> OrganizationMembership:
    """The user's membership in the staff record's organisation, made with
    the mapped role when missing — an existing membership's role is never
    changed — plus venue access that is the union of what they have and the
    venues the directory lists. Links the staff record to the user."""
    now = now or _now()
    org_id = staff.organization_id
    membership = next(
        (
            m
            for m in active_org.memberships_for(db, user.id)
            if m.organization_id == org_id
        ),
        None,
    )
    if membership is None:
        role = role_for_staff(db, staff)
        membership = OrganizationMembership(
            user_id=user.id,
            organization_id=org_id,
            role="owner" if role is not None and role.name == "owner" else "member",
            role_id=role.id if role is not None else None,
        )
        db.add(membership)
        # The directory let them in, so the directory may show them out.
        staff.granted_membership = True
        db.flush()

    wanted = [str(v) for v in (staff.venue_ids or []) if v]
    if wanted:
        org_venue_ids = {
            v[0]
            for v in db.query(Venue.id).filter(
                Venue.organization_id == org_id, Venue.id.in_(wanted)
            )
        }
        held = {
            r[0]
            for r in db.query(UserVenueAccess.venue_id).filter(
                UserVenueAccess.user_id == user.id
            )
        }
        for vid in dict.fromkeys(wanted):
            if vid in org_venue_ids and vid not in held:
                db.add(UserVenueAccess(user_id=user.id, venue_id=vid))

    staff.user_id = user.id
    staff.last_seen_at = now
    db.flush()
    return membership


def claim_account(
    db: Session, email: str | None, *, now: dt.datetime | None = None
) -> User | None:
    """The account behind a verified email: the User with that address, or a
    new one with no password, made a member of every organisation where the
    email is an active staff member. None when no organisation has them (the
    directory changed since the code went out). Flushed, not committed."""
    address = normalise_email(email)
    if address is None:
        return None
    now = now or _now()
    staff_rows = eligible_staff(db, address)
    if not staff_rows:
        return None
    user = _user_by_email(db, address)
    if user is None:
        # A tablet PIN may already have made them a placeholder account; the
        # verified email adopts it, so what they did on the tablet stays theirs.
        user = _placeholder_of(db, staff_rows)
        if user is not None:
            user.email = address
            user.is_active = True
            if not user.full_name:
                user.full_name = staff_rows[0].name
    if user is None:
        user = _new_staff_user(db, address, staff_rows[0].name)
    elif not user.is_active:
        # The directory says they work here again (a leaver re-added in
        # Loaded, or an invite they never got round to accepting).
        user.is_active = True
    for staff in staff_rows:
        ensure_membership(db, user, staff, now=now)
    db.flush()
    return user


def _placeholder_of(db: Session, staff_rows: list[StaffMember]) -> User | None:
    """The placeholder account a tablet PIN linked to one of these rows."""
    for staff in staff_rows:
        if staff.user_id:
            user = db.query(User).filter(User.id == staff.user_id).first()
            if is_placeholder(user):
                return user
    return None


def revoke(db: Session, staff: StaffMember) -> None:
    """The directory says this person has gone: take away the membership and
    venue access the directory gave them in that organisation, and deactivate
    an account Norm made for them (no password, not a tablet) once no
    membership is left anywhere. Their records stay. Flushed, not committed.

    Only what the claim granted goes. Someone who was a member before they
    appeared on the roster — an owner or manager who also works shifts — was
    not let in by the directory (``granted_membership`` is false), so
    dropping off Loaded's list, or a manager switching their staff row off,
    leaves their membership and venues alone."""
    user_id = staff.user_id
    if not user_id:
        return
    if not staff.granted_membership:
        return
    org_id = staff.organization_id
    still_here = (
        db.query(StaffMember.id)
        .filter(
            StaffMember.organization_id == org_id,
            StaffMember.user_id == user_id,
            StaffMember.id != staff.id,
            StaffMember.is_active.is_(True),
        )
        .first()
    )
    if still_here is not None:
        # Another active staff record in this organisation (a manual row
        # beside the Loaded one) keeps them in.
        return
    db.query(OrganizationMembership).filter(
        OrganizationMembership.organization_id == org_id,
        OrganizationMembership.user_id == user_id,
    ).delete(synchronize_session=False)
    staff.granted_membership = False
    org_venue_ids = db.query(Venue.id).filter(Venue.organization_id == org_id)
    db.query(UserVenueAccess).filter(
        UserVenueAccess.user_id == user_id,
        UserVenueAccess.venue_id.in_(org_venue_ids),
    ).delete(synchronize_session=False)
    db.flush()
    user = db.query(User).filter(User.id == user_id).first()
    if (
        user is not None
        and not user.password_set
        and not user.is_device
        and not active_org.memberships_for(db, user_id)
    ):
        user.is_active = False
    db.flush()
