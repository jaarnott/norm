"""The client portal (``/e/<token>``): a customer's own event, behind a
one-time code.

Link -> an allowed email -> a code by email -> a session -> the event's
sections, autosaved, confirmed (and re-confirmed) by the client, shareable
with a colleague, printable. Orbit's portal could be read and written with the
public anon key; here every step is checked on the server:

* **The link alone shows nothing personal** — only the venue's name and brand,
  until a code is verified.
* **Codes** are 6 digits from the CSPRNG, stored as an HMAC (never the code),
  live 10 minutes, burn after 5 wrong tries, and go out at most 3 times per
  email per 15 minutes (the router also caps sends per link). "Send me a
  code" gets the same answer for every address, so the form can't be used to
  learn who is invited.
* **Sessions** are a 256-bit random token in an httpOnly cookie scoped to this
  link's API path; only its SHA-256 is stored. They last 30 days and end with
  the link, or with the email's access.
* **Reads and writes go through F1's services with a ``for_client`` ctx**,
  which reaches only this event, its lists and its menus, and enforces the
  client field limits (``events.CLIENT_FIELDS``) — a wrong id is NotFound, a
  staff field Forbidden. This module narrows further to what the venue shows
  for this booking type (``venue_profile.enabled_fields_for``).

Staff are emailed when the client confirms, and when the client changes a
confirmed event (once per editing session — autosave would otherwise send one
per keystroke pause).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import hmac
import secrets
from dataclasses import dataclass

from sqlalchemy import Boolean, Integer, String, Text, Time, exists, func, text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.functions_models import (
    EVENT_SECTIONS,
    FnContact,
    FnContactEmail,
    FnEnquiry,
    FnEvent,
    FnEventChange,
    FnPortalAccess,
    FnPortalSession,
    FnPublicToken,
    FnVenueArea,
    FnVenueFunctionSettings,
)
from app.db.models import Organization, _now
from app.services.functions import events as events_svc
from app.services.functions import menus as menus_svc
from app.services.functions import min_spends as min_spends_svc
from app.services.functions import notify
from app.services.functions import public_tokens
from app.services.functions._rows import check_version, jsonable
from app.services.functions.company import format_money, get_company_settings
from app.services.functions.contacts import normalise_email
from app.services.functions.ctx import (
    FunctionsCtx,
    for_client,
    require_live,
    require_staff,
)
from app.services.functions.errors import (
    Conflict,
    Forbidden,
    FunctionsError,
    Invalid,
    NotFound,
    flush,
)
from app.services.functions.quote_page import venue_summary
from app.services.functions.venue_profile import enabled_fields_for

logger = logging.getLogger(__name__)

OTP_TTL = dt.timedelta(minutes=10)
OTP_MAX_ATTEMPTS = 5
OTP_SENDS_PER_WINDOW = 3
OTP_WINDOW = dt.timedelta(minutes=15)
#: Wrong codes one email may enter in a (UTC) day before it is locked out
#: until staff allow it again. Per-code and per-window limits alone still let
#: someone holding the link guess all day — and each new window emails the
#: client another code. A lockout ends both; the cost (a link-holder can lock
#: the real client out) is one click for staff to undo.
FAILS_PER_DAY = 10
SESSION_TTL = dt.timedelta(days=30)
SESSION_COOKIE = "norm_portal"
#: People who may open one event's portal (staff-allowed plus invited).
MAX_PEOPLE = 25
#: A client edit after confirmation emails staff only if the client made no
#: other such edit in this long — one email per editing session.
EDIT_EMAIL_QUIET = dt.timedelta(minutes=30)
MAX_TEXT = 5000

#: Event fields shown to every signed-in client (no venue switch governs them).
ALWAYS_FIELDS = (
    "name",
    "booking_type",
    "event_date",
    "start_time",
    "end_time",
    "ends_next_day",
    "guests",
    "special_instructions",
)
#: Venue x booking-type switches (venue_profile.FIELD_CATALOGUE keys) -> the
#: event fields each one shows. A field shown AND in events.CLIENT_FIELDS is
#: editable; the rest (payment, deposit, bar-tab amount) are read-only.
FIELD_GATES = {
    "details.payment": ("payment_method",),
    "details.deposit": ("deposit_cents", "deposit_status"),
    "details.notes": ("notes",),
    "beverages.bar_tab": ("bar_tab_cents",),
    "beverages.authorised_people": ("bar_tab_authorised_people",),
    "beverages.restrictions": ("bar_tab_restrictions",),
    "beverages.arrival_drinks": ("arrival_drinks",),
    "beverages.table_ordering": ("table_ordering", "table_ordering_limit_cents"),
    "beverages.notes": ("bar_tab_notes",),
    "food.timing_preference": ("food_timing_type", "food_time", "food_end_time"),
    "food.allergies": ("allergies",),
    "food.notes": ("food_notes",),
    "timings.preference": ("timing_preference",),
    "timings.notes": ("timing_notes",),
    "timings.arrival_time": ("arrival_time",),
    "timings.seated_time": ("seated_time",),
}
LIST_GATES = {
    "timings": "timings.schedule",
    "security": "details.security",
    "entertainment": "details.entertainment",
    "av": "details.av",
}
KIND_GATES = {
    "entertainment": {
        "dj": "details.entertainment.dj",
        "playlist": "details.entertainment.playlist",
    },
    "av": {
        "in_house_mic": "details.av.in_house_mic",
        "in_house_tv": "details.av.in_house_tv",
        "external_av": "details.av.external_av",
    },
}
MENUS_GATE = "food.preorder_menus"
AREA_GATE = "details.area"
MIN_SPEND_GATE = "details.min_spend"
#: What a client may correct on their contact record (email is how they and
#: their colleagues sign in, so it stays with staff).
CONTACT_FIELDS = ("name", "phone", "company")


class SessionRequired(FunctionsError):
    """No valid portal session for this link (sign in with a code)."""

    status_code = 401
    code = "session_required"


_GONE = "This link isn't available."


# ---------------------------------------------------------------------------
# Link, codes and sessions
# ---------------------------------------------------------------------------


def resolve_portal(db: Session, token: str) -> tuple[FnPublicToken, FnEvent]:
    """The portal token and its event — NotFound for every way it can fail
    (unknown, revoked, expired, organisation not live)."""
    row = public_tokens.resolve(db, token, "portal")
    if row is None:
        raise NotFound(_GONE)
    mode = (
        db.query(Organization.functions_mode)
        .filter(Organization.id == row.organization_id)
        .scalar()
    )
    event = (
        db.query(FnEvent)
        .filter(
            FnEvent.organization_id == row.organization_id, FnEvent.id == row.event_id
        )
        .first()
    )
    if mode != "live" or event is None or event.cancelled_at is not None:
        # A cancelled event's portal closes (reinstating reopens it).
        raise NotFound(_GONE)
    return row, event


def _otp_key() -> bytes:
    return hmac.new(
        settings.JWT_SECRET.encode(), b"norm-functions/portal-otp/v1", "sha256"
    ).digest()


def _otp_hash(access_id: str, code: str) -> str:
    return hmac.new(_otp_key(), f"{access_id}:{code}".encode(), "sha256").hexdigest()


def _session_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _access(db: Session, event: FnEvent, email: str) -> FnPortalAccess | None:
    return (
        db.query(FnPortalAccess)
        .filter(
            FnPortalAccess.organization_id == event.organization_id,
            FnPortalAccess.event_id == event.id,
            func.lower(FnPortalAccess.email) == email,
            FnPortalAccess.revoked_at.is_(None),
        )
        .with_for_update()
        .first()
    )


def request_code(
    db: Session, event: FnEvent, email: str, *, now: dt.datetime | None = None
) -> tuple[str, str] | None:
    """Issue a code for an allowed email: ``(address, code)`` for the caller
    to send once committed, or None — not allowed, throttled or malformed,
    which the caller must answer exactly as it answers success."""
    try:
        address = normalise_email(email)
    except Invalid:
        return None
    access = _access(db, event, address)
    if access is None:
        return None
    now = now or _now()
    if (
        access.otp_window_started_at is None
        or now - access.otp_window_started_at >= OTP_WINDOW
    ):
        access.otp_window_started_at = now
        access.otp_sends_in_window = 0
    if access.otp_sends_in_window >= OTP_SENDS_PER_WINDOW:
        db.flush()
        return None
    code = f"{secrets.randbelow(1_000_000):06d}"
    access.otp_hash = _otp_hash(access.id, code)
    access.otp_expires_at = now + OTP_TTL
    access.otp_attempts = 0
    access.otp_sends_in_window += 1
    db.flush()
    return address, code


def _failure_key(access_id: str) -> str:
    return f"fnpub:portal-fails:{access_id}"


def _day_end(now: dt.datetime) -> dt.datetime:
    return dt.datetime.combine(
        now.astimezone(dt.timezone.utc).date() + dt.timedelta(days=1),
        dt.time.min,
        tzinfo=dt.timezone.utc,
    )


def _count_failure(db: Session, access: FnPortalAccess, now: dt.datetime) -> int:
    """Count a wrong code against today's allowance (written in the caller's
    transaction, which commits even though it refuses). The counter lives in
    ``mcp_rate_limits`` keyed to the END of the day, so the hourly GC keeps it
    for the whole day."""
    row = db.execute(
        text(
            "INSERT INTO mcp_rate_limits (id, bucket_key, window_start, count) "
            "VALUES (gen_random_uuid()::text, :key, :until, 1) "
            "ON CONFLICT (bucket_key, window_start) "
            "DO UPDATE SET count = mcp_rate_limits.count + 1 "
            "RETURNING count"
        ),
        {"key": _failure_key(access.id), "until": _day_end(now)},
    ).first()
    return row[0] if row else 1


def failures_today(db: Session, access_id: str, now: dt.datetime | None = None) -> int:
    row = db.execute(
        text(
            "SELECT count FROM mcp_rate_limits "
            "WHERE bucket_key = :key AND window_start = :until"
        ),
        {"key": _failure_key(access_id), "until": _day_end(now or _now())},
    ).first()
    return row[0] if row else 0


def _end_sessions(db: Session, event: FnEvent, email: str, now: dt.datetime) -> None:
    db.query(FnPortalSession).filter(
        FnPortalSession.organization_id == event.organization_id,
        FnPortalSession.event_id == event.id,
        func.lower(FnPortalSession.email) == email.lower(),
        FnPortalSession.revoked_at.is_(None),
    ).update({FnPortalSession.revoked_at: now}, synchronize_session=False)


def verify_code(
    db: Session,
    event: FnEvent,
    email: str,
    code: str,
    *,
    now: dt.datetime | None = None,
) -> tuple[FnPortalSession, str] | None:
    """``(session, raw_session_token)`` for a right code, else None. A wrong
    code counts against it (5 and it is burnt), so the caller must COMMIT
    before answering — a rolled-back attempt would be a free guess."""
    try:
        address = normalise_email(email)
    except Invalid:
        return None
    access = _access(db, event, address)
    now = now or _now()
    if (
        access is None
        or not access.otp_hash
        or access.otp_expires_at is None
        or access.otp_expires_at <= now
        or access.otp_attempts >= OTP_MAX_ATTEMPTS
    ):
        return None
    given = (code or "").strip()
    if not hmac.compare_digest(_otp_hash(access.id, given), access.otp_hash):
        access.otp_attempts += 1
        if access.otp_attempts >= OTP_MAX_ATTEMPTS:
            access.otp_hash = None
            access.otp_expires_at = None
        if _count_failure(db, access, now) >= FAILS_PER_DAY:
            # Locked: the same state as staff removing the email, so the
            # code form goes quiet (no more codes emailed) and only staff
            # allowing it again reopens it.
            access.revoked_at = now
            access.otp_hash = None
            access.otp_expires_at = None
            _end_sessions(db, event, access.email, now)
            logger.warning(
                "portal access %s locked after %d wrong codes today",
                access.id,
                FAILS_PER_DAY,
            )
        db.flush()
        return None
    access.otp_hash = None
    access.otp_expires_at = None
    access.otp_attempts = 0
    access.verified_at = now
    raw = secrets.token_urlsafe(32)
    session = FnPortalSession(
        organization_id=event.organization_id,
        session_hash=_session_hash(raw),
        event_id=event.id,
        email=address,
        expires_at=now + SESSION_TTL,
        last_seen_at=now,
    )
    db.add(session)
    flush(db)
    return session, raw


def session_for(
    db: Session, event: FnEvent, raw: str | None, *, now: dt.datetime | None = None
) -> FnPortalSession | None:
    """The live session a cookie carries for THIS event, if its email is
    still allowed in."""
    if not raw or len(raw) > 128:
        return None
    now = now or _now()
    session = (
        db.query(FnPortalSession)
        .filter(
            FnPortalSession.session_hash == _session_hash(raw),
            FnPortalSession.organization_id == event.organization_id,
            FnPortalSession.event_id == event.id,
            FnPortalSession.revoked_at.is_(None),
            FnPortalSession.expires_at > now,
        )
        .first()
    )
    if session is None:
        return None
    allowed = db.query(
        exists().where(
            FnPortalAccess.organization_id == event.organization_id,
            FnPortalAccess.event_id == event.id,
            func.lower(FnPortalAccess.email) == session.email,
            FnPortalAccess.revoked_at.is_(None),
        )
    ).scalar()
    if not allowed:
        return None
    if session.last_seen_at is None or now - session.last_seen_at > dt.timedelta(
        minutes=5
    ):
        session.last_seen_at = now
        db.flush()
    return session


def end_session(db: Session, session: FnPortalSession) -> None:
    session.revoked_at = _now()
    db.flush()


def client_ctx(db: Session, event: FnEvent, session: FnPortalSession) -> FunctionsCtx:
    return for_client(db, event.organization_id, event_id=event.id, email=session.email)


# ---------------------------------------------------------------------------
# What the venue shows for this booking type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class View:
    fields_on: dict
    visible: frozenset
    editable: frozenset
    lists: frozenset
    kinds: dict


def view_for(db: Session, event: FnEvent) -> View:
    settings_row = (
        db.query(FnVenueFunctionSettings)
        .filter(
            FnVenueFunctionSettings.organization_id == event.organization_id,
            FnVenueFunctionSettings.venue_id == event.venue_id,
        )
        .first()
    )
    on = enabled_fields_for(settings_row, event.booking_type)
    visible = set(ALWAYS_FIELDS)
    for key, names in FIELD_GATES.items():
        if on.get(key, True):
            visible.update(names)
    lists = {kind for kind, key in LIST_GATES.items() if on.get(key, True)}
    kinds = {
        kind: frozenset(k for k, key in gates.items() if on.get(key, True))
        for kind, gates in KIND_GATES.items()
    }
    return View(
        fields_on=on,
        visible=frozenset(visible),
        editable=frozenset(visible & events_svc.CLIENT_FIELDS),
        lists=frozenset(lists & events_svc.CLIENT_CHILDREN),
        kinds=kinds,
    )


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def _row_dict(row, names) -> dict:
    return {n: jsonable(getattr(row, n)) for n in names}


def payload(db: Session, ctx: FunctionsCtx, event: FnEvent, *, email: str) -> dict:
    """Everything the portal shows the signed-in client, limited to what the
    venue shows for this booking type."""
    view = view_for(db, event)
    company = get_company_settings(db, ctx.org_id)
    state = events_svc.event_state(db, ctx, event.id)

    ev = {"id": event.id, "version": event.version}
    ev.update({name: jsonable(getattr(event, name)) for name in sorted(view.visible)})
    ev["booking_label"] = events_svc.BOOKING_LABELS.get(event.booking_type or "")
    if view.fields_on.get(AREA_GATE, True) and event.area_id:
        ev["area_name"] = (
            db.query(FnVenueArea.name)
            .filter(
                FnVenueArea.organization_id == ctx.org_id,
                FnVenueArea.id == event.area_id,
            )
            .scalar()
        )
    min_spend = None
    if view.fields_on.get(MIN_SPEND_GATE, True):
        option = min_spends_svc.booked_option(db, ctx, event.id)
        if option is not None:
            min_spend = {
                "amount_cents": option.amount_cents,
                "amount_text": format_money(option.amount_cents, company.currency),
                "label": option.label,
            }

    children = events_svc.event_children(db, ctx, event.id)
    lists = {}
    for kind in sorted(view.lists):
        _, names = events_svc.CHILDREN[kind]
        rows = children[kind]
        if kind in view.kinds:
            rows = [r for r in rows if r.kind in view.kinds[kind]]
        lists[kind] = [_row_dict(r, names) for r in rows]

    menus = []
    if view.fields_on.get(MENUS_GATE, True):
        for menu, items in menus_svc.list_menus(db, ctx, event_id=event.id):
            menus.append(
                {
                    "id": menu.id,
                    "version": menu.version,
                    "name": menu.name,
                    "is_set_menu": menu.is_set_menu,
                    "price_per_person_cents": menu.price_per_person_cents,
                    "guest_count": menu.guest_count,
                    "value_cents": menus_svc.menu_value_cents(menu, items),
                    "items": [
                        {
                            "id": i.id,
                            "kind": i.kind,
                            "name": i.name,
                            "description": i.description,
                            "quantity": i.quantity,
                            "unit_price_cents": i.unit_price_cents,
                            "dietaries": list(i.dietaries or []),
                        }
                        for i in items
                    ],
                }
            )

    latest = {}
    for c in events_svc.section_confirmations(db, ctx, event.id):
        latest[c.section] = c  # ordered oldest first, so the last one wins
    sections = {
        s: {
            "state": latest[s].kind if s in latest else None,
            "at": latest[s].occurred_at.isoformat() if s in latest else None,
            "by_client": bool(s in latest and latest[s].actor_kind == "client"),
        }
        for s in EVENT_SECTIONS
    }

    contact = (
        db.query(FnContact)
        .filter(
            FnContact.organization_id == ctx.org_id, FnContact.id == event.contact_id
        )
        .first()
    )
    primary = (
        db.query(FnContactEmail.email)
        .filter(
            FnContactEmail.organization_id == ctx.org_id,
            FnContactEmail.contact_id == event.contact_id,
        )
        .order_by(FnContactEmail.is_primary.desc(), FnContactEmail.created_at)
        .first()
    )
    people = (
        db.query(FnPortalAccess)
        .filter(
            FnPortalAccess.organization_id == ctx.org_id,
            FnPortalAccess.event_id == event.id,
            FnPortalAccess.revoked_at.is_(None),
        )
        .order_by(FnPortalAccess.created_at)
        .all()
    )
    settings_row = (
        db.query(FnVenueFunctionSettings)
        .filter(
            FnVenueFunctionSettings.organization_id == ctx.org_id,
            FnVenueFunctionSettings.venue_id == event.venue_id,
        )
        .first()
    )
    venue = venue_summary(db, ctx.org_id, event.venue_id) or {}
    venue["policies"] = settings_row.policies if settings_row else None
    venue["menu_links"] = [
        {k: link.get(k) for k in ("label", "url", "kind", "description")}
        for link in ((settings_row.menu_links if settings_row else None) or [])
        if isinstance(link, dict) and link.get("active", True) and link.get("url")
    ]
    return {
        "authenticated": True,
        "email": email,
        "event": ev,
        "status": {
            "status": state.status,
            "edited_since_confirmed": state.edited_since_confirmed,
            "sections": sections,
        },
        "editable": sorted(view.editable),
        "fields": view.fields_on,
        "lists": lists,
        "list_kinds": {k: sorted(v) for k, v in view.kinds.items()},
        "menus": menus,
        "min_spend": min_spend,
        "contact": {
            "name": contact.name if contact else None,
            "phone": contact.phone if contact else None,
            "company": contact.company if contact else None,
            "email": primary[0] if primary else None,
        },
        "people": [
            {
                "email": p.email,
                "verified": p.verified_at is not None,
                "invited_by_email": p.invited_by_email,
            }
            for p in people
        ],
        "venue": venue,
        "money": {"currency": company.currency, "tax_label": company.tax_label},
    }


def public_payload(db: Session, event: FnEvent) -> dict:
    """Before sign-in: only who the link is from."""
    return {
        "authenticated": False,
        "venue": venue_summary(db, event.organization_id, event.venue_id),
    }


# ---------------------------------------------------------------------------
# Client edits
# ---------------------------------------------------------------------------


def _coerce(model, name: str, value):
    """A JSON value as the column's Python type (F1's services take typed
    values). Unknown names and wrong types are Invalid."""
    column = model.__table__.c.get(name)
    if column is None:
        raise Invalid(f"Cannot set: {name}.")
    kind = column.type
    if value is None or value == "":
        if isinstance(kind, Boolean):
            return False
        if isinstance(kind, (String, Text)) and not column.nullable:
            return ""
        return None
    if isinstance(kind, Boolean):
        if not isinstance(value, bool):
            raise Invalid(f"{name} must be true or false.")
        return value
    if isinstance(kind, Integer):
        if isinstance(value, bool):
            raise Invalid(f"{name} must be a whole number.")
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise Invalid(f"{name} must be a whole number.") from exc
        if number != value and str(number) != str(value).strip():
            raise Invalid(f"{name} must be a whole number.")
        if not 0 <= number <= 1_000_000_000:
            raise Invalid(f"{name} is out of range.")
        return number
    if isinstance(kind, Time):
        try:
            return dt.time.fromisoformat(str(value))
        except ValueError as exc:
            raise Invalid(f"{name} must be a time like 19:30.") from exc
    if isinstance(kind, (String, Text)):
        if not isinstance(value, (str, int, float)):
            raise Invalid(f"{name} must be text.")
        text = str(value)
        if len(text) > MAX_TEXT:
            raise Invalid(f"{name} is too long.")
        return text
    raise Invalid(f"Cannot set: {name}.")


def apply_changes(
    db: Session, ctx: FunctionsCtx, event: FnEvent, changes: dict
) -> list[FnEventChange]:
    """Apply one autosave: ``{expected_version, fields, contact, lists,
    menus, note}`` — all optional. Every write is a client-scoped F1 service
    call, so F1's limits apply on top of this module's. Returns the change
    rows written."""
    require_live(ctx)
    if not isinstance(changes, dict):
        raise Invalid("Send the changes as an object.")
    unknown = set(changes) - {
        "expected_version",
        "fields",
        "contact",
        "lists",
        "menus",
        "note",
    }
    if unknown:
        raise Invalid(f"Unknown: {', '.join(sorted(unknown))}.")
    view = view_for(db, event)
    note = (changes.get("note") or "").strip()[:MAX_TEXT] or None
    # One version check for the request: the first write below bumps the
    # version, so the later ones must not compare against the old one.
    check_version(event, _version(changes.get("expected_version")))
    started = _now()

    fields = changes.get("fields") or {}
    if not isinstance(fields, dict):
        raise Invalid("fields must be an object.")
    if fields:
        refused = sorted(set(fields) - view.editable)
        if refused:
            raise Forbidden(f"You can't change: {', '.join(refused)}.")
        typed = {n: _coerce(FnEvent, n, v) for n, v in fields.items()}
        events_svc.update_event(db, ctx, event.id, note=note, **typed)

    contact = changes.get("contact") or {}
    if not isinstance(contact, dict):
        raise Invalid("contact must be an object.")
    if contact:
        refused = sorted(set(contact) - set(CONTACT_FIELDS))
        if refused:
            raise Forbidden(f"You can't change: {', '.join(refused)}.")
        typed = {n: _coerce(FnContact, n, v) for n, v in contact.items()}
        events_svc.update_event_contact(db, ctx, event.id, note=note, **typed)

    lists = changes.get("lists") or {}
    if not isinstance(lists, dict):
        raise Invalid("lists must be an object.")
    for kind, items in lists.items():
        if kind not in view.lists:
            raise Forbidden("That list isn't part of this event.")
        events_svc.replace_event_children(
            db,
            ctx,
            event.id,
            kind,
            _merge_list(db, ctx, event, view, kind, items),
            note=note,
        )

    menus = changes.get("menus") or []
    if not isinstance(menus, list):
        raise Invalid("menus must be a list.")
    if menus and not view.fields_on.get(MENUS_GATE, True):
        raise Forbidden("Menus aren't part of this event.")
    for entry in menus:
        if not isinstance(entry, dict) or not isinstance(entry.get("quantities"), dict):
            raise Invalid("Each menu needs an id and its quantities.")
        quantities = {
            str(k): _coerce_quantity(v) for k, v in entry["quantities"].items()
        }
        menus_svc.set_item_quantities(
            db,
            ctx,
            str(entry.get("id") or ""),
            quantities,
            expected_version=_version(entry.get("expected_version")),
            note=note,
        )
    # What this request wrote: the services log every change as this client.
    return (
        db.query(FnEventChange)
        .filter(
            FnEventChange.organization_id == ctx.org_id,
            FnEventChange.event_id == event.id,
            FnEventChange.actor_kind == "client",
            FnEventChange.client_email == ctx.client_email,
            FnEventChange.occurred_at >= started,
        )
        .all()
    )


def _version(value) -> int | None:
    """A version the page sends back: a whole number or nothing."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise Invalid("expected_version must be a whole number.")
    return value


def _coerce_quantity(value) -> int:
    if isinstance(value, bool):
        raise Invalid("Quantities must be whole numbers.")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise Invalid("Quantities must be whole numbers.") from exc
    if not 0 <= number <= 100_000:
        raise Invalid("Quantities must be between 0 and 100,000.")
    return number


def _merge_list(db, ctx, event, view, kind, items) -> list[dict]:
    """The client's rows, typed — plus any existing rows of kinds the venue
    hides from the client (the client never saw them, so replacing the list
    must not delete them)."""
    if not isinstance(items, list) or len(items) > 100:
        raise Invalid("Send the list as up to 100 rows.")
    model, names = events_svc.CHILDREN[kind]
    allowed = view.kinds.get(kind)
    typed = []
    for raw in items:
        if not isinstance(raw, dict):
            raise Invalid("Each row must be an object.")
        unknown = set(raw) - set(names)
        if unknown:
            raise Invalid(f"Cannot set: {', '.join(sorted(unknown))}.")
        row = {n: _coerce(model, n, v) for n, v in raw.items()}
        if allowed is not None and row.get("kind") not in allowed:
            raise Forbidden("That option isn't available for this event.")
        typed.append(row)
    if allowed is None:
        return typed
    hidden = [
        {n: getattr(r, n) for n in names}
        for r in events_svc.event_children(db, ctx, event.id)[kind]
        if r.kind not in allowed
    ]
    return hidden + typed


def edit_notices(
    db: Session, ctx: FunctionsCtx, event: FnEvent, rows: list[FnEventChange]
) -> list[notify.Notice]:
    """Email staff when the client changed a CONFIRMED event — unless they
    already made such a change in the last half hour (same editing
    session)."""
    late = [r for r in rows if r.after_confirmation and r.actor_kind == "client"]
    if not late:
        return []
    earlier = db.query(
        exists().where(
            FnEventChange.organization_id == ctx.org_id,
            FnEventChange.event_id == event.id,
            FnEventChange.actor_kind == "client",
            FnEventChange.after_confirmation.is_(True),
            FnEventChange.occurred_at > _now() - EDIT_EMAIL_QUIET,
            FnEventChange.id.notin_([r.id for r in late]),
        )
    ).scalar()
    if earlier:
        return []
    changed = sorted(
        {_label(k) for r in late for k in (r.changes or {})} or {"details"}
    )
    args = _event_notice_args(db, ctx, event)
    return [
        lambda session: notify.client_edited(
            session, client_email=ctx.client_email or "", changed=changed, **args
        )
    ]


def _label(key: str) -> str:
    return key.replace("contact.", "contact ").replace("_cents", "").replace("_", " ")


def _event_notice_args(db, ctx, event) -> dict:
    owner = (
        db.query(FnEnquiry.owner_user_id)
        .filter(
            FnEnquiry.organization_id == ctx.org_id, FnEnquiry.id == event.enquiry_id
        )
        .scalar()
        if event.enquiry_id
        else None
    )
    return {
        "org_id": ctx.org_id,
        "venue_id": event.venue_id,
        "owner_user_id": owner,
        "event_id": event.id,
        "event_name": event.name,
        "event_date": event.event_date.strftime("%a %d %b %Y"),
    }


# ---------------------------------------------------------------------------
# Confirming, inviting
# ---------------------------------------------------------------------------


def confirm_sections(
    db: Session,
    ctx: FunctionsCtx,
    event: FnEvent,
    sections: list[str],
    *,
    note: str | None = None,
) -> list[notify.Notice]:
    """The client confirms (or re-confirms) one or more sections; staff get
    one email for the lot."""
    require_live(ctx)
    wanted = list(dict.fromkeys(sections or []))
    if not wanted or any(s not in EVENT_SECTIONS for s in wanted):
        raise Invalid(f"Sections must be among {', '.join(EVENT_SECTIONS)}.")
    text = (note or "").strip()[:MAX_TEXT] or None
    for section in wanted:
        events_svc.confirm_section(db, ctx, event.id, section, note=text)
    state = events_svc.event_state(db, ctx, event.id)
    args = _event_notice_args(db, ctx, event)
    return [
        lambda session: notify.sections_confirmed(
            session,
            client_email=ctx.client_email or "",
            sections=wanted,
            all_confirmed=state.status == "confirmed",
            note=text,
            **args,
        )
    ]


def invite(
    db: Session,
    ctx: FunctionsCtx,
    event: FnEvent,
    email: str,
    *,
    link_token: str,
) -> list[notify.Notice]:
    """A signed-in client gives a colleague access, and the colleague is
    emailed the link (Orbit added them silently and sent nothing)."""
    require_live(ctx)
    if event.cancelled_at is not None:
        raise Conflict("This event is cancelled.")
    address = normalise_email(email)
    count = (
        db.query(func.count(FnPortalAccess.id))
        .filter(
            FnPortalAccess.organization_id == ctx.org_id,
            FnPortalAccess.event_id == event.id,
            FnPortalAccess.revoked_at.is_(None),
        )
        .scalar()
    )
    existing = (
        db.query(FnPortalAccess.id)
        .filter(
            FnPortalAccess.organization_id == ctx.org_id,
            FnPortalAccess.event_id == event.id,
            func.lower(FnPortalAccess.email) == address,
            FnPortalAccess.revoked_at.is_(None),
        )
        .first()
    )
    if existing is not None:
        return []
    if count >= MAX_PEOPLE:
        raise Conflict(
            "This event already has as many people as it can — ask the venue."
        )
    public_tokens.allow_email(
        db, ctx, event, address, invited_by_email=ctx.client_email
    )
    venue = venue_summary(db, ctx.org_id, event.venue_id) or {}
    args = {
        "org_id": ctx.org_id,
        "venue_id": event.venue_id,
        "to": address,
        "inviter_email": ctx.client_email or "",
        "venue_name": venue.get("name") or "the venue",
        "event_name": event.name,
        "url": public_tokens.portal_url(link_token),
    }
    return [lambda session: notify.portal_invite(session, **args)]


# ---------------------------------------------------------------------------
# Staff: who may open the portal
# ---------------------------------------------------------------------------


def access_list(db: Session, ctx: FunctionsCtx, event_id: str) -> list[dict]:
    """The emails allowed into the event's portal (current and removed), for
    staff. ``locked_for_wrong_codes``: removed today by the lockout rather
    than by a person."""
    require_staff(ctx)
    event = events_svc.get_event(db, ctx, event_id)
    rows = (
        db.query(FnPortalAccess)
        .filter(
            FnPortalAccess.organization_id == ctx.org_id,
            FnPortalAccess.event_id == event.id,
        )
        .order_by(FnPortalAccess.created_at)
        .all()
    )
    now = _now()
    return [
        {
            "email": r.email,
            "allowed": r.revoked_at is None,
            "verified_at": r.verified_at.isoformat() if r.verified_at else None,
            "invited_by_email": r.invited_by_email,
            "removed_at": r.revoked_at.isoformat() if r.revoked_at else None,
            "locked_for_wrong_codes": r.revoked_at is not None
            and failures_today(db, r.id, now) >= FAILS_PER_DAY,
        }
        for r in rows
    ]


def allow_access(
    db: Session, ctx: FunctionsCtx, event_id: str, email: str
) -> FnPortalAccess:
    """Allow an email in — or back in after staff removed it or the lockout
    did — with a clean slate: no live code, no counted wrong codes."""
    require_live(ctx)
    require_staff(ctx)
    event = events_svc.get_event(db, ctx, event_id, for_update=True)
    if event.cancelled_at is not None:
        raise Conflict("This event is cancelled — reinstate it first.")
    row = public_tokens.allow_email(
        db, ctx, event, email, invited_by_user_id=ctx.user_id
    )
    row.otp_hash = None
    row.otp_expires_at = None
    row.otp_attempts = 0
    db.execute(
        text("DELETE FROM mcp_rate_limits WHERE bucket_key = :key"),
        {"key": _failure_key(row.id)},
    )
    flush(db)
    return row


def remove_access(db: Session, ctx: FunctionsCtx, event_id: str, email: str) -> None:
    """Take an email out of the portal now: it can't get a code, and anyone
    signed in with it is signed out. (The link itself keeps working for the
    others; revoke the link to close it for everyone.)"""
    require_live(ctx)
    require_staff(ctx)
    event = events_svc.get_event(db, ctx, event_id, for_update=True)
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
        raise NotFound("That email can't open this event's portal.")
    now = _now()
    if row.revoked_at is None:
        row.revoked_at = now
    row.otp_hash = None
    row.otp_expires_at = None
    _end_sessions(db, event, address, now)
    flush(db)
