"""Events — booking, editing, the client's section confirmations, cancelling.

How an event comes to exist decides who may create it:

* A **minimum-spend** booking type (large events) is created only by the
  customer confirming an approved option — ``min_spends.confirm_option``.
* A booking **without** a minimum spend (seated dining, group bookings) is
  confirmed the same way on the enquiry itself — ``confirm_booking`` — or
  entered directly with no enquiry — ``create_event``.

Either way the event LINKS what was agreed (the option via
``fn_min_spends.event_id``, the food via ``fn_preorder_menus.event_id``) rather
than copying it, and every edit afterwards is written to ``fn_event_changes``
— with the client's edits marked as theirs, because "edited by the client
since they confirmed" is a status flag staff are emailed about.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db.functions_models import (
    AV_KINDS,
    BOOKING_TYPES,
    ENTERTAINMENT_KINDS,
    EVENT_SECTIONS,
    FOOD_TIMING_TYPES,
    MIN_SPEND_BOOKING_TYPES,
    RESPONSE_CHANNELS,
    FnContact,
    FnEnquiry,
    FnEvent,
    FnEventAv,
    FnEventChange,
    FnEventEntertainment,
    FnEventSectionConfirmation,
    FnEventSecurity,
    FnEventTiming,
    FnFile,
    FnMinSpend,
    FnPreorderMenu,
    FnVenueArea,
)
from app.db.models import Venue, _now
from app.services.functions import contacts as contacts_svc
from app.services.functions import enquiries as enquiries_svc
from app.services.functions import money as money_svc
from app.services.functions import status as status_svc
from app.services.functions._rows import (
    apply_fields,
    check_version,
    jsonable,
    load,
    require_text,
)
from app.services.functions.ctx import (
    FunctionsCtx,
    require_live,
    require_staff,
    require_venue,
)
from app.services.functions.errors import Conflict, Forbidden, Invalid, flush
from app.services.functions.timeutil import validate_window, venue_tz

BOOKING_LABELS = {
    "seated_dining": "Seated dining",
    "group_booking": "Group booking",
    "large_event_partial": "Large event (partial exclusive)",
    "large_event_exclusive": "Large event (exclusive)",
}

#: Every field update_event may change, and the portal section it belongs to
#: (the change log records the section; run-sheet fields count as details).
EVENT_FIELD_SECTIONS = {
    "name": "details",
    "contact_id": "details",
    "invoice_contact_id": "details",
    "booking_type": "details",
    "area_id": "details",
    "event_date": "details",
    "start_time": "details",
    "end_time": "details",
    "ends_next_day": "details",
    "guests": "details",
    "description": "details",
    "notes": "details",
    "special_instructions": "details",
    "payment_method": "details",
    "deposit_cents": "details",
    "deposit_status": "details",
    "table_plan": "details",
    "table_plan_file_id": "details",
    "arrival_time": "details",
    "seated_time": "details",
    "timing_preference": "details",
    "timing_notes": "details",
    "bar_tab_cents": "beverages",
    "bar_tab_authorised_people": "beverages",
    "bar_tab_restrictions": "beverages",
    "bar_tab_notes": "beverages",
    "arrival_drinks": "beverages",
    "table_ordering": "beverages",
    "table_ordering_limit_cents": "beverages",
    "allergies": "food",
    "food_timing_type": "food",
    "food_time": "food",
    "food_end_time": "food",
    "food_notes": "food",
}
#: What a client may change from the portal — Orbit's portal save, field for
#: field. The venue's enabled fields narrow it further (the portal applies
#: those); the booking and the money — date, times, area, payment, deposit,
#: the bar tab amount — are staff's to change.
CLIENT_FIELDS = frozenset(
    {
        "guests",
        "notes",
        "special_instructions",
        "arrival_time",
        "seated_time",
        "timing_preference",
        "timing_notes",
        "bar_tab_authorised_people",
        "bar_tab_restrictions",
        "bar_tab_notes",
        "arrival_drinks",
        "table_ordering",
        "table_ordering_limit_cents",
        "allergies",
        "food_timing_type",
        "food_time",
        "food_end_time",
        "food_notes",
    }
)

#: Run-sheet style children replaced as a whole list: kind -> (model, fields).
CHILDREN = {
    "timings": (
        FnEventTiming,
        ("at_time", "ends_next_day", "label", "activity", "is_heading"),
    ),
    "security": (
        FnEventSecurity,
        ("name", "start_time", "end_time", "ends_next_day", "notes"),
    ),
    "entertainment": (
        FnEventEntertainment,
        ("kind", "name", "link", "start_time", "end_time", "ends_next_day", "notes"),
    ),
    "av": (FnEventAv, ("kind", "details")),
}
CLIENT_CHILDREN = frozenset({"timings", "security", "entertainment", "av"})
#: Contact details a client may correct from the portal (on fn_contacts).
#: Email is not among them: it is how they and their colleagues get in.
EVENT_CONTACT_FIELDS = ("name", "phone", "company")


def visible_events(db: Session, ctx: FunctionsCtx):
    return db.query(FnEvent).filter(
        FnEvent.organization_id == ctx.org_id,
        ctx.venue_clause(FnEvent.venue_id),
        ctx.record_clause(FnEvent),
    )


def get_event(
    db: Session, ctx: FunctionsCtx, event_id: str, *, for_update: bool = False
) -> FnEvent:
    return load(db, ctx, FnEvent, event_id, label="Event", for_update=for_update)


def _venue(db: Session, ctx: FunctionsCtx, venue_id: str) -> Venue:
    require_venue(ctx, venue_id)
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if venue is None or venue.organization_id != ctx.org_id:
        raise Forbidden("You do not have access to that venue.")
    venue_tz(venue)  # Functions venues must have a timezone
    return venue


def _check_area(
    db: Session, ctx: FunctionsCtx, venue_id: str, area_id: str | None
) -> None:
    if area_id is None:
        return
    found = (
        db.query(FnVenueArea.id)
        .filter(
            FnVenueArea.organization_id == ctx.org_id,
            FnVenueArea.venue_id == venue_id,
            FnVenueArea.id == area_id,
        )
        .first()
    )
    if found is None:
        raise Invalid("That area is not at this venue.")


def _validate_fields(
    db: Session, ctx: FunctionsCtx, event: FnEvent | None, fields: dict
) -> None:
    if (
        fields.get("booking_type") is not None
        and fields["booking_type"] not in BOOKING_TYPES
    ):
        raise Invalid(f"Booking type must be one of {', '.join(BOOKING_TYPES)}.")
    ftt = fields.get("food_timing_type")
    if ftt is not None and ftt not in FOOD_TIMING_TYPES:
        raise Invalid(f"Food timing must be one of {', '.join(FOOD_TIMING_TYPES)}.")
    for money_field in ("bar_tab_cents", "deposit_cents", "table_ordering_limit_cents"):
        if fields.get(money_field) is not None and int(fields[money_field]) < 0:
            raise Invalid("Amounts cannot be negative.")
    if fields.get("guests") is not None and int(fields["guests"]) < 0:
        raise Invalid("Guests cannot be negative.")
    if "name" in fields:
        fields["name"] = require_text(fields["name"], "An event name")
    for contact_field in ("contact_id", "invoice_contact_id"):
        if fields.get(contact_field):
            contacts_svc.get_contact(db, ctx, fields[contact_field])
    if fields.get("contact_id", 1) is None:
        raise Invalid("An event needs a contact.")
    venue_id = event.venue_id if event is not None else fields.get("venue_id")
    if "area_id" in fields:
        _check_area(db, ctx, venue_id, fields["area_id"])
    if fields.get("table_plan_file_id") is not None:
        found = (
            db.query(FnFile.id)
            .filter(
                FnFile.organization_id == ctx.org_id,
                FnFile.id == fields["table_plan_file_id"],
            )
            .first()
        )
        if found is None:
            raise Invalid("That table-plan file doesn't exist.")

    def current(name):
        if name in fields:
            return fields[name]
        return getattr(event, name) if event is not None else None

    validate_window(
        current("start_time"), current("end_time"), bool(current("ends_next_day"))
    )


def _default_name(contact_name: str | None, booking_type: str | None) -> str:
    label = BOOKING_LABELS.get(booking_type or "", "Function")
    return f"{contact_name} — {label}" if contact_name else label


def _log(
    db: Session,
    ctx: FunctionsCtx,
    event: FnEvent,
    kind: str,
    *,
    section: str | None = None,
    changes: dict | None = None,
    after_confirmation: bool = False,
    note: str | None = None,
) -> FnEventChange:
    row = FnEventChange(
        organization_id=ctx.org_id,
        event_id=event.id,
        kind=kind,
        actor_kind=ctx.actor_kind,
        actor_user_id=ctx.user_id,
        client_email=ctx.client_email,
        actor_label=ctx.actor_label,
        section=section,
        changes=changes,
        after_confirmation=after_confirmation,
        note=(note or "").strip() or None,
        occurred_at=_now(),
    )
    db.add(row)
    return row


def new_event(
    db: Session,
    ctx: FunctionsCtx,
    *,
    venue_id: str,
    contact_id: str,
    event_date: dt.date,
    created_via: str,
    enquiry: FnEnquiry | None = None,
    name: str | None = None,
    booking_type: str | None = None,
    area_id: str | None = None,
    start_time: dt.time | None = None,
    end_time: dt.time | None = None,
    ends_next_day: bool = False,
    guests: int | None = None,
    confirmation_channel: str | None = None,
    note: str | None = None,
    details: dict | None = None,
) -> FnEvent:
    """Insert an event and its ``created`` log row, and link the enquiry's
    menus at this venue. No policy checks — the public entry points
    (``create_event``, ``confirm_booking``, ``confirm_option``) decide first."""
    if event_date is None:
        raise Invalid("A booking needs a date.")
    _venue(db, ctx, venue_id)
    _check_area(db, ctx, venue_id, area_id)
    validate_window(start_time, end_time, ends_next_day)
    contact = (
        db.query(FnContact)
        .filter(FnContact.organization_id == ctx.org_id, FnContact.id == contact_id)
        .first()
    )
    if contact is None:
        raise Invalid("An event needs a contact.")
    fields = dict(details or {})
    unknown = set(fields) - set(EVENT_FIELD_SECTIONS)
    if unknown:
        raise Invalid(f"Cannot set: {', '.join(sorted(unknown))}.")
    event = FnEvent(
        organization_id=ctx.org_id,
        enquiry_id=enquiry.id if enquiry is not None else None,
        contact_id=contact.id,
        venue_id=venue_id,
        area_id=area_id,
        booking_type=booking_type,
        name=(name or "").strip()
        or (enquiry.subject if enquiry is not None and enquiry.subject else None)
        or _default_name(contact.name, booking_type),
        event_date=event_date,
        start_time=start_time,
        end_time=end_time,
        ends_next_day=bool(ends_next_day),
        guests=guests,
        created_via=created_via,
        confirmation_channel=confirmation_channel,
        created_by_user_id=ctx.user_id,
        created_by_label=ctx.actor_label,
        **fields,
    )
    db.add(event)
    flush(db)
    if enquiry is not None:
        link_enquiry_menus(db, ctx, enquiry.id, venue_id, event)
    _log(db, ctx, event, "created", note=note)
    flush(db)
    return event


def link_enquiry_menus(
    db: Session, ctx: FunctionsCtx, enquiry_id: str, venue_id: str, event: FnEvent
) -> list[FnPreorderMenu]:
    """Point the enquiry's menus at this venue to the event — unbooked ones,
    or ones left on a cancelled booking of the same enquiry."""
    cancelled = select(FnEvent.id).where(
        FnEvent.organization_id == ctx.org_id,
        FnEvent.enquiry_id == enquiry_id,
        FnEvent.cancelled_at.is_not(None),
    )
    menus = (
        db.query(FnPreorderMenu)
        .filter(
            FnPreorderMenu.organization_id == ctx.org_id,
            FnPreorderMenu.enquiry_id == enquiry_id,
            FnPreorderMenu.venue_id == venue_id,
            or_(
                FnPreorderMenu.event_id.is_(None),
                FnPreorderMenu.event_id.in_(cancelled),
            ),
        )
        .all()
    )
    for m in menus:
        m.event_id = event.id
    flush(db)
    return menus


def create_event(
    db: Session,
    ctx: FunctionsCtx,
    *,
    venue_id: str,
    contact_id: str | None = None,
    event_date: dt.date,
    booking_type: str,
    name: str | None = None,
    area_id: str | None = None,
    start_time: dt.time | None = None,
    end_time: dt.time | None = None,
    ends_next_day: bool = False,
    guests: int | None = None,
    note: str | None = None,
    contact: dict | None = None,
    **details,
) -> FnEvent:
    """A booking entered directly, with no enquiry — only for booking types
    without a minimum spend. A large event has to be an approved option the
    customer confirmed, or approvals would mean nothing.

    The customer is ``contact_id`` (one the caller can see) or ``contact``
    (``{name, email, phone, company}``, matched by email to the org's contact
    like ``enquiries.create_enquiry`` — never revealing whether another venue
    already knows them)."""
    require_live(ctx)
    require_staff(ctx)
    if booking_type not in BOOKING_TYPES:
        raise Invalid(f"Booking type must be one of {', '.join(BOOKING_TYPES)}.")
    if booking_type in MIN_SPEND_BOOKING_TYPES:
        raise Invalid(
            "Large events are booked by the customer confirming an approved "
            "minimum-spend option on an enquiry."
        )
    if contact_id is None and not contact:
        raise Invalid("An event needs a contact.")
    if contact_id is not None:
        contacts_svc.get_contact(db, ctx, contact_id)
    require_venue(ctx, venue_id)
    _validate_fields(db, ctx, None, dict(details) | {"venue_id": venue_id})
    if contact_id is None:
        found, _ = contacts_svc.find_or_create_contact(
            db,
            ctx,
            email=contact.get("email"),
            name=contact.get("name"),
            phone=contact.get("phone"),
            company=contact.get("company"),
        )
        contact_id = found.id
    return new_event(
        db,
        ctx,
        venue_id=venue_id,
        contact_id=contact_id,
        event_date=event_date,
        created_via="direct",
        name=name,
        booking_type=booking_type,
        area_id=area_id,
        start_time=start_time,
        end_time=end_time,
        ends_next_day=ends_next_day,
        guests=guests,
        note=note,
        details=details,
    )


def confirm_booking(
    db: Session,
    ctx: FunctionsCtx,
    enquiry_id: str,
    *,
    channel: str,
    event_date: dt.date | None = None,
    start_time: dt.time | None = None,
    end_time: dt.time | None = None,
    ends_next_day: bool | None = None,
    venue_id: str | None = None,
    area_id: str | None = None,
    name: str | None = None,
    guests: int | None = None,
    note: str | None = None,
) -> tuple[FnEvent, bool]:
    """The customer confirmed an enquiry that has no minimum spend: create its
    event, exactly once. ``(event, created)`` — a repeat returns the live
    event with ``created`` False. Dates and times default to the enquiry's
    preferred ones; the venue to the enquiry's."""
    require_live(ctx)
    if channel not in RESPONSE_CHANNELS or channel == "import":
        raise Invalid(
            "Say how the customer confirmed: quote_page, email, phone or in_person."
        )
    if channel != "quote_page":
        require_staff(ctx)
    enquiry = enquiries_svc.get_enquiry(db, ctx, enquiry_id, for_update=True)
    live = (
        db.query(FnEvent)
        .filter(
            FnEvent.organization_id == ctx.org_id,
            FnEvent.enquiry_id == enquiry.id,
            FnEvent.cancelled_at.is_(None),
        )
        .first()
    )
    if live is not None:
        return live, False
    enquiries_svc.require_open_enquiry(db, ctx, enquiry)
    if enquiry.booking_type in MIN_SPEND_BOOKING_TYPES:
        raise Invalid("This is a minimum-spend booking — confirm one of its options.")
    open_options = (
        db.query(FnMinSpend.id)
        .filter(
            FnMinSpend.organization_id == ctx.org_id,
            FnMinSpend.enquiry_id == enquiry.id,
            FnMinSpend.withdrawn_at.is_(None),
        )
        .first()
    )
    if open_options is not None:
        raise Invalid("This enquiry has minimum-spend options — confirm one of those.")
    venue = venue_id or enquiry.venue_id
    if venue is None:
        raise Invalid("Choose the venue for this booking.")
    start = start_time if start_time is not None else enquiry.preferred_start_time
    end = end_time if end_time is not None else enquiry.preferred_end_time
    next_day = (
        ends_next_day if ends_next_day is not None else enquiry.preferred_ends_next_day
    )
    event = new_event(
        db,
        ctx,
        venue_id=venue,
        contact_id=enquiry.contact_id,
        event_date=event_date or enquiry.preferred_date_start,
        created_via="booking",
        enquiry=enquiry,
        name=name,
        booking_type=enquiry.booking_type,
        area_id=area_id,
        start_time=start,
        end_time=end,
        ends_next_day=bool(next_day),
        guests=guests if guests is not None else enquiry.guests,
        confirmation_channel=channel,
        note=note or f"Confirmed by the customer ({channel.replace('_', ' ')}).",
    )
    return event, True


def _status(db: Session, ctx: FunctionsCtx, event: FnEvent) -> status_svc.EventStatus:
    return status_svc.event_status_for(db, ctx.org_id, event.id)


def _require_not_cancelled(event: FnEvent) -> None:
    if event.cancelled_at is not None:
        raise Conflict("This event is cancelled — reinstate it first.")


def update_event(
    db: Session,
    ctx: FunctionsCtx,
    event_id: str,
    *,
    expected_version: int | None = None,
    note: str | None = None,
    **fields,
) -> list[FnEventChange]:
    """Edit an event's fields. One ``edited`` log row per section touched,
    each marked ``after_confirmation`` when the event was confirmed — the
    caller (the portal) emails staff when a CLIENT row carries that flag.
    Returns the rows written (empty when nothing changed)."""
    require_live(ctx)
    event = get_event(db, ctx, event_id)
    check_version(event, expected_version)
    _require_not_cancelled(event)
    if ctx.actor_kind == "client":
        refused = set(fields) - CLIENT_FIELDS
        if refused:
            raise Forbidden(f"Clients cannot change: {', '.join(sorted(refused))}.")
    _validate_fields(db, ctx, event, fields)
    confirmed = _status(db, ctx, event).status == "confirmed"
    changes = apply_fields(event, fields, EVENT_FIELD_SECTIONS)
    by_section: dict[str, dict] = {}
    for name, diff in changes.items():
        by_section.setdefault(EVENT_FIELD_SECTIONS[name], {})[name] = diff
    rows = [
        _log(
            db,
            ctx,
            event,
            "edited",
            section=section,
            changes=diff,
            after_confirmation=confirmed,
            note=note,
        )
        for section, diff in by_section.items()
    ]
    flush(db)
    return rows


def update_event_contact(
    db: Session,
    ctx: FunctionsCtx,
    event_id: str,
    *,
    note: str | None = None,
    **fields,
) -> FnEventChange | None:
    """Correct the event's contact — name, phone, company — as the client
    (portal) or staff. The contact is one record, so the correction shows on
    their other bookings too; it is logged on this event as an ``edited``
    row (``contact.<field>``), which makes it a client edit when the client
    made it."""
    require_live(ctx)
    event = get_event(db, ctx, event_id)
    _require_not_cancelled(event)
    contact = (
        db.query(FnContact)
        .filter(
            FnContact.organization_id == ctx.org_id, FnContact.id == event.contact_id
        )
        .first()
    )
    if "name" in fields:
        fields["name"] = require_text(fields["name"], "A name")
    for optional in ("phone", "company"):
        if optional in fields:
            fields[optional] = (fields[optional] or "").strip() or None
    confirmed = _status(db, ctx, event).status == "confirmed"
    changes = apply_fields(contact, fields, EVENT_CONTACT_FIELDS)
    if not changes:
        return None
    row = _log(
        db,
        ctx,
        event,
        "edited",
        section="details",
        changes={f"contact.{k}": v for k, v in changes.items()},
        after_confirmation=confirmed,
        note=note,
    )
    flush(db)
    return row


def _child_dict(row, names) -> dict:
    return {n: jsonable(getattr(row, n)) for n in names}


def event_children(db: Session, ctx: FunctionsCtx, event_id: str) -> dict:
    """``{timings, security, entertainment, av}`` — ORM rows in order."""
    get_event(db, ctx, event_id)
    out = {}
    for kind, (model, _) in CHILDREN.items():
        out[kind] = (
            db.query(model)
            .filter(model.organization_id == ctx.org_id, model.event_id == event_id)
            .order_by(model.sort_order, model.created_at)
            .all()
        )
    return out


def replace_event_children(
    db: Session,
    ctx: FunctionsCtx,
    event_id: str,
    kind: str,
    rows: list[dict],
    *,
    expected_version: int | None = None,
    note: str | None = None,
) -> FnEventChange | None:
    """Replace the whole run-sheet / security / entertainment / A-V list.
    Logged as one ``edited`` row (``{kind: [old, new]}``) and bumps the
    event's version, so two editors of the same list conflict properly."""
    require_live(ctx)
    if kind not in CHILDREN:
        raise Invalid(f"Unknown list '{kind}'.")
    if ctx.actor_kind == "client" and kind not in CLIENT_CHILDREN:
        raise Forbidden("Clients cannot change that.")
    model, names = CHILDREN[kind]
    event = get_event(db, ctx, event_id)
    check_version(event, expected_version)
    _require_not_cancelled(event)
    vocab = {"entertainment": ENTERTAINMENT_KINDS, "av": AV_KINDS}.get(kind)
    clean = []
    for r in rows:
        unknown = set(r) - set(names)
        if unknown:
            raise Invalid(f"Cannot set: {', '.join(sorted(unknown))}.")
        if vocab and r.get("kind") not in vocab:
            raise Invalid(f"Kind must be one of {', '.join(vocab)}.")
        if kind != "timings" and kind != "av":
            validate_window(
                r.get("start_time"), r.get("end_time"), bool(r.get("ends_next_day"))
            )
        clean.append(r)
    existing = (
        db.query(model)
        .filter(model.organization_id == ctx.org_id, model.event_id == event.id)
        .order_by(model.sort_order, model.created_at)
        .all()
    )
    old = [_child_dict(x, names) for x in existing]
    for x in existing:
        db.delete(x)
    flush(db)
    for i, r in enumerate(clean):
        db.add(model(organization_id=ctx.org_id, event_id=event.id, sort_order=i, **r))
    flush(db)
    new = [
        _child_dict(x, names)
        for x in db.query(model)
        .filter(model.organization_id == ctx.org_id, model.event_id == event.id)
        .order_by(model.sort_order)
        .all()
    ]
    if old == new:
        return None
    confirmed = _status(db, ctx, event).status == "confirmed"
    event.updated_at = _now()
    row = _log(
        db,
        ctx,
        event,
        "edited",
        section="details",
        changes={kind: [old, new]},
        after_confirmation=confirmed,
        note=note,
    )
    flush(db)
    return row


# ---------------------------------------------------------------------------
# Section confirmations (the client portal's three sections)
# ---------------------------------------------------------------------------


def _section_fact(
    db: Session, ctx: FunctionsCtx, event: FnEvent, section: str, kind: str, note
) -> FnEventSectionConfirmation:
    if section not in EVENT_SECTIONS:
        raise Invalid(f"Section must be one of {', '.join(EVENT_SECTIONS)}.")
    _require_not_cancelled(event)
    row = FnEventSectionConfirmation(
        organization_id=ctx.org_id,
        event_id=event.id,
        section=section,
        kind=kind,
        actor_kind=ctx.actor_kind,
        client_email=ctx.client_email,
        actor_user_id=ctx.user_id,
        actor_label=ctx.actor_label,
        note=(note or "").strip() or None,
        occurred_at=_now(),
    )
    db.add(row)
    event.updated_at = _now()
    flush(db)
    return row


def confirm_section(
    db: Session,
    ctx: FunctionsCtx,
    event_id: str,
    section: str,
    *,
    note: str | None = None,
) -> FnEventSectionConfirmation:
    """The client confirmed a section (or staff record that they did). All
    three confirmed-or-skipped makes the event confirmed; confirming again
    after an edit clears "edited since confirmed"."""
    require_live(ctx)
    event = get_event(db, ctx, event_id)
    return _section_fact(db, ctx, event, section, "confirmed", note)


def skip_section(
    db: Session, ctx: FunctionsCtx, event_id: str, section: str, *, note: str
) -> FnEventSectionConfirmation:
    """Staff mark a section as not needing the client, saying why."""
    require_live(ctx)
    require_staff(ctx)
    event = get_event(db, ctx, event_id)
    return _section_fact(
        db, ctx, event, section, "skipped", require_text(note, "A note")
    )


def reopen_section(
    db: Session,
    ctx: FunctionsCtx,
    event_id: str,
    section: str,
    *,
    note: str | None = None,
) -> FnEventSectionConfirmation:
    """Staff undo a confirmation or skip; the section needs the client again."""
    require_live(ctx)
    require_staff(ctx)
    event = get_event(db, ctx, event_id)
    return _section_fact(db, ctx, event, section, "reopened", note)


def section_confirmations(
    db: Session, ctx: FunctionsCtx, event_id: str
) -> list[FnEventSectionConfirmation]:
    get_event(db, ctx, event_id)
    return (
        db.query(FnEventSectionConfirmation)
        .filter(
            FnEventSectionConfirmation.organization_id == ctx.org_id,
            FnEventSectionConfirmation.event_id == event_id,
        )
        .order_by(
            FnEventSectionConfirmation.occurred_at,
            FnEventSectionConfirmation.created_at,
        )
        .all()
    )


# ---------------------------------------------------------------------------
# Link sent, cancel, reinstate
# ---------------------------------------------------------------------------


def record_link_sent(
    db: Session,
    ctx: FunctionsCtx,
    event_id: str,
    *,
    to_emails: list[str],
    outbox_id: str | None = None,
    note: str | None = None,
) -> FnEventChange:
    """The portal link went to the client — the "sent to client" fact. Call
    it once the outbox has confirmed the send."""
    require_live(ctx)
    require_staff(ctx)
    event = get_event(db, ctx, event_id)
    _require_not_cancelled(event)
    row = _log(
        db,
        ctx,
        event,
        "link_sent",
        changes={"to": list(to_emails), "outbox_id": outbox_id},
        note=note,
    )
    flush(db)
    return row


def cancel_event(
    db: Session,
    ctx: FunctionsCtx,
    event_id: str,
    *,
    reason: str,
    expected_version: int | None = None,
) -> FnEvent:
    """Cancel with a reason. Its enquiry (if no other live booking) is closed
    as lost with the same reason — a cancelled booking must not fall back
    into the open pipeline as "quoted"."""
    require_live(ctx)
    require_staff(ctx)
    text = require_text(reason, "A reason")
    event = get_event(db, ctx, event_id, for_update=True)
    check_version(event, expected_version)
    if event.cancelled_at is not None:
        raise Conflict("This event is already cancelled.")
    event.cancelled_at = _now()
    event.cancel_reason = text
    event.cancelled_by_user_id = ctx.user_id
    _log(db, ctx, event, "cancelled", note=text)
    flush(db)
    if event.enquiry_id:
        enquiry = (
            db.query(FnEnquiry)
            .filter(
                FnEnquiry.organization_id == ctx.org_id,
                FnEnquiry.id == event.enquiry_id,
            )
            .first()
        )
        latest = enquiries_svc._latest_closure(db, ctx, event.enquiry_id)
        if enquiry is not None and (latest is None or latest.kind != "lost"):
            enquiries_svc.record_closure(
                db, ctx, enquiry, "lost", f"Event cancelled: {text}"
            )
    return event


def reinstate_event(
    db: Session, ctx: FunctionsCtx, event_id: str, *, reason: str | None = None
) -> FnEvent:
    """Undo a cancellation (and reopen the enquiry it closed)."""
    require_live(ctx)
    require_staff(ctx)
    event = get_event(db, ctx, event_id, for_update=True)
    if event.cancelled_at is None:
        raise Conflict("This event is not cancelled.")
    previous = event.cancel_reason
    event.cancelled_at = None
    event.cancel_reason = None
    event.cancelled_by_user_id = None
    _log(
        db,
        ctx,
        event,
        "reinstated",
        changes={"cancel_reason": [previous, None]},
        note=reason,
    )
    flush(db)  # the live-event unique index refuses a second live booking here
    if event.enquiry_id:
        latest = enquiries_svc._latest_closure(db, ctx, event.enquiry_id)
        if latest is not None and latest.kind == "lost":
            enquiry = (
                db.query(FnEnquiry)
                .filter(
                    FnEnquiry.organization_id == ctx.org_id,
                    FnEnquiry.id == event.enquiry_id,
                )
                .first()
            )
            enquiries_svc.record_closure(
                db,
                ctx,
                enquiry,
                "reopened",
                (reason or "").strip() or "Event reinstated",
            )
    return event


def event_changes(db: Session, ctx: FunctionsCtx, event_id: str) -> list[FnEventChange]:
    get_event(db, ctx, event_id)
    return (
        db.query(FnEventChange)
        .filter(
            FnEventChange.organization_id == ctx.org_id,
            FnEventChange.event_id == event_id,
        )
        .order_by(FnEventChange.occurred_at, FnEventChange.created_at)
        .all()
    )


def event_state(
    db: Session, ctx: FunctionsCtx, event_id: str
) -> status_svc.EventStatus:
    event = get_event(db, ctx, event_id)
    return _status(db, ctx, event)


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


@dataclass
class EventRow:
    event: FnEvent
    contact_name: str | None
    venue_name: str | None
    area_name: str | None
    status: str
    edited_since_confirmed: bool
    min_spend_cents: int | None
    food_cents: int
    expected_spend_cents: int


def list_events(
    db: Session,
    ctx: FunctionsCtx,
    *,
    venue_ids: list[str] | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    statuses: list[str] | None = None,
    include_cancelled: bool = False,
    booking_types: list[str] | None = None,
    enquiry_id: str | None = None,
    contact_id: str | None = None,
    q: str | None = None,
    sort: str = "date_asc",
    limit: int = 200,
    offset: int = 0,
) -> list[EventRow]:
    """Events with derived status and expected spend, filtered in SQL.
    ``date_to`` is inclusive. Cancelled events are left out unless asked for
    (``include_cancelled`` or ``'cancelled'`` in ``statuses``).

    A ``statuses`` filter derives every event's status in SQL and filters on
    it; otherwise the page is chosen from the events alone and only its rows'
    status and money are derived."""
    ev = FnEvent
    if sort == "date_asc":
        order = (ev.event_date.asc(), ev.start_time.asc().nulls_last())
    elif sort == "date_desc":
        order = (ev.event_date.desc(), ev.start_time.desc().nulls_last())
    elif sort == "created_desc":
        order = (ev.created_at.desc(),)
    else:
        raise Invalid(f"Unknown sort '{sort}'.")
    filters = [
        ev.organization_id == ctx.org_id,
        ctx.venue_clause(ev.venue_id),
        ctx.record_clause(ev),
    ]
    if venue_ids:
        filters.append(ev.venue_id.in_([v for v in venue_ids if v in ctx.venue_ids]))
    if date_from:
        filters.append(ev.event_date >= date_from)
    if date_to:
        filters.append(ev.event_date <= date_to)
    if not statuses and not include_cancelled:
        filters.append(ev.cancelled_at.is_(None))
    if booking_types:
        filters.append(ev.booking_type.in_(booking_types))
    if enquiry_id:
        filters.append(ev.enquiry_id == enquiry_id)
    if contact_id:
        filters.append(
            or_(ev.contact_id == contact_id, ev.invoice_contact_id == contact_id)
        )
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        filters.append(
            or_(func.lower(ev.name).like(like), func.lower(FnContact.name).like(like))
        )

    def page(query):
        return (
            query.join(
                FnContact,
                and_(
                    FnContact.organization_id == ev.organization_id,
                    FnContact.id == ev.contact_id,
                ),
            )
            .join(Venue, Venue.id == ev.venue_id)
            .outerjoin(
                FnVenueArea,
                and_(
                    FnVenueArea.organization_id == ev.organization_id,
                    FnVenueArea.id == ev.area_id,
                ),
            )
            .filter(*filters)
            .order_by(*order, ev.id)
            .offset(max(0, offset))
            .limit(min(limit, 2000))
        )

    base = (ev, FnContact.name, Venue.name, FnVenueArea.name)
    if statuses:
        st = status_svc.event_status_select(ctx.org_id).subquery("est")
        mo = money_svc.event_money_select(ctx.org_id).subquery("emo")
        query = (
            db.query(
                *base,
                st.c.status,
                st.c.edited_since_confirmed,
                mo.c.min_spend_cents,
                mo.c.food_cents,
                mo.c.expected_spend_cents,
            )
            .join(st, st.c.event_id == ev.id)
            .join(mo, mo.c.event_id == ev.id)
            .filter(st.c.status.in_(statuses))
        )
        return [EventRow(*r) for r in page(query).all()]

    rows = page(db.query(*base)).all()
    if not rows:
        return []
    ids = [r[0].id for r in rows]
    states = {
        r.event_id: r
        for r in db.execute(status_svc.event_status_select(ctx.org_id, event_ids=ids))
    }
    sums = {
        r.event_id: r
        for r in db.execute(money_svc.event_money_select(ctx.org_id, event_ids=ids))
    }
    return [
        EventRow(
            *r,
            states[r[0].id].status,
            bool(states[r[0].id].edited_since_confirmed),
            sums[r[0].id].min_spend_cents,
            sums[r[0].id].food_cents,
            sums[r[0].id].expected_spend_cents,
        )
        for r in rows
    ]
