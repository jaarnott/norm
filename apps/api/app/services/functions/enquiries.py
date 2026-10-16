"""Enquiries — create, edit, close/reopen, archive, follow-ups, mail threads.

An enquiry's stage and next action are never set here; they follow from the
facts other actions record (status.py). What this module writes are the
enquiry's own fields and two kinds of fact: closures (lost / reopened, each
with a reason) and thread links.

A group-wide enquiry (``venue_id`` NULL) is visible to everyone in the
organisation; otherwise the caller needs the venue.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from app.db.functions_models import (
    BOOKING_TYPES,
    ENQUIRY_SOURCES,
    FnContact,
    FnContactEmail,
    FnEnquiry,
    FnEnquiryClosure,
    FnEvent,
    FnMailboxAccount,
    FnThreadLink,
)
from app.db.models import Venue, _now
from app.services.functions import contacts as contacts_svc
from app.services.functions import status as status_svc
from app.services.functions._rows import apply_fields, check_version, load, require_text
from app.services.functions.ctx import (
    FunctionsCtx,
    require_live,
    require_org_member,
    require_staff,
    require_venue,
)
from app.services.functions.errors import Conflict, Invalid, NotFound, flush
from app.services.functions.timeutil import validate_window

#: What update_enquiry may change. Stage-bearing facts are not here on
#: purpose: they are recorded by the actions that cause them.
ENQUIRY_FIELDS = (
    "contact_id",
    "venue_id",
    "booking_type",
    "subject",
    "guests",
    "preferred_date_start",
    "preferred_date_end",
    "preferred_start_time",
    "preferred_end_time",
    "preferred_ends_next_day",
    "source",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "referrer",
    "landing_page",
    "owner_user_id",
    "follow_up_date",
    "follow_up_reason",
    "client_note",
    "notes",
)
ATTRIBUTION_FIELDS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "referrer",
    "landing_page",
)

#: Orbit's rule, shown to staff and the agent alike when reopening.
REOPEN_RULE = (
    "Reopen only when the client has said they want to proceed — a new date, a "
    'revised quote, "we would like to go ahead". A reply explaining why we lost '
    'it, a polite thank-you, or "maybe next year" is not a reason to reopen: '
    "leave it lost and raise a new enquiry when that event becomes real."
)


def visible_enquiries(db: Session, ctx: FunctionsCtx):
    """Base query: the org's enquiries at the caller's venues (+ group-wide)."""
    return db.query(FnEnquiry).filter(
        FnEnquiry.organization_id == ctx.org_id,
        ctx.venue_clause(FnEnquiry.venue_id, allow_null=True),
        ctx.record_clause(FnEnquiry),
    )


def get_enquiry(
    db: Session, ctx: FunctionsCtx, enquiry_id: str, *, for_update: bool = False
) -> FnEnquiry:
    return load(
        db,
        ctx,
        FnEnquiry,
        enquiry_id,
        label="Enquiry",
        allow_null_venue=True,
        for_update=for_update,
    )


def _validate(
    db: Session, ctx: FunctionsCtx, fields: dict, *, check_contact: bool = True
) -> None:
    if (
        fields.get("booking_type") is not None
        and fields["booking_type"] not in BOOKING_TYPES
    ):
        raise Invalid(f"Booking type must be one of {', '.join(BOOKING_TYPES)}.")
    if "source" in fields and fields["source"] not in ENQUIRY_SOURCES:
        raise Invalid(f"Source must be one of {', '.join(ENQUIRY_SOURCES)}.")
    if "venue_id" in fields and fields["venue_id"] is not None:
        require_venue(ctx, fields["venue_id"])
    if check_contact and fields.get("contact_id") is not None:
        contacts_svc.get_contact(db, ctx, fields["contact_id"])
    if fields.get("owner_user_id") is not None:
        require_org_member(db, ctx, fields["owner_user_id"])
    guests = fields.get("guests")
    if guests is not None and int(guests) < 0:
        raise Invalid("Guests cannot be negative.")
    start, end = fields.get("preferred_date_start"), fields.get("preferred_date_end")
    if start and end and end < start:
        raise Invalid("The preferred date range ends before it starts.")


def create_enquiry(
    db: Session,
    ctx: FunctionsCtx,
    *,
    contact_id: str | None = None,
    contact: dict | None = None,
    venue_id: str | None = None,
    booking_type: str | None = None,
    subject: str | None = None,
    guests: int | None = None,
    preferred_date_start: dt.date | None = None,
    preferred_date_end: dt.date | None = None,
    preferred_start_time: dt.time | None = None,
    preferred_end_time: dt.time | None = None,
    preferred_ends_next_day: bool = False,
    source: str = "manual",
    attribution: dict | None = None,
    owner_user_id: str | None = None,
    follow_up_date: dt.date | None = None,
    follow_up_reason: str | None = None,
    client_note: str | None = None,
    notes: str | None = None,
) -> FnEnquiry:
    """A new enquiry for ``contact_id``, or for ``contact`` (``{name, email,
    phone, company}`` — matched to an existing contact by email, else
    created). ``attribution`` carries a form's source fields (utm_*, gclid,
    referrer, landing_page)."""
    require_live(ctx)
    require_staff(ctx)
    if contact_id is None and not contact:
        raise Invalid("An enquiry needs a contact.")
    fields = {
        "contact_id": contact_id,
        "venue_id": venue_id,
        "booking_type": booking_type,
        "guests": guests,
        "preferred_date_start": preferred_date_start,
        "preferred_date_end": preferred_date_end,
        "source": source,
        "owner_user_id": owner_user_id,
    }
    # A contact_id the caller picked must be one they can see; contact
    # details are matched by email instead — to the org's contact for that
    # address even when another venue holds it (contacts.py's module note),
    # so the answer never reveals whether another venue knows this customer.
    _validate(db, ctx, fields, check_contact=contact_id is not None)
    validate_window(preferred_start_time, preferred_end_time, preferred_ends_next_day)
    extra = {k: v for k, v in (attribution or {}).items() if v not in (None, "")}
    unknown = set(extra) - set(ATTRIBUTION_FIELDS)
    if unknown:
        raise Invalid(f"Unknown attribution fields: {', '.join(sorted(unknown))}.")
    if contact_id is None:
        found, _ = contacts_svc.find_or_create_contact(
            db,
            ctx,
            email=contact.get("email"),
            name=contact.get("name"),
            phone=contact.get("phone"),
            company=contact.get("company"),
            source="web_form" if source == "web_form" else "manual",
        )
        fields["contact_id"] = found.id
    enquiry = FnEnquiry(
        organization_id=ctx.org_id,
        subject=(subject or "").strip() or None,
        preferred_start_time=preferred_start_time,
        preferred_end_time=preferred_end_time,
        preferred_ends_next_day=bool(preferred_ends_next_day),
        follow_up_date=follow_up_date,
        follow_up_reason=follow_up_reason,
        client_note=client_note,
        notes=notes,
        created_by_user_id=ctx.user_id,
        created_by_label=ctx.actor_label,
        **fields,
        **extra,
    )
    db.add(enquiry)
    flush(db)
    return enquiry


def update_enquiry(
    db: Session,
    ctx: FunctionsCtx,
    enquiry_id: str,
    *,
    expected_version: int | None = None,
    **fields,
) -> dict:
    """Edit fields (ENQUIRY_FIELDS). Returns ``{field: [old, new]}``. Moving a
    booked enquiry to another venue is refused — the event holds the venue."""
    require_live(ctx)
    require_staff(ctx)
    enquiry = get_enquiry(db, ctx, enquiry_id)
    check_version(enquiry, expected_version)
    merged = {
        "preferred_date_start": enquiry.preferred_date_start,
        "preferred_date_end": enquiry.preferred_date_end,
        **fields,
    }
    _validate(db, ctx, merged | {"source": fields.get("source", enquiry.source)})
    validate_window(
        fields.get("preferred_start_time", enquiry.preferred_start_time),
        fields.get("preferred_end_time", enquiry.preferred_end_time),
        fields.get("preferred_ends_next_day", enquiry.preferred_ends_next_day),
    )
    if (
        "venue_id" in fields
        and fields["venue_id"] != enquiry.venue_id
        and _has_live_event(db, ctx, enquiry.id)
    ):
        raise Conflict("This enquiry is booked — change the event's venue instead.")
    if "subject" in fields:
        fields["subject"] = (fields["subject"] or "").strip() or None
    changes = apply_fields(enquiry, fields, ENQUIRY_FIELDS)
    flush(db)
    return changes


def _latest_closure(db: Session, ctx: FunctionsCtx, enquiry_id: str):
    return (
        db.query(FnEnquiryClosure)
        .filter(
            FnEnquiryClosure.organization_id == ctx.org_id,
            FnEnquiryClosure.enquiry_id == enquiry_id,
        )
        .order_by(
            FnEnquiryClosure.occurred_at.desc(),
            FnEnquiryClosure.created_at.desc(),
            FnEnquiryClosure.id.desc(),
        )
        .first()
    )


def _has_live_event(db: Session, ctx: FunctionsCtx, enquiry_id: str) -> bool:
    return (
        db.query(FnEvent.id)
        .filter(
            FnEvent.organization_id == ctx.org_id,
            FnEvent.enquiry_id == enquiry_id,
            FnEvent.cancelled_at.is_(None),
        )
        .first()
        is not None
    )


def record_closure(
    db: Session, ctx: FunctionsCtx, enquiry: FnEnquiry, kind: str, reason: str
) -> FnEnquiryClosure:
    """Write a closure fact (no checks — ``close_enquiry``/``reopen_enquiry``
    and event cancellation call this once they have decided)."""
    row = FnEnquiryClosure(
        organization_id=ctx.org_id,
        enquiry_id=enquiry.id,
        kind=kind,
        reason=reason,
        actor_user_id=ctx.user_id,
        actor_label=ctx.actor_label,
        occurred_at=_now(),
    )
    db.add(row)
    # Touch the enquiry so its version moves: a page holding the old version
    # must reload before editing a closed enquiry.
    enquiry.updated_at = _now()
    flush(db)
    return row


def close_enquiry(
    db: Session, ctx: FunctionsCtx, enquiry_id: str, *, reason: str
) -> FnEnquiryClosure:
    """Mark lost, with a reason. A booked enquiry is not "lost" — cancel its
    event instead."""
    require_live(ctx)
    require_staff(ctx)
    enquiry = get_enquiry(db, ctx, enquiry_id, for_update=True)
    text = require_text(reason, "A reason")
    latest = _latest_closure(db, ctx, enquiry.id)
    if latest is not None and latest.kind == "lost":
        raise Conflict("This enquiry is already closed.")
    if _has_live_event(db, ctx, enquiry.id):
        raise Conflict(
            "This enquiry is booked — cancel the event instead of closing it."
        )
    return record_closure(db, ctx, enquiry, "lost", text)


def reopen_enquiry(
    db: Session, ctx: FunctionsCtx, enquiry_id: str, *, reason: str
) -> FnEnquiryClosure:
    """Reopen a lost enquiry, with a reason (see REOPEN_RULE). It lands on
    whatever stage its facts give — never blindly "new"."""
    require_live(ctx)
    require_staff(ctx)
    enquiry = get_enquiry(db, ctx, enquiry_id, for_update=True)
    text = require_text(reason, "A reason")
    latest = _latest_closure(db, ctx, enquiry.id)
    if latest is None or latest.kind != "lost":
        raise Conflict("This enquiry is not closed.")
    return record_closure(db, ctx, enquiry, "reopened", text)


def closures(db: Session, ctx: FunctionsCtx, enquiry_id: str) -> list[FnEnquiryClosure]:
    get_enquiry(db, ctx, enquiry_id)
    return (
        db.query(FnEnquiryClosure)
        .filter(
            FnEnquiryClosure.organization_id == ctx.org_id,
            FnEnquiryClosure.enquiry_id == enquiry_id,
        )
        .order_by(FnEnquiryClosure.occurred_at, FnEnquiryClosure.created_at)
        .all()
    )


def archive_enquiry(db: Session, ctx: FunctionsCtx, enquiry_id: str) -> FnEnquiry:
    """Delete, as far as anyone can tell: hidden from lists, facts kept."""
    require_live(ctx)
    require_staff(ctx)
    enquiry = get_enquiry(db, ctx, enquiry_id)
    if enquiry.archived_at is None:
        enquiry.archived_at = _now()
        flush(db)
    return enquiry


def unarchive_enquiry(db: Session, ctx: FunctionsCtx, enquiry_id: str) -> FnEnquiry:
    require_live(ctx)
    require_staff(ctx)
    enquiry = get_enquiry(db, ctx, enquiry_id)
    if enquiry.archived_at is not None:
        enquiry.archived_at = None
        flush(db)
    return enquiry


def set_follow_up(
    db: Session,
    ctx: FunctionsCtx,
    enquiry_id: str,
    *,
    date: dt.date,
    reason: str | None = None,
) -> FnEnquiry:
    """The customer asked us to come back on ``date`` (venue-local)."""
    require_live(ctx)
    require_staff(ctx)
    enquiry = get_enquiry(db, ctx, enquiry_id)
    enquiry.follow_up_date = date
    enquiry.follow_up_reason = (reason or "").strip() or None
    flush(db)
    return enquiry


def clear_follow_up(db: Session, ctx: FunctionsCtx, enquiry_id: str) -> FnEnquiry:
    require_live(ctx)
    require_staff(ctx)
    enquiry = get_enquiry(db, ctx, enquiry_id)
    enquiry.follow_up_date = None
    enquiry.follow_up_reason = None
    flush(db)
    return enquiry


# ---------------------------------------------------------------------------
# Mail threads
# ---------------------------------------------------------------------------


def _visible_account(
    db: Session, ctx: FunctionsCtx, account_id: str
) -> FnMailboxAccount:
    return load(db, ctx, FnMailboxAccount, account_id, label="Mailbox")


def link_thread(
    db: Session,
    ctx: FunctionsCtx,
    *,
    account_id: str,
    gmail_thread_id: str,
    enquiry_id: str | None = None,
    event_id: str | None = None,
) -> FnThreadLink:
    """Attach a mailbox thread to an enquiry or an event (exactly one).
    Linking twice returns the live link. Mail on linked threads is what makes
    an enquiry "replied" and drives its next action."""
    require_live(ctx)
    require_staff(ctx)
    if (enquiry_id is None) == (event_id is None):
        raise Invalid("Link a thread to an enquiry or to an event.")
    _visible_account(db, ctx, account_id)
    thread_id = require_text(gmail_thread_id, "A thread")
    if enquiry_id is not None:
        get_enquiry(db, ctx, enquiry_id)
    else:
        load(db, ctx, FnEvent, event_id, label="Event")
    existing = (
        db.query(FnThreadLink)
        .filter(
            FnThreadLink.organization_id == ctx.org_id,
            FnThreadLink.account_id == account_id,
            FnThreadLink.gmail_thread_id == thread_id,
            FnThreadLink.enquiry_id == enquiry_id
            if enquiry_id
            else FnThreadLink.event_id == event_id,
            FnThreadLink.unlinked_at.is_(None),
        )
        .first()
    )
    if existing is not None:
        return existing
    link = FnThreadLink(
        organization_id=ctx.org_id,
        account_id=account_id,
        gmail_thread_id=thread_id,
        enquiry_id=enquiry_id,
        event_id=event_id,
        linked_by_user_id=ctx.user_id,
        linked_by_label=ctx.actor_label,
        linked_at=_now(),
    )
    db.add(link)
    flush(db)
    return link


def unlink_thread(db: Session, ctx: FunctionsCtx, link_id: str) -> FnThreadLink:
    """Soft-unlink: the row stays, its facts stop counting."""
    require_live(ctx)
    require_staff(ctx)
    link = load(db, ctx, FnThreadLink, link_id, label="Thread link", venue_attr=None)
    # The mailbox first, then the record: a link is the caller's to undo only
    # if they can see both ends (security review, Oct 2026). Every miss is
    # the same NotFound.
    try:
        _visible_account(db, ctx, link.account_id)
        if link.enquiry_id:
            get_enquiry(db, ctx, link.enquiry_id)
        else:
            load(db, ctx, FnEvent, link.event_id, label="Event")
    except NotFound:
        raise NotFound("Thread link not found.") from None
    if link.unlinked_at is None:
        link.unlinked_at = _now()
        link.unlinked_by_user_id = ctx.user_id
        flush(db)
    return link


def thread_links(
    db: Session,
    ctx: FunctionsCtx,
    *,
    enquiry_id: str | None = None,
    event_id: str | None = None,
    include_unlinked: bool = False,
) -> list[FnThreadLink]:
    if enquiry_id:
        get_enquiry(db, ctx, enquiry_id)
        cond = FnThreadLink.enquiry_id == enquiry_id
    elif event_id:
        load(db, ctx, FnEvent, event_id, label="Event")
        cond = FnThreadLink.event_id == event_id
    else:
        raise Invalid("Name an enquiry or an event.")
    q = db.query(FnThreadLink).filter(FnThreadLink.organization_id == ctx.org_id, cond)
    if not include_unlinked:
        q = q.filter(FnThreadLink.unlinked_at.is_(None))
    return q.order_by(FnThreadLink.linked_at).all()


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


@dataclass
class EnquiryRow:
    enquiry: FnEnquiry
    contact_name: str | None
    contact_company: str | None
    venue_name: str | None
    stage: str
    next_action: str
    next_action_due: dt.date | None
    next_action_overdue: bool
    next_action_due_today: bool
    last_inbound_at: dt.datetime | None
    last_outbound_at: dt.datetime | None
    quote_sent_at: dt.datetime | None


def _text_key(column):
    """A text sort key as F3's row sorts compare it (reports.sort_rows):
    trimmed, lower-cased, blank -> NULL (sorted last), compared by code point
    (COLLATE "C") the way Python compares strings."""
    trimmed = func.nullif(func.btrim(column, " \t\n\r\f\v"), "")
    return func.lower(trimmed).collate("C")


def _stage_rank(stage_column):
    """Pipeline order (status.STAGES)."""
    return case(
        {stage: i for i, stage in enumerate(status_svc.STAGES)},
        value=stage_column,
        else_=len(status_svc.STAGES),
    )


def _by(key, descending: bool):
    """Blanks last in both directions."""
    return (key.desc() if descending else key.asc()).nulls_last()


#: Every sort ends with ``created_at desc, id`` (newest first, a total order,
#: so pages never overlap or skip) — ``page`` adds the ``id``.
_SORTS = {
    "created_desc": lambda e, s: (e.created_at.desc(),),
    "created_asc": lambda e, s: (e.created_at.asc(),),
    "due": lambda e, s: (s.c.next_action_due.asc().nulls_last(), e.created_at.desc()),
    "preferred_date": lambda e, s: (
        e.preferred_date_start.asc().nulls_last(),
        e.created_at.desc(),
    ),
    "last_activity": lambda e, s: (
        func.greatest(s.c.last_inbound_at, s.c.last_outbound_at).desc().nulls_last(),
        e.created_at.desc(),
    ),
}
for _desc in (False, True):
    _suffix = "desc" if _desc else "asc"
    _SORTS[f"contact_{_suffix}"] = lambda e, s, d=_desc: (
        _by(_text_key(FnContact.name), d),
        e.created_at.desc(),
    )
    _SORTS[f"subject_{_suffix}"] = lambda e, s, d=_desc: (
        _by(_text_key(e.subject), d),
        e.created_at.desc(),
    )
    _SORTS[f"guests_{_suffix}"] = lambda e, s, d=_desc: (
        _by(e.guests, d),
        e.created_at.desc(),
    )
    _SORTS[f"stage_{_suffix}"] = lambda e, s, d=_desc: (
        _by(_stage_rank(s.c.stage), d),
        e.created_at.desc(),
    )
#: The sort keys ``list_enquiries`` accepts.
SORTS = frozenset(_SORTS)


#: Sorts that read derived columns, so need the status of every enquiry.
_DERIVED_SORTS = frozenset({"due", "last_activity", "stage_asc", "stage_desc"})
#: EnquiryRow's derived fields, in order, as the status select names them.
_STATUS_COLUMNS = (
    "stage",
    "next_action",
    "next_action_due",
    "next_action_overdue",
    "next_action_due_today",
    "last_inbound_at",
    "last_outbound_at",
    "quote_sent_at",
)


def _list_filters(
    db,
    ctx,
    e,
    *,
    venue_ids,
    sources,
    booking_types,
    owner_user_id,
    contact_id,
    created_from,
    created_to,
    preferred_from,
    preferred_to,
    q,
    include_archived,
) -> list:
    """The list's filters that read the enquiry and its contact only."""
    filters = [
        e.organization_id == ctx.org_id,
        ctx.venue_clause(e.venue_id, allow_null=True),
        ctx.record_clause(e),
    ]
    if venue_ids:
        allowed = [v for v in venue_ids if v in ctx.venue_ids]
        filters.append(or_(e.venue_id.is_(None), e.venue_id.in_(allowed)))
    if sources:
        filters.append(e.source.in_(sources))
    if booking_types:
        filters.append(e.booking_type.in_(booking_types))
    if owner_user_id:
        filters.append(e.owner_user_id == owner_user_id)
    if contact_id:
        filters.append(e.contact_id == contact_id)
    if created_from:
        filters.append(e.created_at >= created_from)
    if created_to:
        filters.append(e.created_at < created_to)
    if preferred_from:
        filters.append(
            func.coalesce(e.preferred_date_end, e.preferred_date_start)
            >= preferred_from
        )
    if preferred_to:
        filters.append(e.preferred_date_start <= preferred_to)
    if not include_archived:
        filters.append(e.archived_at.is_(None))
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        email_match = (
            db.query(FnContactEmail.id)
            .filter(
                FnContactEmail.organization_id == e.organization_id,
                FnContactEmail.contact_id == e.contact_id,
                func.lower(FnContactEmail.email).like(like),
            )
            .exists()
        )
        filters.append(
            or_(
                func.lower(func.coalesce(e.subject, "")).like(like),
                func.lower(FnContact.name).like(like),
                func.lower(func.coalesce(FnContact.company, "")).like(like),
                email_match,
            )
        )
    return filters


def list_enquiries(
    db: Session,
    ctx: FunctionsCtx,
    *,
    now: dt.datetime | None = None,
    venue_ids: list[str] | None = None,
    stages: list[str] | None = None,
    next_actions: list[str] | None = None,
    due_only: bool = False,
    sources: list[str] | None = None,
    booking_types: list[str] | None = None,
    owner_user_id: str | None = None,
    contact_id: str | None = None,
    created_from: dt.datetime | None = None,
    created_to: dt.datetime | None = None,
    preferred_from: dt.date | None = None,
    preferred_to: dt.date | None = None,
    q: str | None = None,
    include_archived: bool = False,
    sort: str = "created_desc",
    limit: int = 100,
    offset: int = 0,
) -> list[EnquiryRow]:
    """The enquiries list, filtered and sorted by derived stage / next action
    in SQL. ``due_only`` keeps actions due today or overdue (the "needs
    attention" set). ``venue_ids`` narrows within the caller's venues (a
    group-wide enquiry matches any).

    Two shapes, same rows: when a stage / next-action filter or a derived
    sort is asked for, every enquiry's status is derived in SQL and the list
    filters on it; otherwise the page is chosen from the enquiries alone and
    only its rows' status is derived (the default list then costs a page,
    not the whole organisation)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    order = _SORTS.get(sort)
    if order is None:
        raise Invalid(f"Unknown sort '{sort}'.")
    e = FnEnquiry
    filters = _list_filters(
        db,
        ctx,
        e,
        venue_ids=venue_ids,
        sources=sources,
        booking_types=booking_types,
        owner_user_id=owner_user_id,
        contact_id=contact_id,
        created_from=created_from,
        created_to=created_to,
        preferred_from=preferred_from,
        preferred_to=preferred_to,
        q=q,
        include_archived=include_archived,
    )
    valid_tz = status_svc.valid_timezones(db, ctx.org_id)

    def page(query, s=None):
        return (
            query.join(
                FnContact,
                and_(
                    FnContact.organization_id == e.organization_id,
                    FnContact.id == e.contact_id,
                ),
            )
            .outerjoin(Venue, Venue.id == e.venue_id)
            .filter(*filters)
            .order_by(*order(e, s), e.id)
            .offset(max(0, offset))
            .limit(min(limit, 1000))
        )

    base = (e, FnContact.name, FnContact.company, Venue.name)
    if stages or next_actions or due_only or sort in _DERIVED_SORTS:
        s = status_svc.enquiry_status_select(
            ctx.org_id, now, valid_tz=valid_tz
        ).subquery("st")
        query = db.query(*base, *(s.c[c] for c in _STATUS_COLUMNS)).join(
            s, s.c.enquiry_id == e.id
        )
        if stages:
            query = query.filter(s.c.stage.in_(stages))
        if next_actions:
            query = query.filter(s.c.next_action.in_(next_actions))
        if due_only:
            query = query.filter(
                or_(
                    s.c.next_action_overdue.is_(True),
                    s.c.next_action_due_today.is_(True),
                )
            )
        return [EnquiryRow(*r) for r in page(query, s).all()]

    rows = page(db.query(*base)).all()
    if not rows:
        return []
    derived = {
        r.enquiry_id: r
        for r in db.execute(
            status_svc.enquiry_status_select(
                ctx.org_id, now, valid_tz=valid_tz, enquiry_ids=[r[0].id for r in rows]
            )
        )
    }
    return [
        EnquiryRow(*r, *(getattr(derived[r[0].id], c) for c in _STATUS_COLUMNS))
        for r in rows
    ]


def enquiry_state(db: Session, ctx: FunctionsCtx, enquiry_id: str, now=None):
    """``(stage, NextAction)`` for one visible enquiry."""
    get_enquiry(db, ctx, enquiry_id)
    return status_svc.enquiry_status(db, ctx.org_id, enquiry_id, now)


def require_open_enquiry(db: Session, ctx: FunctionsCtx, enquiry: FnEnquiry) -> None:
    """Refuse work on a lost enquiry (reopen it first)."""
    latest = _latest_closure(db, ctx, enquiry.id)
    if latest is not None and latest.kind == "lost":
        raise Conflict("This enquiry is closed — reopen it first.")


def get_enquiry_or_none(db: Session, ctx: FunctionsCtx, enquiry_id: str | None):
    if not enquiry_id:
        return None
    try:
        return get_enquiry(db, ctx, enquiry_id)
    except NotFound:
        return None
