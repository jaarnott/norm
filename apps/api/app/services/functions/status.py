"""Derived state — enquiry stage, next action, event status, min-spend state.

Nothing in Functions stores a status. Each one is a function of recorded
facts, implemented here twice: as SQL (``*_status_select``) so lists can
filter and sort by it, and in Python (``enquiry_stage``, ``next_action``,
``event_status``, ``min_spend_state``) for single records, agent tools and
tests. ``tests/test_functions_foundation.py`` asserts the two agree on a
fixture set; change one, change both.

**Why SQLAlchemy expressions and not a database view.** The next action
depends on *now* and on each venue's timezone, which a view cannot take as
parameters; a view would also be frozen in a migration (every rule change a
new migration) and absent from test databases built with ``create_all``. As
expressions, the rules live beside their Python twins, compose with any
query (``WHERE stage = 'new' ORDER BY next_action_due``) and take ``now`` as a
bound parameter.

The facts each derivation reads — and therefore what other parts of Norm must
WRITE for the state to move:

Enquiry stage (``lost`` > ``confirmed`` > ``quoted`` > ``replied`` > ``new``)
    lost       latest ``fn_enquiry_closures`` row has kind ``lost``
    confirmed  an ``fn_events`` row with this ``enquiry_id`` and no
               ``cancelled_at``
    quoted     an ``fn_proposal_events`` row (kind ``sent``) for the enquiry
    replied    an outbound ``fn_mailbox_messages`` row (``is_inbound`` false,
               not internal, not an auto-reply) on a thread with a live
               ``fn_thread_links`` row to the enquiry
    new        none of the above

Next action (open enquiries only; Orbit's ``nextAction.ts``)
    requested      ``follow_up_date`` set — parks everything else
    reply_overdue  the customer wrote last (latest linked inbound is newer
                   than the latest outbound) more than 24 hours ago
    day_14         client silent 14+ days (since their last message, or the
                   enquiry's creation) — call and close
    day_7          our last reply (or creation) 7+ days ago — follow up
    none           otherwise, or no linked mail at all

Event status (``cancelled`` > ``confirmed`` > ``in_progress`` >
``sent_to_client`` > ``booked``)
    cancelled       ``fn_events.cancelled_at``
    confirmed       the latest ``fn_event_section_confirmations`` row for each
                    of details / beverages / food is ``confirmed`` or ``skipped``
    in_progress     the client has edited (an ``fn_event_changes`` row of kind
                    ``edited`` with actor_kind ``client``) — Orbit's rule
                    exactly. Confirming or skipping a section without an edit
                    does not count: the event stays sent_to_client (or booked)
                    until all three are done.
    sent_to_client  an ``fn_event_changes`` row of kind ``link_sent``
    plus ``edited_since_confirmed``: a client edit after the moment the last
    section was confirmed.

Minimum spend (``confirmed`` | ``withdrawn`` | ``declined`` > ``approved`` >
``proposed``)
    confirmed  ``event_id`` set (the customer's confirmation created the event)
    withdrawn  ``withdrawn_at`` set
    declined   latest ``fn_min_spend_responses`` row is ``decline``
    approved   latest ``fn_min_spend_approvals`` row is ``approve``/``override``
               AND its ``terms_key`` still matches the option's terms (amount,
               date, times, area, venue) — an approval covers exact terms
    Confirmable: not confirmed or withdrawn, and approved — or in an area with
    ``requires_approval`` false. No area means approval is required.

"Latest" always orders by ``occurred_at``, then ``created_at``, then ``id``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import (
    Date,
    DateTime,
    String,
    and_,
    case,
    cast,
    column,
    func,
    literal,
    or_,
    select,
    table,
    true,
)
from sqlalchemy.orm import Session, aliased

from app.db.functions_models import (
    EVENT_SECTIONS,
    FnEnquiry,
    FnEnquiryClosure,
    FnEvent,
    FnEventChange,
    FnEventSectionConfirmation,
    FnMailboxMessage,
    FnMinSpend,
    FnMinSpendApproval,
    FnMinSpendResponse,
    FnProposalEvent,
    FnThreadLink,
    FnVenueArea,
)
from app.db.models import Organization, Venue

STAGES = ("new", "replied", "quoted", "confirmed", "lost")
OPEN_STAGES = ("new", "replied", "quoted")
EVENT_STATUSES = ("booked", "sent_to_client", "in_progress", "confirmed", "cancelled")
MIN_SPEND_STATES = ("proposed", "approved", "confirmed", "declined", "withdrawn")
NEXT_ACTION_KINDS = ("requested", "reply_overdue", "day_14", "day_7", "none")

NEXT_ACTION_LABELS = {
    "requested": "Requested follow-up",
    "reply_overdue": "Reply to the customer",
    "day_14": "Call and close",
    "day_7": "Send Day 7 follow-up",
    "none": "Nothing due",
}

REPLY_WITHIN = dt.timedelta(hours=24)
CALL_AND_CLOSE_AFTER = dt.timedelta(days=14)
FOLLOW_UP_AFTER = dt.timedelta(days=7)

_DONE = ("confirmed", "skipped")
_APPROVED = ("approve", "override")
_pg_timezones = table("pg_timezone_names", column("name"))


def _tz_name(venue_tz: str | None, org_tz: str | None) -> str:
    """The zone an enquiry's dates are judged in: its venue's, else the
    company default (group-wide enquiries), else UTC. An invalid name falls
    through rather than failing a list."""
    for name in (venue_tz, org_tz):
        if not name:
            continue
        try:
            ZoneInfo(name)
            return name
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return "UTC"


def valid_timezones(db: Session, org_id: str) -> tuple[str, ...]:
    """The zone names an org's enquiries can be judged in that Python accepts
    — its venues' and its default. Passing these to the SQL builders avoids
    reading ``pg_timezone_names`` (~45 ms a scan) on every list query."""
    names = db.execute(
        select(Venue.timezone)
        .where(Venue.organization_id == org_id, Venue.timezone.is_not(None))
        .union(select(Organization.default_timezone).where(Organization.id == org_id))
    ).scalars()
    return tuple(sorted(n for n in set(names) if n and _tz_name(n, None) == n))


def _sql_tz(venue_tz_col, org_tz_col, valid: tuple[str, ...] | None = None):
    """SQL twin of ``_tz_name``: ``timezone()`` raises on an unknown zone, so
    a bad venue value must fall back instead of breaking the whole list.
    ``valid`` (from ``valid_timezones``) replaces the catalogue lookup."""
    names = (
        list(valid)
        if valid is not None
        else select(_pg_timezones.c.name).scalar_subquery()
    )
    return case(
        (venue_tz_col.in_(names), venue_tz_col),
        (org_tz_col.in_(names), org_tz_col),
        else_=literal("UTC"),
    )


def _local_date(tz, ts):
    return cast(func.timezone(tz, ts), Date)


def _hours(n: int):
    """An interval of exact hours — ``interval 'N days'`` would follow DST in
    the session zone, Python's timedelta never does."""
    return func.make_interval(0, 0, 0, 0, n)


def _latest_key(model):
    return (model.occurred_at.desc(), model.created_at.desc(), model.id.desc())


def _py_latest(rows):
    return (
        max(rows, key=lambda r: (r.occurred_at, r.created_at, r.id)) if rows else None
    )


# ===========================================================================
# Enquiries
# ===========================================================================


@dataclass(frozen=True)
class EnquiryFacts:
    enquiry_id: str
    created_at: dt.datetime
    follow_up_date: dt.date | None
    follow_up_reason: str | None
    closure_kind: str | None
    has_live_event: bool
    quote_sent_at: dt.datetime | None
    last_inbound_at: dt.datetime | None
    last_outbound_at: dt.datetime | None
    tz: str = "UTC"


@dataclass(frozen=True)
class NextAction:
    kind: str
    label: str
    due_date: dt.date | None
    overdue: bool
    due_today: bool
    why: str
    #: "customer" when they asked for it, "policy" when derived, None.
    source: str | None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "overdue": self.overdue,
            "due_today": self.due_today,
            "why": self.why,
            "source": self.source,
        }


def enquiry_stage(f: EnquiryFacts) -> str:
    if f.closure_kind == "lost":
        return "lost"
    if f.has_live_event:
        return "confirmed"
    if f.quote_sent_at is not None:
        return "quoted"
    if f.last_outbound_at is not None:
        return "replied"
    return "new"


def _fmt(d: dt.date) -> str:
    return f"{d.day} {d.strftime('%b')}"


def _none(why: str) -> NextAction:
    return NextAction("none", NEXT_ACTION_LABELS["none"], None, False, False, why, None)


def next_action(f: EnquiryFacts, now: dt.datetime) -> NextAction:
    """What we owe this enquiry — Orbit's ``resolveNextAction``, with dates
    judged in the venue's timezone rather than the browser's."""
    tz = ZoneInfo(f.tz)
    today = now.astimezone(tz).date()
    if enquiry_stage(f) not in OPEN_STAGES:
        return _none("This enquiry is closed.")

    if f.follow_up_date is not None:
        due = f.follow_up_date
        reason = (f.follow_up_reason or "").strip()
        tail = f" — {reason}" if reason else ""
        why = f"The customer asked us to follow up on {_fmt(due)}{tail}."
        if due > today:
            why += " Everything else is parked until then."
        return NextAction(
            "requested",
            NEXT_ACTION_LABELS["requested"],
            due,
            due < today,
            due == today,
            why,
            "customer",
        )

    inbound, outbound = f.last_inbound_at, f.last_outbound_at
    if inbound is None and outbound is None:
        return _none("No emails are linked to this enquiry yet.")

    if inbound is not None and (outbound is None or inbound > outbound):
        waited = now - inbound
        if waited > REPLY_WITHIN:
            hours = round(waited.total_seconds() / 3600)
            return NextAction(
                "reply_overdue",
                NEXT_ACTION_LABELS["reply_overdue"],
                (inbound + REPLY_WITHIN).astimezone(tz).date(),
                True,
                False,
                f"The customer emailed on {_fmt(inbound.astimezone(tz).date())} and "
                f"we haven't replied in {hours} hours.",
                "policy",
            )
        return _none(
            f"The customer emailed on {_fmt(inbound.astimezone(tz).date())} — a "
            "reply is due within 24 hours."
        )

    anchor = inbound or f.created_at
    silence = now - anchor
    if silence >= CALL_AND_CLOSE_AFTER:
        due = (anchor + CALL_AND_CLOSE_AFTER).astimezone(tz).date()
        return NextAction(
            "day_14",
            NEXT_ACTION_LABELS["day_14"],
            due,
            due < today,
            due == today,
            f"The client has been silent for {silence.days} days. Policy is a phone "
            "call, then close the enquiry — another email won't clear it.",
            "policy",
        )

    last_out = outbound or f.created_at
    since = now - last_out
    if since >= FOLLOW_UP_AFTER:
        due = (last_out + FOLLOW_UP_AFTER).astimezone(tz).date()
        return NextAction(
            "day_7",
            NEXT_ACTION_LABELS["day_7"],
            due,
            due < today,
            due == today,
            f"We replied {since.days} days ago and the client hasn't come back. "
            "Sending a follow-up resets this for another 7 days.",
            "policy",
        )
    days = since.days
    return _none(
        f"We replied {days} day{'' if days == 1 else 's'} ago — nothing is due "
        "until day 7."
    )


def load_enquiry_facts(
    db: Session, org_id: str, enquiry_ids: list[str]
) -> dict[str, EnquiryFacts]:
    """The Python side's facts, loaded with plain ORM queries — deliberately
    not via the SQL below, so the agreement test compares two implementations
    rather than one with itself."""
    if not enquiry_ids:
        return {}
    enquiries = (
        db.query(FnEnquiry)
        .filter(FnEnquiry.organization_id == org_id, FnEnquiry.id.in_(enquiry_ids))
        .all()
    )
    org = db.query(Organization).filter(Organization.id == org_id).first()
    org_tz = org.default_timezone if org else None
    venue_tz = {
        v.id: v.timezone
        for v in db.query(Venue).filter(
            Venue.id.in_({e.venue_id for e in enquiries if e.venue_id})
        )
    }

    closures: dict[str, list] = {}
    for c in db.query(FnEnquiryClosure).filter(
        FnEnquiryClosure.organization_id == org_id,
        FnEnquiryClosure.enquiry_id.in_(enquiry_ids),
    ):
        closures.setdefault(c.enquiry_id, []).append(c)

    live_events = {
        r[0]
        for r in db.query(FnEvent.enquiry_id).filter(
            FnEvent.organization_id == org_id,
            FnEvent.enquiry_id.in_(enquiry_ids),
            FnEvent.cancelled_at.is_(None),
        )
    }

    quotes: dict[str, dt.datetime] = {}
    for p in db.query(FnProposalEvent).filter(
        FnProposalEvent.organization_id == org_id,
        FnProposalEvent.enquiry_id.in_(enquiry_ids),
        FnProposalEvent.kind == "sent",
    ):
        if p.enquiry_id not in quotes or p.occurred_at < quotes[p.enquiry_id]:
            quotes[p.enquiry_id] = p.occurred_at

    threads: dict[tuple[str, str], set[str]] = {}
    for link in db.query(FnThreadLink).filter(
        FnThreadLink.organization_id == org_id,
        FnThreadLink.enquiry_id.in_(enquiry_ids),
        FnThreadLink.unlinked_at.is_(None),
    ):
        threads.setdefault((link.account_id, link.gmail_thread_id), set()).add(
            link.enquiry_id
        )
    last_in: dict[str, dt.datetime] = {}
    last_out: dict[str, dt.datetime] = {}
    if threads:
        accounts = {a for a, _ in threads}
        for m in db.query(FnMailboxMessage).filter(
            FnMailboxMessage.organization_id == org_id,
            FnMailboxMessage.account_id.in_(accounts),
            FnMailboxMessage.gmail_thread_id.in_({t for _, t in threads}),
        ):
            if m.is_internal or m.is_auto_reply:
                continue
            for eid in threads.get((m.account_id, m.gmail_thread_id), ()):
                bucket = last_in if m.is_inbound else last_out
                if eid not in bucket or m.internal_date > bucket[eid]:
                    bucket[eid] = m.internal_date

    out = {}
    for e in enquiries:
        latest = _py_latest(closures.get(e.id, []))
        out[e.id] = EnquiryFacts(
            enquiry_id=e.id,
            created_at=e.created_at,
            follow_up_date=e.follow_up_date,
            follow_up_reason=e.follow_up_reason,
            closure_kind=latest.kind if latest else None,
            has_live_event=e.id in live_events,
            quote_sent_at=quotes.get(e.id),
            last_inbound_at=last_in.get(e.id),
            last_outbound_at=last_out.get(e.id),
            tz=_tz_name(venue_tz.get(e.venue_id), org_tz),
        )
    return out


def _enquiry_fact_tables(org_id: str, enquiry_ids=None):
    """One derived table per enquiry fact, each computed ONCE for the org.

    Correlated scalar subqueries read naturally but plan terribly here: the
    stage and next-action layers reference each fact several times, Postgres
    flattens the layers, and every reference became its own per-row SubPlan
    (344 of them, 0.6 s to over a minute on the imported 1,235 enquiries).
    Grouped / DISTINCT ON subqueries cannot be flattened, so each of these
    is one pass, hash-joined to the enquiries. ``enquiry_ids`` narrows every
    pass to those enquiries (a page of a list) instead of the whole org."""

    def only(column):
        return column.in_(list(enquiry_ids)) if enquiry_ids is not None else true()

    c = FnEnquiryClosure
    closure = (
        select(c.enquiry_id, c.kind)
        .where(c.organization_id == org_id, only(c.enquiry_id))
        .distinct(c.enquiry_id)
        .order_by(c.enquiry_id, *_latest_key(c))
        .subquery("closure_fact")
    )
    live = (
        select(FnEvent.enquiry_id)
        .where(
            FnEvent.organization_id == org_id,
            FnEvent.enquiry_id.is_not(None),
            FnEvent.cancelled_at.is_(None),
            only(FnEvent.enquiry_id),
        )
        .group_by(FnEvent.enquiry_id)
        .subquery("live_event_fact")
    )
    quote = (
        select(
            FnProposalEvent.enquiry_id,
            func.min(FnProposalEvent.occurred_at).label("quote_sent_at"),
        )
        .where(
            FnProposalEvent.organization_id == org_id,
            FnProposalEvent.kind == "sent",
            only(FnProposalEvent.enquiry_id),
        )
        .group_by(FnProposalEvent.enquiry_id)
        .subquery("quote_fact")
    )
    m, link = FnMailboxMessage, FnThreadLink
    mail = (
        select(
            link.enquiry_id,
            func.max(m.internal_date)
            .filter(m.is_inbound.is_(True))
            .label("last_inbound_at"),
            func.max(m.internal_date)
            .filter(m.is_inbound.is_(False))
            .label("last_outbound_at"),
        )
        .select_from(link)
        .join(
            m,
            and_(
                m.organization_id == link.organization_id,
                m.account_id == link.account_id,
                m.gmail_thread_id == link.gmail_thread_id,
            ),
        )
        .where(
            link.organization_id == org_id,
            link.enquiry_id.is_not(None),
            link.unlinked_at.is_(None),
            m.is_internal.is_(False),
            m.is_auto_reply.is_(False),
            only(link.enquiry_id),
        )
        .group_by(link.enquiry_id)
        .subquery("mail_fact")
    )
    return closure, live, quote, mail


def enquiry_facts_select(
    org_id: str, valid_tz: tuple[str, ...] | None = None, *, enquiry_ids=None
):
    """One row of facts per enquiry of the org (archived included), or per
    enquiry in ``enquiry_ids``."""
    e = FnEnquiry
    closure, live, quote, mail = _enquiry_fact_tables(org_id, enquiry_ids)
    return (
        select(
            e.id.label("enquiry_id"),
            e.created_at.label("created_at"),
            e.follow_up_date.label("follow_up_date"),
            closure.c.kind.label("closure_kind"),
            live.c.enquiry_id.is_not(None).label("has_live_event"),
            quote.c.quote_sent_at.label("quote_sent_at"),
            mail.c.last_inbound_at.label("last_inbound_at"),
            mail.c.last_outbound_at.label("last_outbound_at"),
            _sql_tz(Venue.timezone, Organization.default_timezone, valid_tz).label(
                "tz"
            ),
        )
        .select_from(e)
        .join(Organization, Organization.id == e.organization_id)
        .outerjoin(Venue, Venue.id == e.venue_id)
        .outerjoin(closure, closure.c.enquiry_id == e.id)
        .outerjoin(live, live.c.enquiry_id == e.id)
        .outerjoin(quote, quote.c.enquiry_id == e.id)
        .outerjoin(mail, mail.c.enquiry_id == e.id)
        .where(
            e.organization_id == org_id,
            e.id.in_(list(enquiry_ids)) if enquiry_ids is not None else true(),
        )
    )


def enquiry_status_select(
    org_id: str,
    now: dt.datetime,
    *,
    valid_tz: tuple[str, ...] | None = None,
    enquiry_ids=None,
):
    """Per enquiry: ``enquiry_id, stage, next_action, next_action_due,
    next_action_overdue, next_action_due_today`` plus the facts
    (``closure_kind, has_live_event, quote_sent_at, last_inbound_at,
    last_outbound_at, tz``). Subquery it and join on ``enquiry_id``. Pass
    ``valid_tz=valid_timezones(db, org_id)`` in request paths, and
    ``enquiry_ids`` when only those rows are wanted (a list page): every fact
    is then computed for them alone."""
    now_ = literal(now, DateTime(timezone=True))
    # OFFSET 0 is an optimisation fence: the facts are computed once per row
    # and the layers below read them as columns, instead of Postgres inlining
    # (and so repeating) the facts expression into every reference.
    f = (
        enquiry_facts_select(org_id, valid_tz, enquiry_ids=enquiry_ids)
        .offset(0)
        .subquery("enquiry_facts")
    )
    stage = case(
        (f.c.closure_kind == "lost", "lost"),
        (f.c.has_live_event, "confirmed"),
        (f.c.quote_sent_at.is_not(None), "quoted"),
        (f.c.last_outbound_at.is_not(None), "replied"),
        else_="new",
    )
    s = select(*f.c, stage.label("stage")).subquery("enquiry_stage")

    inbound_last = and_(
        s.c.last_inbound_at.is_not(None),
        or_(s.c.last_outbound_at.is_(None), s.c.last_inbound_at > s.c.last_outbound_at),
    )
    anchor14 = func.coalesce(s.c.last_inbound_at, s.c.created_at)
    anchor7 = func.coalesce(s.c.last_outbound_at, s.c.created_at)
    kind = case(
        (s.c.stage.not_in(OPEN_STAGES), "none"),
        (s.c.follow_up_date.is_not(None), "requested"),
        (and_(s.c.last_inbound_at.is_(None), s.c.last_outbound_at.is_(None)), "none"),
        (
            and_(
                inbound_last,
                s.c.last_inbound_at
                < literal(now - REPLY_WITHIN, DateTime(timezone=True)),
            ),
            "reply_overdue",
        ),
        (inbound_last, "none"),
        (
            anchor14 <= literal(now - CALL_AND_CLOSE_AFTER, DateTime(timezone=True)),
            "day_14",
        ),
        (anchor7 <= literal(now - FOLLOW_UP_AFTER, DateTime(timezone=True)), "day_7"),
        else_="none",
    )
    k = select(
        *s.c,
        kind.label("next_action"),
        anchor14.label("anchor14"),
        anchor7.label("anchor7"),
        _local_date(s.c.tz, now_).label("today"),
    ).subquery("enquiry_next")

    due = case(
        (k.c.next_action == "requested", k.c.follow_up_date),
        (
            k.c.next_action == "reply_overdue",
            _local_date(k.c.tz, k.c.last_inbound_at + _hours(24)),
        ),
        (
            k.c.next_action == "day_14",
            _local_date(k.c.tz, k.c.anchor14 + _hours(14 * 24)),
        ),
        (k.c.next_action == "day_7", _local_date(k.c.tz, k.c.anchor7 + _hours(7 * 24))),
        else_=None,
    )
    d = select(*k.c, due.label("next_action_due")).subquery("enquiry_due")
    overdue = case(
        (d.c.next_action == "reply_overdue", True),
        (d.c.next_action_due.is_(None), False),
        else_=d.c.next_action_due < d.c.today,
    )
    due_today = case(
        (
            d.c.next_action.in_(("requested", "day_14", "day_7")),
            d.c.next_action_due == d.c.today,
        ),
        else_=False,
    )
    return select(
        d.c.enquiry_id,
        d.c.stage,
        d.c.next_action,
        d.c.next_action_due,
        overdue.label("next_action_overdue"),
        due_today.label("next_action_due_today"),
        d.c.closure_kind,
        d.c.has_live_event,
        d.c.quote_sent_at,
        d.c.last_inbound_at,
        d.c.last_outbound_at,
        d.c.tz,
    )


def enquiry_status(db: Session, org_id: str, enquiry_id: str, now=None):
    """``(stage, NextAction)`` for one enquiry (Python side)."""
    facts = load_enquiry_facts(db, org_id, [enquiry_id]).get(enquiry_id)
    if facts is None:
        return None, None
    return enquiry_stage(facts), next_action(
        facts, now or dt.datetime.now(dt.timezone.utc)
    )


# ===========================================================================
# Events
# ===========================================================================


@dataclass(frozen=True)
class EventFacts:
    event_id: str
    cancelled_at: dt.datetime | None
    #: section -> (kind, occurred_at) of its latest confirmation row.
    sections: dict = field(default_factory=dict)
    link_sent_at: dt.datetime | None = None
    last_client_edit_at: dt.datetime | None = None


@dataclass(frozen=True)
class EventStatus:
    status: str
    #: When the last of the three sections was confirmed/skipped.
    confirmed_at: dt.datetime | None
    edited_since_confirmed: bool
    sections_done: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "confirmed_at": self.confirmed_at.isoformat()
            if self.confirmed_at
            else None,
            "edited_since_confirmed": self.edited_since_confirmed,
            "sections_done": list(self.sections_done),
        }


def event_status(f: EventFacts) -> EventStatus:
    done = tuple(
        s for s in EVENT_SECTIONS if f.sections.get(s) and f.sections[s][0] in _DONE
    )
    all_done = len(done) == len(EVENT_SECTIONS)
    confirmed_at = max(f.sections[s][1] for s in done) if all_done else None
    if f.cancelled_at is not None:
        status = "cancelled"
    elif all_done:
        status = "confirmed"
    elif f.last_client_edit_at is not None:
        status = "in_progress"
    elif f.link_sent_at is not None:
        status = "sent_to_client"
    else:
        status = "booked"
    edited = bool(
        status == "confirmed"
        and f.last_client_edit_at is not None
        and f.last_client_edit_at > confirmed_at
    )
    return EventStatus(status, confirmed_at, edited, done)


def load_event_facts(
    db: Session, org_id: str, event_ids: list[str]
) -> dict[str, EventFacts]:
    if not event_ids:
        return {}
    events = (
        db.query(FnEvent.id, FnEvent.cancelled_at)
        .filter(FnEvent.organization_id == org_id, FnEvent.id.in_(event_ids))
        .all()
    )
    by_section: dict[tuple[str, str], list] = {}
    for c in db.query(FnEventSectionConfirmation).filter(
        FnEventSectionConfirmation.organization_id == org_id,
        FnEventSectionConfirmation.event_id.in_(event_ids),
    ):
        by_section.setdefault((c.event_id, c.section), []).append(c)
    link_sent: dict[str, dt.datetime] = {}
    client_edit: dict[str, dt.datetime] = {}
    for ch in db.query(FnEventChange).filter(
        FnEventChange.organization_id == org_id,
        FnEventChange.event_id.in_(event_ids),
        FnEventChange.kind.in_(("link_sent", "edited")),
    ):
        if ch.kind == "link_sent":
            bucket = link_sent
        elif ch.actor_kind == "client":
            bucket = client_edit
        else:
            continue
        if ch.event_id not in bucket or ch.occurred_at > bucket[ch.event_id]:
            bucket[ch.event_id] = ch.occurred_at
    out = {}
    for event_id, cancelled_at in events:
        sections = {}
        for s in EVENT_SECTIONS:
            latest = _py_latest(by_section.get((event_id, s), []))
            if latest is not None:
                sections[s] = (latest.kind, latest.occurred_at)
        out[event_id] = EventFacts(
            event_id=event_id,
            cancelled_at=cancelled_at,
            sections=sections,
            link_sent_at=link_sent.get(event_id),
            last_client_edit_at=client_edit.get(event_id),
        )
    return out


def event_status_for(db: Session, org_id: str, event_id: str) -> EventStatus | None:
    facts = load_event_facts(db, org_id, [event_id]).get(event_id)
    return event_status(facts) if facts else None


def _ids_clause(column, ids):
    return column.in_(list(ids)) if ids is not None else true()


def event_status_select(org_id: str, *, event_ids=None):
    """Per event: ``event_id, status, confirmed_at, edited_since_confirmed,
    link_sent_at, last_client_edit_at`` and ``<section>_kind`` for each of
    details / beverages / food — for the org's events, or those in
    ``event_ids`` (a list page: the facts are then computed for them alone).

    Each fact is a grouped / DISTINCT ON derived table, computed once and
    hash-joined. Neither correlated scalar subqueries (Postgres repeats those
    per reference once it flattens the layers) nor per-event LATERALs (a
    fixed cost per event and fact: 0.25 s for 949 events)."""
    ev = FnEvent
    sc = FnEventSectionConfirmation
    latest = (
        select(sc.event_id, sc.section, sc.kind, sc.occurred_at)
        .where(sc.organization_id == org_id, _ids_clause(sc.event_id, event_ids))
        .distinct(sc.event_id, sc.section)
        .order_by(sc.event_id, sc.section, *_latest_key(sc))
        .subquery("latest_section")
    )
    sections = (
        select(
            latest.c.event_id,
            *[
                func.max(latest.c.kind).filter(latest.c.section == s).label(f"{s}_kind")
                for s in EVENT_SECTIONS
            ],
            *[
                func.max(latest.c.occurred_at)
                .filter(latest.c.section == s)
                .label(f"{s}_at")
                for s in EVENT_SECTIONS
            ],
        )
        .group_by(latest.c.event_id)
        .subquery("section_fact")
    )
    ch = FnEventChange
    changes = (
        select(
            ch.event_id,
            func.max(ch.occurred_at)
            .filter(ch.kind == "link_sent")
            .label("link_sent_at"),
            func.max(ch.occurred_at)
            .filter(ch.kind == "edited", ch.actor_kind == "client")
            .label("last_client_edit_at"),
        )
        .where(
            ch.organization_id == org_id,
            ch.kind.in_(("link_sent", "edited")),
            _ids_clause(ch.event_id, event_ids),
        )
        .group_by(ch.event_id)
        .subquery("change_fact")
    )
    cols = [ev.id.label("event_id"), ev.cancelled_at.label("cancelled_at")]
    for s in EVENT_SECTIONS:
        cols += [sections.c[f"{s}_kind"], sections.c[f"{s}_at"]]
    cols += [changes.c.link_sent_at, changes.c.last_client_edit_at]
    f = (
        select(*cols)
        .select_from(ev)
        .outerjoin(sections, sections.c.event_id == ev.id)
        .outerjoin(changes, changes.c.event_id == ev.id)
        .where(ev.organization_id == org_id, _ids_clause(ev.id, event_ids))
        .subquery("event_facts")
    )

    done = [f.c[f"{s}_kind"].in_(_DONE) for s in EVENT_SECTIONS]
    all_done = and_(*done)
    confirmed_at = case(
        (all_done, func.greatest(*[f.c[f"{s}_at"] for s in EVENT_SECTIONS])),
        else_=None,
    )
    status = case(
        (f.c.cancelled_at.is_not(None), "cancelled"),
        (all_done, "confirmed"),
        (f.c.last_client_edit_at.is_not(None), "in_progress"),
        (f.c.link_sent_at.is_not(None), "sent_to_client"),
        else_="booked",
    )
    s = select(
        *f.c, status.label("status"), confirmed_at.label("confirmed_at")
    ).subquery("event_state")
    edited = and_(
        s.c.status == "confirmed",
        s.c.last_client_edit_at.is_not(None),
        s.c.last_client_edit_at > s.c.confirmed_at,
    )
    return select(
        s.c.event_id,
        s.c.status,
        s.c.confirmed_at,
        case((edited, True), else_=False).label("edited_since_confirmed"),
        s.c.link_sent_at,
        s.c.last_client_edit_at,
        *[s.c[f"{sec}_kind"] for sec in EVENT_SECTIONS],
    )


# ===========================================================================
# Minimum spends
# ===========================================================================


@dataclass(frozen=True)
class MinSpendFacts:
    min_spend_id: str
    event_id: str | None
    withdrawn_at: dt.datetime | None
    approval_kind: str | None
    response_kind: str | None
    #: None when the option has no area — which means approval is required.
    area_requires_approval: bool | None
    #: Does the latest approval cover the option's CURRENT terms? An approval
    #: is of an amount, a date, times and an area: once any of them moves it
    #: stops counting — whichever path moved it (security review, Oct 2026:
    #: approve $9,000 -> record a decline -> edit to $50 -> confirm booked an
    #: unapproved $50 minimum).
    approval_covers: bool = True


def terms_key(
    venue_id, area_id, amount_cents, event_date, start_time, end_time, ends_next_day
) -> str:
    """The terms an approval is OF, as one comparable string. The SQL twin is
    ``_terms_key_sql``; the two must produce the same characters."""

    def hms(t):
        return t.strftime("%H:%M:%S") if t is not None else ""

    return "|".join(
        (
            venue_id or "",
            area_id or "",
            str(int(amount_cents)) if amount_cents is not None else "",
            event_date.isoformat() if event_date is not None else "",
            hms(start_time),
            hms(end_time),
            "1" if ends_next_day else "0",
        )
    )


def terms_key_of(option) -> str:
    """``terms_key`` of an ``FnMinSpend`` row."""
    return terms_key(
        option.venue_id,
        option.area_id,
        option.amount_cents,
        option.event_date,
        option.start_time,
        option.end_time,
        option.ends_next_day,
    )


def approval_covers(approval, option) -> bool:
    """Whether an approval fact covers the option's current terms. Imported
    history has no ``terms_key``; then the approved amount is the check."""
    if approval is None:
        return True
    if approval.terms_key is not None:
        return approval.terms_key == terms_key_of(option)
    return approval.amount_cents is None or approval.amount_cents == option.amount_cents


def _terms_key_sql(ms):
    def text_of(expr):
        return func.coalesce(expr, "")

    return func.concat_ws(
        "|",
        text_of(ms.venue_id),
        text_of(ms.area_id),
        text_of(cast(ms.amount_cents, String)),
        text_of(func.to_char(ms.event_date, "YYYY-MM-DD")),
        text_of(func.to_char(ms.start_time, "HH24:MI:SS")),
        text_of(func.to_char(ms.end_time, "HH24:MI:SS")),
        case((ms.ends_next_day.is_(True), "1"), else_="0"),
    )


def _approved(f: MinSpendFacts) -> bool:
    return f.approval_kind in _APPROVED and f.approval_covers


def min_spend_state(f: MinSpendFacts) -> str:
    if f.event_id is not None:
        return "confirmed"
    if f.withdrawn_at is not None:
        return "withdrawn"
    if f.response_kind == "decline":
        return "declined"
    if _approved(f):
        return "approved"
    return "proposed"


def needs_approval(f: MinSpendFacts) -> bool:
    return f.area_requires_approval is not False


def min_spend_confirmable(f: MinSpendFacts) -> bool:
    """May the customer confirm it (and so create the event)? A decline does
    not block a later confirm — customers change their minds."""
    if f.event_id is not None or f.withdrawn_at is not None:
        return False
    return _approved(f) or not needs_approval(f)


def load_min_spend_facts(
    db: Session, org_id: str, min_spend_ids: list[str]
) -> dict[str, MinSpendFacts]:
    if not min_spend_ids:
        return {}
    rows = (
        db.query(FnMinSpend)
        .filter(FnMinSpend.organization_id == org_id, FnMinSpend.id.in_(min_spend_ids))
        .all()
    )
    areas = {
        a.id: a.requires_approval
        for a in db.query(FnVenueArea).filter(
            FnVenueArea.organization_id == org_id,
            FnVenueArea.id.in_({r.area_id for r in rows if r.area_id}),
        )
    }
    approvals: dict[str, list] = {}
    for a in db.query(FnMinSpendApproval).filter(
        FnMinSpendApproval.organization_id == org_id,
        FnMinSpendApproval.min_spend_id.in_(min_spend_ids),
    ):
        approvals.setdefault(a.min_spend_id, []).append(a)
    responses: dict[str, list] = {}
    for r in db.query(FnMinSpendResponse).filter(
        FnMinSpendResponse.organization_id == org_id,
        FnMinSpendResponse.min_spend_id.in_(min_spend_ids),
    ):
        responses.setdefault(r.min_spend_id, []).append(r)
    out = {}
    for r in rows:
        ap = _py_latest(approvals.get(r.id, []))
        rs = _py_latest(responses.get(r.id, []))
        out[r.id] = MinSpendFacts(
            min_spend_id=r.id,
            event_id=r.event_id,
            withdrawn_at=r.withdrawn_at,
            approval_kind=ap.kind if ap else None,
            response_kind=rs.kind if rs else None,
            area_requires_approval=areas.get(r.area_id) if r.area_id else None,
            approval_covers=approval_covers(ap, r),
        )
    return out


def min_spend_status_select(org_id: str, *, enquiry_ids=None):
    """Per option: ``min_spend_id, state, approval_kind, response_kind,
    area_requires_approval, approval_covers, confirmable`` — for the org, or for the options
    of the enquiries in ``enquiry_ids`` (pass them when you have them: the
    approval and response facts are then read for those options alone)."""
    ms = FnMinSpend
    ap, rs = FnMinSpendApproval, FnMinSpendResponse
    mine = (
        select(ms.id)
        .where(ms.organization_id == org_id, ms.enquiry_id.in_(list(enquiry_ids)))
        .scalar_subquery()
        if enquiry_ids is not None
        else None
    )
    approval = (
        select(ap.min_spend_id, ap.kind, ap.terms_key, ap.amount_cents)
        .where(
            ap.organization_id == org_id,
            ap.min_spend_id.in_(mine) if mine is not None else true(),
        )
        .distinct(ap.min_spend_id)
        .order_by(ap.min_spend_id, *_latest_key(ap))
        .subquery("approval_fact")
    )
    response = (
        select(rs.min_spend_id, rs.kind)
        .where(
            rs.organization_id == org_id,
            rs.min_spend_id.in_(mine) if mine is not None else true(),
        )
        .distinct(rs.min_spend_id)
        .order_by(rs.min_spend_id, *_latest_key(rs))
        .subquery("response_fact")
    )
    area = aliased(FnVenueArea)
    f = (
        select(
            ms.id.label("min_spend_id"),
            ms.event_id,
            ms.withdrawn_at,
            approval.c.kind.label("approval_kind"),
            response.c.kind.label("response_kind"),
            area.requires_approval.label("area_requires_approval"),
            case(
                (
                    approval.c.terms_key.is_not(None),
                    approval.c.terms_key == _terms_key_sql(ms),
                ),
                (
                    approval.c.amount_cents.is_not(None),
                    approval.c.amount_cents == ms.amount_cents,
                ),
                else_=True,
            ).label("approval_covers"),
        )
        .select_from(ms)
        .outerjoin(
            area,
            and_(
                area.organization_id == ms.organization_id,
                area.venue_id == ms.venue_id,
                area.id == ms.area_id,
            ),
        )
        .outerjoin(approval, approval.c.min_spend_id == ms.id)
        .outerjoin(response, response.c.min_spend_id == ms.id)
        .where(
            ms.organization_id == org_id,
            ms.enquiry_id.in_(list(enquiry_ids)) if enquiry_ids is not None else true(),
        )
        .subquery("min_spend_facts")
    )
    approved = and_(f.c.approval_kind.in_(_APPROVED), f.c.approval_covers.is_(True))
    state = case(
        (f.c.event_id.is_not(None), "confirmed"),
        (f.c.withdrawn_at.is_not(None), "withdrawn"),
        (f.c.response_kind == "decline", "declined"),
        (approved, "approved"),
        else_="proposed",
    )
    confirmable = case(
        (or_(f.c.event_id.is_not(None), f.c.withdrawn_at.is_not(None)), False),
        (approved, True),
        (f.c.area_requires_approval.is_(False), True),
        else_=False,
    )
    return select(
        f.c.min_spend_id,
        state.label("state"),
        f.c.approval_kind,
        f.c.response_kind,
        f.c.area_requires_approval,
        f.c.approval_covers,
        confirmable.label("confirmable"),
    )
