"""Read models — the dashboard, the reports, activity timelines and print
sheets, and the budget / last-year reference from LoadedHub.

Nothing here decides a status or a sum. Stages, next actions, event statuses
and option states come from ``status.py``'s SQL selects; expected spend from
``money.event_money_select`` and the pure ``money`` rules — so a number on the
dashboard, in a report, on the agent's answer and on a print is the same
number. What this module adds is grouping, windows and shapes.

**Windows.** Rolling windows (the dashboard's "last 4 weeks", the conversion
cohort) run from *now*. Day buckets and "today" are civil dates in the
organisation's default timezone — or the venue's, when the caller narrowed to
one venue — never the server's.

**Conversion** is ``confirmed / (confirmed + lost)`` over a cohort of
enquiries created in a window that ends ``lag`` weeks ago, so recent
enquiries have had time to close (Orbit's ConversionDetail). Open enquiries
are counted but left out of the rate. A cancelled booking closes its enquiry
as lost (``events.cancel_event``), so it converts as lost.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import defaultdict
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import and_, false, func, or_
from sqlalchemy.orm import Session

from app.db.functions_models import (
    FnContact,
    FnContactEmail,
    FnEnquiry,
    FnEnquiryClosure,
    FnEvent,
    FnEventChange,
    FnEventSectionConfirmation,
    FnFormSubmission,
    FnMailboxAccount,
    FnMailboxMessage,
    FnMailboxThread,
    FnMinSpend,
    FnMinSpendApproval,
    FnMinSpendResponse,
    FnPreorderMenu,
    FnPreorderMenuItem,
    FnProposalEvent,
    FnProposalVersion,
    FnThreadLink,
    FnVenueArea,
)
from app.db.models import Organization, User, Venue
from app.services.functions import enquiries as enquiries_svc
from app.services.functions import events as events_svc
from app.services.functions import money as money_svc
from app.services.functions import status as status_svc
from app.services.functions.company import format_money, get_company_settings
from app.services.functions.ctx import FunctionsCtx
from app.services.functions.errors import Invalid, NotFound
from app.services.functions.timeutil import to_instant, venue_tz

logger = logging.getLogger(__name__)

UTC = dt.timezone.utc
#: Option states that make up what an enquiry is worth (a declined or
#: withdrawn option is not on the table).
VALUE_STATES = ("proposed", "approved", "confirmed")
UNCONFIRMED = ("booked", "sent_to_client", "in_progress")
ATTENTION_ORDER = {
    "reply_overdue": 0,
    "unconfirmed_event": 1,
    "client_edit": 2,
    "quote_response": 3,
    "requested": 4,
    "day_14": 5,
    "day_7": 6,
}
FOOD_TIMING_LABELS = {
    "all_at_once_arrival": "All at once, on arrival",
    "staggered_arrival": "Staggered, from arrival",
    "all_at_once_time": "All at once, at a set time",
    "staggered_time": "Staggered, from a set time",
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _now(now: dt.datetime | None) -> dt.datetime:
    return now or dt.datetime.now(UTC)


def org_tz(db: Session, ctx: FunctionsCtx, venue_id: str | None = None) -> ZoneInfo:
    """The zone "today" and day buckets are read in: the one venue the
    caller narrowed to, else the company default, else UTC."""
    if venue_id and venue_id in ctx.venue_ids:
        venue = db.query(Venue).filter(Venue.id == venue_id).first()
        try:
            return venue_tz(venue)
        except Invalid:
            pass
    org = db.query(Organization).filter(Organization.id == ctx.org_id).first()
    name = getattr(org, "default_timezone", None) or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — a bad stored zone must not break a report
        return ZoneInfo("UTC")


def venue_filter(ctx: FunctionsCtx, venue: str | None) -> tuple[list[str] | None, bool]:
    """``(venue_ids, unassigned_only)`` from a report's ``venue`` param: empty
    = every venue the caller has (+ group-wide enquiries), ``"unassigned"`` =
    group-wide enquiries only, else one venue (which must be the caller's)."""
    if not venue:
        return None, False
    if venue == "unassigned":
        return None, True
    if venue not in ctx.venue_ids:
        raise NotFound("Venue not found.")
    return [venue], False


def _enquiry_scope(
    q, ctx: FunctionsCtx, venue_ids, unassigned: bool, *, group_wide: bool = False
):
    """The caller's enquiries, narrowed to ``venue_ids`` (also keeping the
    group-wide ones when ``group_wide`` — the enquiries list's rule) or to
    the group-wide ones alone (``unassigned``)."""
    e = FnEnquiry
    q = q.filter(
        e.organization_id == ctx.org_id,
        ctx.venue_clause(e.venue_id, allow_null=True),
        ctx.record_clause(e),
    )
    if unassigned:
        q = q.filter(e.venue_id.is_(None))
    elif venue_ids:
        at_venue = e.venue_id.in_(venue_ids)
        q = q.filter(or_(e.venue_id.is_(None), at_venue) if group_wide else at_venue)
    return q


def _stage_select(db: Session, ctx: FunctionsCtx, now: dt.datetime):
    return status_svc.enquiry_status_select(
        ctx.org_id, now, valid_tz=status_svc.valid_timezones(db, ctx.org_id)
    ).subquery("st")


def _pct(part: int, whole: int) -> int | None:
    return round(part / whole * 100) if whole else None


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def user_names(db: Session, user_ids) -> dict[str, str]:
    ids = {u for u in user_ids if u}
    if not ids:
        return {}
    return {
        u.id: (u.full_name or u.email or "Someone")
        for u in db.query(User).filter(User.id.in_(ids))
    }


def _actor(names: dict, user_id: str | None, label: str | None) -> str | None:
    if user_id and user_id in names:
        return names[user_id] + (f" (via {label})" if label == "Norm" else "")
    return label


# ---------------------------------------------------------------------------
# Enquiry value — the money rules over many enquiries at once
# ---------------------------------------------------------------------------


def value_of(options, food_at: dict, food_total: int) -> int:
    """One enquiry's worth from its options ``[(venue_id, amount_cents,
    state, ...)]``: the best option total (max of its minimum and the food at
    its venue) over the options still on the table, else its food —
    ``money.enquiry_value_cents``, never a sum of alternatives."""
    totals = [
        money_svc.option_total_cents(amount, food_at.get(venue_id, 0))
        for venue_id, amount, state, *_ in options
        if state in VALUE_STATES
    ]
    return money_svc.enquiry_value_cents(totals, food_total)


def _require_ctx(ctx) -> FunctionsCtx:
    """Money helpers take the caller's context, never a bare organisation id:
    a group-wide enquiry can carry options and menus at venues the caller
    can't see, and their amounts must not reach the caller's figures."""
    if not isinstance(ctx, FunctionsCtx):
        raise TypeError("Pass the caller's FunctionsCtx, not an organisation id.")
    return ctx


def option_rows(db: Session, ctx: FunctionsCtx, enquiry_ids) -> dict[str, list]:
    """``{enquiry_id: [(venue_id, amount_cents, state, confirmable)]}`` —
    these enquiries' options at the caller's venues, with their derived
    state, the status select narrowed to them."""
    ctx = _require_ctx(ctx)
    org_id = ctx.org_id
    ids = list(dict.fromkeys(enquiry_ids))
    if not ids:
        return {}
    ms = status_svc.min_spend_status_select(org_id, enquiry_ids=ids).subquery("mss")
    options: dict[str, list] = defaultdict(list)
    for enquiry_id, venue_id, amount, state, confirmable in (
        db.query(
            FnMinSpend.enquiry_id,
            FnMinSpend.venue_id,
            FnMinSpend.amount_cents,
            ms.c.state,
            ms.c.confirmable,
        )
        .join(ms, ms.c.min_spend_id == FnMinSpend.id)
        .filter(
            FnMinSpend.organization_id == org_id,
            FnMinSpend.enquiry_id.in_(ids),
            ctx.venue_clause(FnMinSpend.venue_id),
        )
    ):
        options[enquiry_id].append((venue_id, amount, state, bool(confirmable)))
    return options


def food_at(db: Session, ctx: FunctionsCtx, enquiry_ids) -> dict[str, dict[str, int]]:
    """``{enquiry_id: {venue_id: cents}}`` — the food on these enquiries'
    menus at the caller's venues (two queries, narrowed to them). A menu at a
    venue the caller can't see adds nothing."""
    ctx = _require_ctx(ctx)
    org_id = ctx.org_id
    ids = list(dict.fromkeys(enquiry_ids))
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    if not ids:
        return out
    # Plain rows, not ORM objects: menu_food_cents reads dicts as well, and
    # only priced dish lines can add anything (headers and unpriced set-menu
    # dishes are skipped by the rule anyway).
    pm, it = FnPreorderMenu, FnPreorderMenuItem
    menus = [
        {
            "id": m[0],
            "enquiry_id": m[1],
            "venue_id": m[2],
            "is_set_menu": m[3],
            "price_per_person_cents": m[4],
            "guest_count": m[5],
        }
        for m in db.query(
            pm.id,
            pm.enquiry_id,
            pm.venue_id,
            pm.is_set_menu,
            pm.price_per_person_cents,
            pm.guest_count,
        ).filter(
            pm.organization_id == org_id,
            pm.enquiry_id.in_(ids),
            ctx.venue_clause(pm.venue_id),
        )
    ]
    items: dict[str, list] = defaultdict(list)
    if menus:
        for menu_id, price, quantity in db.query(
            it.menu_id, it.unit_price_cents, it.quantity
        ).filter(
            it.organization_id == org_id,
            it.menu_id.in_([m["id"] for m in menus]),
            it.kind == "item",
            it.unit_price_cents.is_not(None),
        ):
            items[menu_id].append(
                {"kind": "item", "unit_price_cents": price, "quantity": quantity}
            )
    for m in menus:
        out[m["enquiry_id"]][m["venue_id"]] += money_svc.menu_food_cents(
            m, items[m["id"]]
        )
    return out


def enquiry_food(db: Session, ctx: FunctionsCtx, enquiry_id: str) -> int:
    """The food on one enquiry at the caller's venues (``money.enquiry_food_cents``
    counts every venue's — not for a caller's figures)."""
    return sum(food_at(db, ctx, [enquiry_id]).get(enquiry_id, {}).values())


def enquiry_values(
    db: Session, ctx: FunctionsCtx, enquiry_ids, *, options: dict | None = None
) -> dict[str, int]:
    """``{enquiry_id: cents}`` for these enquiries in three queries (their
    options with derived state, their menus, the menus' items), each narrowed
    to them and to the caller's venues — ``value_of`` decides. ``options``
    (``option_rows`` for at least these ids) saves the first query."""
    ctx = _require_ctx(ctx)
    ids = list(dict.fromkeys(enquiry_ids))
    if not ids:
        return {}
    if options is None:
        options = option_rows(db, ctx, ids)
    food = food_at(db, ctx, ids)
    return {
        e: value_of(options.get(e, []), food[e], sum(food[e].values())) for e in ids
    }


# ---------------------------------------------------------------------------
# The enquiries list's extra row facts and its row-level sorts
# ---------------------------------------------------------------------------


def min_spend_summary(options) -> dict:
    """An enquiry's options at a glance, from ``option_rows``: how many are
    still on the table (proposed / approved / confirmed — not withdrawn or
    declined), the largest minimum among them, and whether any is waiting
    for an approval (proposed, at an area that needs one, not yet approved)."""
    live = [
        (amount, state, confirmable)
        for _, amount, state, confirmable in options
        if state in VALUE_STATES
    ]
    return {
        "count": len(live),
        "largest_amount_cents": max((a for a, _, _ in live), default=None),
        "needs_approval": any(s == "proposed" and not c for _, s, c in live),
    }


def list_page_facts(db: Session, ctx: FunctionsCtx, enquiry_ids) -> dict[str, dict]:
    """What the enquiries list shows beside each row, for THESE enquiries
    (a page): ``value_cents``, the contact's primary ``contact_email``,
    ``min_spend_summary`` and ``last_email_from`` (who sent the latest
    message on the enquiry's linked threads, from the mailbox mirror). Four
    small queries, each narrowed to the page."""
    ids = list(dict.fromkeys(enquiry_ids))
    if not ids:
        return {}
    options = option_rows(db, ctx, ids)
    values = enquiry_values(db, ctx, ids, options=options)
    e, ce = FnEnquiry, FnContactEmail
    emails = dict(
        db.query(e.id, ce.email)
        .join(
            ce,
            and_(
                ce.organization_id == e.organization_id,
                ce.contact_id == e.contact_id,
            ),
        )
        .filter(e.organization_id == ctx.org_id, e.id.in_(ids))
        .order_by(e.id, ce.is_primary.desc(), ce.created_at, ce.id)
        .distinct(e.id)
    )
    # Threads in mailboxes the caller can see (the enquiry detail's rule),
    # still linked, latest message first.
    tl, mt, ma = FnThreadLink, FnMailboxThread, FnMailboxAccount
    last = {
        r[0]: {"name": r[1], "email": r[2], "at": _iso(r[3])}
        for r in db.query(
            tl.enquiry_id, mt.last_from_name, mt.last_from_email, mt.last_message_at
        )
        .join(
            ma, and_(ma.organization_id == tl.organization_id, ma.id == tl.account_id)
        )
        .join(
            mt,
            and_(
                mt.organization_id == tl.organization_id,
                mt.account_id == tl.account_id,
                mt.gmail_thread_id == tl.gmail_thread_id,
            ),
        )
        .filter(
            tl.organization_id == ctx.org_id,
            tl.enquiry_id.in_(ids),
            tl.unlinked_at.is_(None),
            mt.last_message_at.is_not(None),
            ctx.venue_clause(ma.venue_id),
        )
        .order_by(tl.enquiry_id, mt.last_message_at.desc(), mt.id)
        .distinct(tl.enquiry_id)
    }
    return {
        i: {
            "value_cents": values.get(i),
            "contact_email": emails.get(i),
            "min_spend_summary": min_spend_summary(options.get(i, [])),
            "last_email_from": last.get(i),
        }
        for i in ids
    }


#: List sorts the API applies over every matching row (``<field>_asc`` /
#: ``<field>_desc``): only value, which needs the money rule. Contact,
#: subject, guests and stage are F1's SQL sorts (``enquiries.SORTS``) in the
#: same order ``sort_rows`` gives — F1's tests hold the two together.
ROW_SORT_FIELDS = ("value",)
#: Every row is read in chunks of this many for a row sort.
ROW_SORT_CHUNK = 1000
#: ...up to this many rows (a guard; an organisation has far fewer).
ROW_SORT_MAX = 20_000


def row_sort(sort: str) -> tuple[str, bool] | None:
    """``(field, descending)`` for a row sort key, else None (an F1 sort)."""
    field, _, direction = sort.rpartition("_")
    if field in ROW_SORT_FIELDS and direction in ("asc", "desc"):
        return field, direction == "desc"
    return None


def all_enquiry_rows(db: Session, ctx: FunctionsCtx, **filters) -> list:
    """Every row ``enquiries.list_enquiries`` would list for these filters,
    newest first (its own ``created_desc, id`` order), read in chunks."""
    rows: list = []
    while len(rows) < ROW_SORT_MAX:
        chunk = enquiries_svc.list_enquiries(
            db,
            ctx,
            **filters,
            sort="created_desc",
            limit=ROW_SORT_CHUNK,
            offset=len(rows),
        )
        rows.extend(chunk)
        if len(chunk) < ROW_SORT_CHUNK:
            break
    return rows


def sort_rows(rows, field: str, descending: bool, values: dict | None = None) -> list:
    """List rows by contact name, subject (the event name), guests, stage
    (pipeline order) or value (``values``: ``{enquiry_id: cents}``), blanks
    last either way. ``rows`` arrive newest first, and the sort is stable, so
    equal keys stay newest first — a total order, so pages never overlap."""

    def text_key(value):
        return (value or "").strip().casefold() or None

    key = {
        "contact": lambda r: text_key(r.contact_name),
        "subject": lambda r: text_key(r.enquiry.subject),
        "guests": lambda r: r.enquiry.guests,
        "stage": lambda r: _STAGE_ORDER.get(r.stage, len(_STAGE_ORDER)),
        "value": lambda r: (values or {}).get(r.enquiry.id, 0),
    }[field]
    keyed = [(key(r), r) for r in rows]
    present = [kr for kr in keyed if kr[0] is not None]
    present.sort(key=lambda kr: kr[0], reverse=descending)  # stable both ways
    return [r for _, r in present] + [r for k, r in keyed if k is None]


_STAGE_ORDER = {stage: i for i, stage in enumerate(status_svc.STAGES)}


# ---------------------------------------------------------------------------
# Enquiry counts — aggregated by the database over status.py's select
# ---------------------------------------------------------------------------

#: Next actions that are follow-ups (vs ``reply_overdue``, a reply owed).
FOLLOW_UPS = ("requested", "day_7", "day_14")


def _cohort(now: dt.datetime, weeks: int, lag_weeks: int):
    """The conversion cohort: created between ``lag + weeks`` and ``lag``
    weeks ago, rolling from now."""
    if not 1 <= int(weeks) <= 104 or not 0 <= int(lag_weeks) <= 52:
        raise Invalid("weeks must be 1–104 and lag 0–52.")
    end = now - dt.timedelta(weeks=int(lag_weeks))
    return end - dt.timedelta(weeks=int(weeks)), end


@dataclass
class EnquiryCounts:
    """What the dashboard, the list KPIs and conversion need, as the database
    aggregated it: per (venue, stage) counts over the dashboard's windows,
    replies owed, follow-ups due, and the ids of the open enquiries (for
    their value and the attention list)."""

    #: ``[{venue_id, stage, all, last_28, prior_28, cohort}]`` (archived left
    #: out of every count).
    groups: list[dict]
    needs_reply: int
    follow_ups_due: int
    open_ids: list[str]
    #: Open enquiries whose next action is due today or overdue:
    #: ``[{id, kind, due ("YYYY-MM-DD"|None), overdue}]`` — the attention list.
    due: list[dict]

    def by_stage(self, key: str) -> dict[str, int]:
        out = dict.fromkeys(status_svc.STAGES, 0)
        for g in self.groups:
            out[g["stage"]] += g[key]
        return out


def enquiry_counts(
    db: Session,
    ctx: FunctionsCtx,
    *,
    now: dt.datetime,
    venue_ids: list[str] | None = None,
    unassigned: bool = False,
    group_wide: bool = False,
    cohort: tuple[dt.datetime, dt.datetime] | None = None,
) -> EnquiryCounts:
    """ONE evaluation of the status select, grouped by venue and stage in
    SQL: a handful of rows come back (counts, and the open ids as an array),
    not every enquiry of the organisation."""
    s = _stage_select(db, ctx, now)
    e = FnEnquiry
    live = e.archived_at.is_(None)
    is_open = and_(live, s.c.stage.in_(status_svc.OPEN_STAGES))
    is_due = and_(
        is_open,
        s.c.next_action != "none",
        or_(s.c.next_action_overdue.is_(True), s.c.next_action_due_today.is_(True)),
    )
    c0, c1 = cohort or (now, now)  # an empty window when no cohort is asked

    def n(condition):
        return func.count().filter(condition)

    rows = (
        _enquiry_scope(
            db.query(
                e.venue_id,
                s.c.stage,
                n(live),
                n(and_(live, e.created_at >= now - dt.timedelta(days=28))),
                n(
                    and_(
                        live,
                        e.created_at >= now - dt.timedelta(days=56),
                        e.created_at < now - dt.timedelta(days=28),
                    )
                ),
                n(and_(live, e.created_at >= c0, e.created_at < c1)),
                n(and_(is_open, s.c.next_action == "reply_overdue")),
                n(
                    and_(
                        is_open,
                        s.c.next_action.in_(FOLLOW_UPS),
                        or_(
                            s.c.next_action_overdue.is_(True),
                            s.c.next_action_due_today.is_(True),
                        ),
                    )
                ),
                func.array_agg(e.id).filter(is_open),
                func.json_agg(
                    func.json_build_object(
                        "id",
                        e.id,
                        "kind",
                        s.c.next_action,
                        "due",
                        s.c.next_action_due,
                        "overdue",
                        s.c.next_action_overdue,
                    )
                ).filter(is_due),
            )
            .select_from(e)
            .join(s, s.c.enquiry_id == e.id),
            ctx,
            venue_ids,
            unassigned,
            group_wide=group_wide,
        )
        .group_by(e.venue_id, s.c.stage)
        .all()
    )
    return EnquiryCounts(
        groups=[
            {
                "venue_id": r[0],
                "stage": r[1],
                "all": r[2],
                "last_28": r[3],
                "prior_28": r[4],
                "cohort": r[5],
            }
            for r in rows
        ],
        needs_reply=sum(r[6] for r in rows),
        follow_ups_due=sum(r[7] for r in rows),
        open_ids=[i for r in rows for i in (r[8] or [])],
        due=[d for r in rows for d in (r[9] or [])],
    )


@dataclass
class EnquiryFacts:
    """One enquiry with its derived stage and next action (status.py's SQL)
    and who / where — what the quote-response check reads."""

    id: str
    venue_id: str | None
    venue_name: str | None
    contact_name: str | None
    subject: str | None
    created_at: dt.datetime
    archived: bool
    stage: str
    next_action: str
    next_action_due: dt.date | None
    overdue: bool
    due_today: bool
    last_outbound_at: dt.datetime | None

    @property
    def open(self) -> bool:
        return not self.archived and self.stage in status_svc.OPEN_STAGES


def enquiry_facts(
    db: Session,
    ctx: FunctionsCtx,
    *,
    now: dt.datetime,
    enquiry_ids,
) -> list[EnquiryFacts]:
    """Display rows for THESE enquiries (the caller's only), with the status
    select narrowed to them (``enquiry_ids=``) so their facts alone are
    computed."""
    ids = list(dict.fromkeys(enquiry_ids))
    if not ids:
        return []
    s = status_svc.enquiry_status_select(
        ctx.org_id,
        now,
        valid_tz=status_svc.valid_timezones(db, ctx.org_id),
        enquiry_ids=ids,
    ).subquery("st")
    e = FnEnquiry
    q = _enquiry_scope(
        db.query(
            e.id,
            e.venue_id,
            Venue.name,
            FnContact.name,
            e.subject,
            e.created_at,
            e.archived_at,
            s.c.stage,
            s.c.next_action,
            s.c.next_action_due,
            s.c.next_action_overdue,
            s.c.next_action_due_today,
            s.c.last_outbound_at,
        )
        .join(s, s.c.enquiry_id == e.id)
        .join(
            FnContact,
            and_(
                FnContact.organization_id == e.organization_id,
                FnContact.id == e.contact_id,
            ),
        )
        .outerjoin(Venue, Venue.id == e.venue_id),
        ctx,
        None,
        False,
    ).filter(e.id.in_(ids))
    return [
        EnquiryFacts(
            id=r[0],
            venue_id=r[1],
            venue_name=r[2],
            contact_name=r[3],
            subject=r[4],
            created_at=r[5],
            archived=r[6] is not None,
            stage=r[7],
            next_action=r[8],
            next_action_due=r[9],
            overdue=bool(r[10]),
            due_today=bool(r[11]),
            last_outbound_at=r[12],
        )
        for r in q.all()
    ]


def enquiry_kpis(
    db: Session,
    ctx: FunctionsCtx,
    *,
    venue_ids: list[str] | None = None,
    unassigned: bool = False,
    group_wide: bool = False,
    now: dt.datetime | None = None,
    counts: EnquiryCounts | None = None,
) -> dict:
    """The Enquiries page's numbers over the caller's open enquiries: replies
    owed, follow-ups due (today or overdue), and open value. ``group_wide``
    counts group-wide enquiries under a venue filter too — the enquiries
    list's own rule, so the numbers match the rows."""
    if counts is None:
        counts = enquiry_counts(
            db,
            ctx,
            now=_now(now),
            venue_ids=venue_ids,
            unassigned=unassigned,
            group_wide=group_wide,
        )
    values = enquiry_values(db, ctx, counts.open_ids)
    return {
        "open": len(counts.open_ids),
        "needs_reply": counts.needs_reply,
        "follow_ups_due": counts.follow_ups_due,
        "open_value_cents": sum(values.values()),
    }


def expected_sales(
    db: Session,
    ctx: FunctionsCtx,
    *,
    venue_ids: list[str] | None = None,
    start: dt.date,
    days: int = 28,
    rows=None,
) -> dict:
    """Expected spend of the live events on ``start`` .. ``start + days - 1``
    (money.py's rule per event), in total and by trading week (Monday).
    ``rows`` — ``list_events`` rows already loaded from ``start`` on — saves
    the query (the dashboard has them)."""
    end = start + dt.timedelta(days=days - 1)
    if rows is None:
        rows = events_svc.list_events(
            db, ctx, venue_ids=venue_ids, date_from=start, date_to=end, limit=2000
        )
    rows = [r for r in rows if start <= r.event.event_date <= end]
    weeks: dict[dt.date, dict] = {}
    week = start - dt.timedelta(days=start.weekday())
    while week <= end:
        weeks[week] = {"week_start": week.isoformat(), "events": 0, "cents": 0}
        week += dt.timedelta(days=7)
    for r in rows:
        bucket = weeks[
            r.event.event_date - dt.timedelta(days=r.event.event_date.weekday())
        ]
        bucket["events"] += 1
        bucket["cents"] += int(r.expected_spend_cents or 0)
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "events": len(rows),
        "expected_spend_cents": sum(w["cents"] for w in weeks.values()),
        "weeks": list(weeks.values()),
    }


def conversion(
    db: Session,
    ctx: FunctionsCtx,
    *,
    weeks: int = 4,
    lag_weeks: int = 4,
    venue_ids: list[str] | None = None,
    unassigned: bool = False,
    now: dt.datetime | None = None,
    counts: EnquiryCounts | None = None,
    with_rows: bool = False,
) -> dict:
    """Confirmed ÷ (confirmed + lost) over enquiries created between
    ``lag + weeks`` and ``lag`` weeks ago. ``counts`` must have been taken
    with this cohort (the dashboard passes its own). ``with_rows`` adds the
    cohort's enquiries themselves (``cohort_rows``) — the report's table."""
    now = _now(now)
    start, end = _cohort(now, weeks, lag_weeks)
    if counts is None:
        counts = enquiry_counts(
            db,
            ctx,
            now=now,
            venue_ids=venue_ids,
            unassigned=unassigned,
            cohort=(start, end),
        )

    def _summary(by_stage: dict[str, int]) -> dict:
        total = sum(by_stage.values())
        confirmed, lost = by_stage.get("confirmed", 0), by_stage.get("lost", 0)
        return {
            "enquiries": total,
            "confirmed": confirmed,
            "lost": lost,
            "open": total - confirmed - lost,
            "rate_pct": _pct(confirmed, confirmed + lost),
        }

    per_venue: dict[str | None, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for g in counts.groups:
        if g["cohort"]:
            per_venue[g["venue_id"]][g["stage"]] += g["cohort"]
    names = dict(
        db.query(Venue.id, Venue.name).filter(
            Venue.id.in_([v for v in per_venue if v] or [""])
        )
    )
    out = {
        "weeks": int(weeks),
        "lag_weeks": int(lag_weeks),
        "cohort_from": start.isoformat(),
        "cohort_to": end.isoformat(),
        **_summary(counts.by_stage("cohort")),
        "by_venue": [
            {
                "venue_id": venue_id,
                "venue_name": names.get(venue_id) if venue_id else None,
                **_summary(by_stage),
            }
            for venue_id, by_stage in sorted(
                per_venue.items(), key=lambda kv: names.get(kv[0]) or "~"
            )
        ],
    }
    if with_rows:
        out["rows"] = cohort_rows(
            db,
            ctx,
            start=start,
            end=end,
            now=now,
            venue_ids=venue_ids,
            unassigned=unassigned,
        )
    return out


def cohort_rows(
    db: Session,
    ctx: FunctionsCtx,
    *,
    start: dt.datetime,
    end: dt.datetime,
    now: dt.datetime,
    venue_ids: list[str] | None = None,
    unassigned: bool = False,
) -> list[dict]:
    """The enquiries a conversion cohort counts — created in ``[start,
    end)``, not archived, in the same scope as ``enquiry_counts`` — newest
    first: ``{id, subject, contact, venue_id, venue, received, stage,
    outcome (confirmed | lost | open), counted}``; ``counted`` = in the rate
    (confirmed or lost)."""
    e = FnEnquiry
    rows = (
        _enquiry_scope(
            db.query(
                e.id, e.subject, FnContact.name, e.venue_id, Venue.name, e.created_at
            )
            .join(
                FnContact,
                and_(
                    FnContact.organization_id == e.organization_id,
                    FnContact.id == e.contact_id,
                ),
            )
            .outerjoin(Venue, Venue.id == e.venue_id),
            ctx,
            venue_ids,
            unassigned,
        )
        .filter(e.archived_at.is_(None), e.created_at >= start, e.created_at < end)
        .order_by(e.created_at.desc(), e.id)
        .all()
    )
    if not rows:
        return []
    stages = {
        r.enquiry_id: r.stage
        for r in db.execute(
            status_svc.enquiry_status_select(
                ctx.org_id,
                now,
                valid_tz=status_svc.valid_timezones(db, ctx.org_id),
                enquiry_ids=[r[0] for r in rows],
            )
        )
    }
    out = []
    for enquiry_id, subject, contact, venue_id, venue_name, created_at in rows:
        stage = stages.get(enquiry_id)
        outcome = stage if stage in ("confirmed", "lost") else "open"
        out.append(
            {
                "id": enquiry_id,
                "subject": subject,
                "contact": contact,
                "venue_id": venue_id,
                "venue": venue_name,
                "received": _iso(created_at),
                "stage": stage,
                "outcome": outcome,
                "counted": outcome != "open",
            }
        )
    return out


def _display_names(db: Session, ctx: FunctionsCtx, enquiry_ids) -> dict:
    """``{enquiry_id: (subject, contact name, venue name, follow-up
    reason)}`` — plain columns by id, no derived state."""
    ids = list(dict.fromkeys(enquiry_ids))
    if not ids:
        return {}
    e = FnEnquiry
    return {
        r[0]: (r[1], r[2], r[3], r[4])
        for r in db.query(
            e.id, e.subject, FnContact.name, Venue.name, e.follow_up_reason
        )
        .join(
            FnContact,
            and_(
                FnContact.organization_id == e.organization_id,
                FnContact.id == e.contact_id,
            ),
        )
        .outerjoin(Venue, Venue.id == e.venue_id)
        .filter(e.organization_id == ctx.org_id, e.id.in_(ids))
    }


def needs_attention(
    db: Session,
    ctx: FunctionsCtx,
    *,
    venue_ids: list[str] | None = None,
    unassigned: bool = False,
    now: dt.datetime | None = None,
    horizon_days: int = 14,
    limit: int = 100,
    counts: EnquiryCounts | None = None,
    events=None,
) -> list[dict]:
    """What staff owe today, most urgent first:

    * ``reply_overdue`` / ``requested`` / ``day_14`` / ``day_7`` — open
      enquiries whose derived next action is due today or overdue;
    * ``quote_response`` — a customer answered on the quote page and nobody
      has acted since: a decline with no newer option or reply, or a
      confirmation whose booking hasn't had its portal link sent;
    * ``client_edit`` — an upcoming confirmed event the client edited after
      confirming;
    * ``unconfirmed_event`` — an event in the next ``horizon_days`` days not
      yet confirmed.

    ``counts`` (the caller's enquiry counts, which carry the due ones) and
    ``events`` (their upcoming ``list_events`` rows) save the queries when
    already loaded — the dashboard has both.
    """
    now = _now(now)
    today = now.astimezone(org_tz(db, ctx)).date()
    if counts is None:
        counts = enquiry_counts(
            db, ctx, now=now, venue_ids=venue_ids, unassigned=unassigned
        )
    quote_items = _quote_responses(
        db, ctx, venue_ids=venue_ids, unassigned=unassigned, now=now
    )
    names = _display_names(
        db,
        ctx,
        [d["id"] for d in counts.due] + [q["enquiry_id"] for q in quote_items],
    )
    items: list[dict] = []
    for d in counts.due:
        subject, contact_name, venue_name, reason = names.get(
            d["id"], (None, None, None, None)
        )
        items.append(
            {
                "kind": d["kind"],
                "label": status_svc.NEXT_ACTION_LABELS.get(d["kind"]),
                "enquiry_id": d["id"],
                "event_id": None,
                "title": subject or contact_name,
                "contact_name": contact_name,
                "venue_name": venue_name,
                "date": d["due"],
                "overdue": bool(d["overdue"]),
                "detail": None,
                "follow_up_reason": reason,
            }
        )
    for item in quote_items:
        reason = names.get(item["enquiry_id"], (None,) * 4)[3]
        items.append({**item, "follow_up_reason": reason})

    if events is not None:
        upcoming = events
    elif unassigned:
        upcoming = []  # every event has a venue
    else:
        upcoming = events_svc.list_events(
            db, ctx, venue_ids=venue_ids, date_from=today, limit=2000
        )
    horizon = today + dt.timedelta(days=horizon_days)
    for r in upcoming:
        base = {
            "enquiry_id": r.event.enquiry_id,
            "event_id": r.event.id,
            "title": r.event.name,
            "contact_name": r.contact_name,
            "venue_name": r.venue_name,
            "date": r.event.event_date.isoformat(),
            "follow_up_reason": None,
        }
        if r.edited_since_confirmed:
            items.append(
                {
                    "kind": "client_edit",
                    "label": "Client edited after confirming",
                    "overdue": False,
                    "detail": "Review the client's changes.",
                    **base,
                }
            )
        if r.status in UNCONFIRMED and r.event.event_date < horizon:
            items.append(
                {
                    "kind": "unconfirmed_event",
                    "label": "Event not confirmed",
                    "overdue": r.event.event_date <= today + dt.timedelta(days=2),
                    "detail": f"Status: {r.status.replace('_', ' ')}.",
                    **base,
                }
            )

    items.sort(
        key=lambda i: (
            not i["overdue"],
            ATTENTION_ORDER.get(i["kind"], 9),
            i["date"] or "9999",
            (i["title"] or "").lower(),  # ties in a stable, readable order
            i["enquiry_id"] or "",
            i["event_id"] or "",
        )
    )
    return items[:limit]


def _quote_responses(
    db: Session, ctx: FunctionsCtx, *, venue_ids, unassigned: bool, now: dt.datetime
) -> list[dict]:
    """Quote-page answers nobody has acted on (see ``needs_attention``). The
    recent quote-page responses are found first; option states, enquiry
    facts and event statuses are then computed for those records alone."""
    q = _enquiry_scope(
        db.query(FnMinSpendResponse, FnMinSpend)
        .join(
            FnMinSpend,
            and_(
                FnMinSpend.organization_id == FnMinSpendResponse.organization_id,
                FnMinSpend.id == FnMinSpendResponse.min_spend_id,
            ),
        )
        .join(
            FnEnquiry,
            and_(
                FnEnquiry.organization_id == FnMinSpend.organization_id,
                FnEnquiry.id == FnMinSpend.enquiry_id,
            ),
        ),
        ctx,
        venue_ids,
        unassigned,
    ).filter(
        FnMinSpendResponse.channel == "quote_page",
        FnMinSpendResponse.occurred_at >= now - dt.timedelta(days=60),
        ctx.venue_clause(FnMinSpend.venue_id),
    )
    if venue_ids:
        q = q.filter(FnMinSpend.venue_id.in_(venue_ids))
    rows = q.order_by(FnMinSpendResponse.occurred_at.desc()).all()
    if not rows:
        return []
    enquiry_ids = sorted({option.enquiry_id for _, option in rows})
    facts = {
        f.id: f
        for f in enquiry_facts(db, ctx, now=now, enquiry_ids=enquiry_ids)
        if not f.archived
    }
    ms = status_svc.min_spend_status_select(
        ctx.org_id, enquiry_ids=enquiry_ids
    ).subquery("mss")
    states = dict(db.query(ms.c.min_spend_id, ms.c.state))
    newest_option = dict(
        db.query(FnMinSpend.enquiry_id, func.max(FnMinSpend.created_at))
        .filter(
            FnMinSpend.organization_id == ctx.org_id,
            FnMinSpend.enquiry_id.in_(enquiry_ids),
        )
        .group_by(FnMinSpend.enquiry_id)
    )
    event_ids = sorted({option.event_id for _, option in rows if option.event_id})
    event_status = {}
    if event_ids:
        st = status_svc.event_status_select(ctx.org_id, event_ids=event_ids).subquery(
            "est"
        )
        event_status = dict(db.query(st.c.event_id, st.c.status))
    venue_names = dict(
        db.query(Venue.id, Venue.name).filter(
            Venue.id.in_({option.venue_id for _, option in rows})
        )
    )
    out, seen = [], set()
    for resp, option in rows:
        f = facts.get(option.enquiry_id)
        if f is None or option.id in seen:
            continue
        seen.add(option.id)  # the latest answer per option is the one that counts
        base = {
            "kind": "quote_response",
            "enquiry_id": f.id,
            "title": f.subject or f.contact_name,
            "contact_name": f.contact_name,
            "venue_name": venue_names.get(option.venue_id),
            "date": resp.occurred_at.date().isoformat(),
            "overdue": False,
        }
        if resp.kind == "decline" and states.get(option.id) == "declined":
            replied = (
                f.last_outbound_at is not None and f.last_outbound_at > resp.occurred_at
            )
            newer = newest_option.get(f.id)
            if f.open and not replied and not (newer and newer > resp.occurred_at):
                out.append(
                    {
                        **base,
                        "event_id": None,
                        "label": "Customer declined an option",
                        "detail": option.label or "A minimum-spend option",
                    }
                )
        elif resp.kind == "confirm" and option.event_id:
            if event_status.get(option.event_id) == "booked":
                out.append(
                    {
                        **base,
                        "event_id": option.event_id,
                        "label": "New booking from the quote page",
                        "detail": "Send the client their portal link.",
                    }
                )
    return out


def dashboard(
    db: Session,
    ctx: FunctionsCtx,
    *,
    venue: str | None = None,
    now: dt.datetime | None = None,
) -> dict:
    """Everything the Functions dashboard shows, in one call: one grouped
    read of the enquiry counts, one of the upcoming events, and small reads
    narrowed to the open enquiries. ``venue``: one venue, ``"unassigned"``
    (group-wide enquiries — which have no events), or empty for all of the
    caller's venues."""
    now = _now(now)
    venue_ids, unassigned = venue_filter(ctx, venue)
    tz = org_tz(db, ctx, venue)
    today = now.astimezone(tz).date()
    counts = enquiry_counts(
        db,
        ctx,
        now=now,
        venue_ids=venue_ids,
        unassigned=unassigned,
        cohort=_cohort(now, 4, 4),
    )
    last4 = counts.by_stage("last_28")
    received = sum(last4.values())
    prior = sum(counts.by_stage("prior_28").values())
    # Every event has a venue, so the group-wide view has none of them.
    booked = (
        []
        if unassigned
        else events_svc.list_events(
            db, ctx, venue_ids=venue_ids, date_from=today, limit=2000
        )
    )
    horizon = today + dt.timedelta(days=14)
    return {
        "today": today.isoformat(),
        "timezone": str(tz),
        "enquiries_last_28_days": {
            "count": received,
            "prior": prior,
            "change_pct": _pct(received - prior, prior),
        },
        "open_enquiries": enquiry_kpis(db, ctx, counts=counts),
        "booked_events": {
            "count": len(booked),
            "expected_spend_cents": sum(
                int(r.expected_spend_cents or 0) for r in booked
            ),
        },
        "next_4_weeks": expected_sales(
            db, ctx, venue_ids=venue_ids, start=today, rows=booked
        ),
        "conversion": conversion(db, ctx, now=now, counts=counts),
        "pipeline": {
            "open": {
                k: v
                for k, v in counts.by_stage("all").items()
                if k in status_svc.OPEN_STAGES
            },
            "last_28_days": last4,
        },
        "events_unconfirmed_14_days": sum(
            1
            for r in booked
            if r.status in UNCONFIRMED and r.event.event_date < horizon
        ),
        # Group-wide enquiries still owe replies and follow-ups; they just
        # have no events (``booked`` is empty for them).
        "needs_attention": needs_attention(
            db,
            ctx,
            venue_ids=venue_ids,
            unassigned=unassigned,
            now=now,
            counts=counts,
            events=booked,
        ),
    }


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def enquiries_over_time(
    db: Session,
    ctx: FunctionsCtx,
    *,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    venue: str | None = None,
    stage: str | None = None,
    now: dt.datetime | None = None,
) -> dict:
    """Enquiries received per day (zero-filled) over ``date_from`` ..
    ``date_to`` (inclusive, default the last 28 days), with the same-length
    period just before it for comparison."""
    now = _now(now)
    venue_ids, unassigned = venue_filter(ctx, venue)
    tz = org_tz(db, ctx, venue)
    today = now.astimezone(tz).date()
    date_to = date_to or today
    date_from = date_from or date_to - dt.timedelta(days=27)
    if date_from > date_to:
        raise Invalid("The report starts after it ends.")
    days = (date_to - date_from).days + 1
    if days > 400:
        raise Invalid("Pick a range of at most 400 days.")
    if stage is not None and stage not in status_svc.STAGES:
        raise Invalid(f"Stage must be one of {', '.join(status_svc.STAGES)}.")
    prior_to = date_from - dt.timedelta(days=1)
    prior_from = prior_to - dt.timedelta(days=days - 1)

    def _in(start: dt.date, end: dt.date):
        return (
            FnEnquiry.created_at >= to_instant(tz, start),
            FnEnquiry.created_at < to_instant(tz, end + dt.timedelta(days=1)),
        )

    stage_of = None
    if stage is not None:
        # Stages for the two windows' enquiries only, not the whole org.
        ids = [
            r[0]
            for r in _enquiry_scope(
                db.query(FnEnquiry.id), ctx, venue_ids, unassigned
            ).filter(FnEnquiry.archived_at.is_(None), *_in(prior_from, date_to))
        ]
        stage_of = status_svc.enquiry_status_select(
            ctx.org_id,
            now,
            valid_tz=status_svc.valid_timezones(db, ctx.org_id),
            enquiry_ids=ids,
        ).subquery("st")

    def _counts(start: dt.date, end: dt.date) -> dict[dt.date, int]:
        local_day = func.date(func.timezone(str(tz), FnEnquiry.created_at))
        q = db.query(local_day, func.count(FnEnquiry.id)).select_from(FnEnquiry)
        if stage_of is not None:
            q = q.join(stage_of, stage_of.c.enquiry_id == FnEnquiry.id).filter(
                stage_of.c.stage == stage
            )
        q = _enquiry_scope(q, ctx, venue_ids, unassigned).filter(
            FnEnquiry.archived_at.is_(None), *_in(start, end)
        )
        return {d: int(n) for d, n in q.group_by(local_day).all()}

    current, before = _counts(date_from, date_to), _counts(prior_from, prior_to)
    series = []
    for i in range(days):
        day = date_from + dt.timedelta(days=i)
        prior_day = prior_from + dt.timedelta(days=i)
        series.append(
            {
                "date": day.isoformat(),
                "count": current.get(day, 0),
                "prior_date": prior_day.isoformat(),
                "prior_count": before.get(prior_day, 0),
            }
        )
    total, prior_total = sum(current.values()), sum(before.values())
    return {
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "prior_from": prior_from.isoformat(),
        "prior_to": prior_to.isoformat(),
        "timezone": str(tz),
        "total": total,
        "prior_total": prior_total,
        "change": total - prior_total,
        "change_pct": _pct(total - prior_total, prior_total),
        "days": series,
    }


def upcoming_events(
    db: Session,
    ctx: FunctionsCtx,
    *,
    days: int | None = None,
    venue: str | None = None,
    statuses: list[str] | None = None,
    now: dt.datetime | None = None,
) -> dict:
    """Live events from today (optionally the next ``days`` days), with
    totals of guests and expected spend."""
    now = _now(now)
    venue_ids, _ = venue_filter(ctx, venue if venue != "unassigned" else None)
    today = now.astimezone(org_tz(db, ctx, venue)).date()
    rows = events_svc.list_events(
        db,
        ctx,
        venue_ids=venue_ids,
        date_from=today,
        date_to=today + dt.timedelta(days=int(days) - 1) if days else None,
        statuses=[s for s in (statuses or []) if s != "cancelled"] or None,
        limit=2000,
    )
    return {
        "from": today.isoformat(),
        "days": days,
        "rows": rows,
        "totals": {
            "events": len(rows),
            "guests": sum(int(r.event.guests or 0) for r in rows),
            "expected_spend_cents": sum(int(r.expected_spend_cents or 0) for r in rows),
        },
    }


def _channel(utm_source, utm_medium, utm_campaign, click_id, fallback):
    """Orbit's attribution grouping: a click id with no UTM is Google paid."""
    source = utm_source or ("google" if click_id else fallback)
    medium = utm_medium or ("cpc" if click_id else "(none)")
    return source, medium, utm_campaign or "(not set)"


def lead_sources(
    db: Session,
    ctx: FunctionsCtx,
    *,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    venue: str | None = None,
    now: dt.datetime | None = None,
) -> dict:
    """Where enquiries (and web-form leads) came from over a window
    (default the last 90 days): by channel, and by source / medium /
    campaign with what they converted to."""
    now = _now(now)
    venue_ids, unassigned = venue_filter(ctx, venue)
    tz = org_tz(db, ctx, venue)
    date_to = date_to or now.astimezone(tz).date()
    date_from = date_from or date_to - dt.timedelta(days=89)
    if date_from > date_to:
        raise Invalid("The report starts after it ends.")
    start, end = (
        to_instant(tz, date_from),
        to_instant(tz, date_to + dt.timedelta(days=1)),
    )
    # The window's enquiries first; stages and booked values are then
    # computed for them (and their live events) alone.
    window_ids = [
        r[0]
        for r in _enquiry_scope(
            db.query(FnEnquiry.id), ctx, venue_ids, unassigned
        ).filter(
            FnEnquiry.archived_at.is_(None),
            FnEnquiry.created_at >= start,
            FnEnquiry.created_at < end,
        )
    ]
    rows = []
    if window_ids:
        s = status_svc.enquiry_status_select(
            ctx.org_id,
            now,
            valid_tz=status_svc.valid_timezones(db, ctx.org_id),
            enquiry_ids=window_ids,
        ).subquery("st")
        event_ids = [
            r[0]
            for r in db.query(FnEvent.id).filter(
                FnEvent.organization_id == ctx.org_id,
                FnEvent.enquiry_id.in_(window_ids),
                FnEvent.cancelled_at.is_(None),
                ctx.venue_clause(
                    FnEvent.venue_id
                ),  # booked value at hidden venues stays hidden
            )
        ]
        mo = money_svc.event_money_select(ctx.org_id, event_ids=event_ids).subquery(
            "emo"
        )
        live_value = (
            db.query(
                FnEvent.enquiry_id, func.sum(mo.c.expected_spend_cents).label("value")
            )
            .join(mo, mo.c.event_id == FnEvent.id)
            .filter(FnEvent.organization_id == ctx.org_id, FnEvent.id.in_(event_ids))
            .group_by(FnEvent.enquiry_id)
            .subquery("lv")
        )
        rows = (
            db.query(
                FnEnquiry.source,
                FnEnquiry.utm_source,
                FnEnquiry.utm_medium,
                FnEnquiry.utm_campaign,
                FnEnquiry.gclid,
                s.c.stage,
                live_value.c.value,
            )
            .join(s, s.c.enquiry_id == FnEnquiry.id)
            .outerjoin(live_value, live_value.c.enquiry_id == FnEnquiry.id)
            .filter(
                FnEnquiry.organization_id == ctx.org_id,
                FnEnquiry.id.in_(window_ids),
            )
            .all()
        )
    by_channel: dict[str, dict] = {}
    by_campaign: dict[tuple, dict] = {}
    for source, utm_s, utm_m, utm_c, gclid, stage, value in rows:
        for key, table in (
            (source, by_channel),
            (_channel(utm_s, utm_m, utm_c, gclid, source), by_campaign),
        ):
            agg = table.setdefault(
                key,
                {"enquiries": 0, "confirmed": 0, "lost": 0, "confirmed_value_cents": 0},
            )
            agg["enquiries"] += 1
            if stage == "confirmed":
                agg["confirmed"] += 1
                agg["confirmed_value_cents"] += int(value or 0)
            elif stage == "lost":
                agg["lost"] += 1

    def _rate(a):
        return _pct(a["confirmed"], a["confirmed"] + a["lost"])

    sub = db.query(FnFormSubmission).filter(
        FnFormSubmission.organization_id == ctx.org_id,
        ctx.venue_clause(FnFormSubmission.venue_id),
        FnFormSubmission.submitted_at >= start,
        FnFormSubmission.submitted_at < end,
        FnFormSubmission.spam_reason.is_(None),
    )
    if unassigned:
        sub = sub.filter(false())
    elif venue_ids:
        sub = sub.filter(FnFormSubmission.venue_id.in_(venue_ids))
    leads: dict[tuple, dict] = {}
    for f in sub.all():
        key = _channel(
            f.utm_source,
            f.utm_medium,
            f.utm_campaign,
            f.gclid or f.gbraid or f.wbraid,
            "direct",
        )
        agg = leads.setdefault(key, {"leads": 0, "enquiries": 0, "with_click_id": 0})
        agg["leads"] += 1
        agg["enquiries"] += 1 if f.enquiry_id else 0
        agg["with_click_id"] += 1 if (f.gclid or f.gbraid or f.wbraid) else 0
    return {
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "enquiries": len(rows),
        "by_channel": sorted(
            ({"source": k, **v, "rate_pct": _rate(v)} for k, v in by_channel.items()),
            key=lambda r: (-r["enquiries"], r["source"]),
        ),
        "by_campaign": sorted(
            (
                {
                    "source": k[0],
                    "medium": k[1],
                    "campaign": k[2],
                    **v,
                    "rate_pct": _rate(v),
                }
                for k, v in by_campaign.items()
            ),
            key=lambda r: (-r["confirmed_value_cents"], -r["enquiries"]),
        ),
        "form_leads": sorted(
            (
                {"source": k[0], "medium": k[1], "campaign": k[2], **v}
                for k, v in leads.items()
            ),
            key=lambda r: (-r["leads"], r["source"]),
        ),
    }


def approvals_log(
    db: Session,
    ctx: FunctionsCtx,
    *,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    venue: str | None = None,
    kind: str | None = None,
    now: dt.datetime | None = None,
    limit: int = 2000,
) -> dict:
    """Every approve / override / revoke at the caller's venues over a window
    (default the last 90 days), newest first, with totals."""
    now = _now(now)
    venue_ids, _ = venue_filter(ctx, venue if venue != "unassigned" else None)
    tz = org_tz(db, ctx, venue)
    date_to = date_to or now.astimezone(tz).date()
    date_from = date_from or date_to - dt.timedelta(days=89)
    if kind is not None and kind not in ("approve", "override", "revoke"):
        raise Invalid("kind must be approve, override or revoke.")
    a = FnMinSpendApproval
    q = (
        db.query(
            a,
            FnMinSpend,
            FnEnquiry,
            FnContact.name,
            FnContact.company,
            Venue.name,
            FnVenueArea.name,
        )
        .join(
            FnMinSpend,
            and_(
                FnMinSpend.organization_id == a.organization_id,
                FnMinSpend.id == a.min_spend_id,
            ),
        )
        # The option is at the caller's venue, but its enquiry may not be
        # theirs to see (an enquiry at another venue): then no subject, no
        # contact, no link.
        .outerjoin(
            FnEnquiry,
            and_(
                FnEnquiry.organization_id == FnMinSpend.organization_id,
                FnEnquiry.id == FnMinSpend.enquiry_id,
                ctx.venue_clause(FnEnquiry.venue_id, allow_null=True),
                ctx.record_clause(FnEnquiry),
            ),
        )
        .outerjoin(
            FnContact,
            and_(
                FnContact.organization_id == FnEnquiry.organization_id,
                FnContact.id == FnEnquiry.contact_id,
            ),
        )
        .join(Venue, Venue.id == FnMinSpend.venue_id)
        .outerjoin(
            FnVenueArea,
            and_(
                FnVenueArea.organization_id == FnMinSpend.organization_id,
                FnVenueArea.id == FnMinSpend.area_id,
            ),
        )
        .filter(
            a.organization_id == ctx.org_id,
            ctx.venue_clause(FnMinSpend.venue_id),
            a.occurred_at >= to_instant(tz, date_from),
            a.occurred_at < to_instant(tz, date_to + dt.timedelta(days=1)),
        )
    )
    if venue_ids:
        q = q.filter(FnMinSpend.venue_id.in_(venue_ids))
    if kind:
        q = q.filter(a.kind == kind)
    rows = (
        q.order_by(a.occurred_at.desc(), a.created_at.desc())
        .limit(min(limit, 5000))
        .all()
    )
    names = user_names(db, [r[0].actor_user_id for r in rows])
    out = []
    totals = {
        "approve": {"count": 0, "cents": 0},
        "override": {"count": 0, "cents": 0},
        "revoke": {"count": 0, "automatic": 0},
    }
    for appr, option, enquiry, contact, company, venue_name, area_name in rows:
        if appr.kind == "revoke":
            totals["revoke"]["count"] += 1
            totals["revoke"]["automatic"] += 1 if appr.automatic else 0
        else:
            totals[appr.kind]["count"] += 1
            totals[appr.kind]["cents"] += int(appr.amount_cents or 0)
        out.append(
            {
                "id": appr.id,
                "occurred_at": _iso(appr.occurred_at),
                "kind": appr.kind,
                "automatic": bool(appr.automatic),
                "actor": "Norm (option changed)"
                if appr.automatic
                else _actor(names, appr.actor_user_id, appr.actor_label),
                "reason": appr.reason,
                "amount_cents": appr.amount_cents,
                "min_spend_id": option.id,
                "option_label": option.label,
                "enquiry_id": enquiry.id if enquiry is not None else None,
                "enquiry_subject": enquiry.subject if enquiry is not None else None,
                "contact": company or contact,
                "venue_id": option.venue_id,
                "venue_name": venue_name,
                "area_name": area_name,
            }
        )
    return {
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "rows": out,
        "totals": totals,
    }


def calendar_month(
    db: Session,
    ctx: FunctionsCtx,
    *,
    month: str,
    venue_ids: list[str] | None = None,
    statuses: list[str] | None = None,
    include_cancelled: bool = False,
    q: str | None = None,
) -> dict:
    """A Monday-first month grid: every day from the Monday on or before the
    1st to the Sunday on or after the last day, each with its events and
    their summed expected spend. Cancelled events are left out (and out of
    the totals) unless asked for."""
    try:
        first = dt.date.fromisoformat(f"{month}-01")
    except ValueError as exc:
        raise Invalid("month must look like 2026-12.") from exc
    nxt = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    last = nxt - dt.timedelta(days=1)
    grid_start = first - dt.timedelta(days=first.weekday())
    grid_end = last + dt.timedelta(days=6 - last.weekday())
    rows = events_svc.list_events(
        db,
        ctx,
        venue_ids=venue_ids,
        date_from=grid_start,
        date_to=grid_end,
        statuses=statuses,
        include_cancelled=include_cancelled,
        q=q,
        limit=2000,
    )
    by_day: dict[dt.date, list] = defaultdict(list)
    for r in rows:
        by_day[r.event.event_date].append(r)
    days = []
    day = grid_start
    month_total = 0
    while day <= grid_end:
        events = by_day.get(day, [])
        total = sum(
            int(r.expected_spend_cents or 0) for r in events if r.status != "cancelled"
        )
        if day.month == first.month:
            month_total += total
        days.append(
            {
                "date": day.isoformat(),
                "in_month": day.month == first.month,
                "expected_spend_cents": total,
                "events": events,
            }
        )
        day += dt.timedelta(days=1)
    return {
        "month": first.strftime("%Y-%m"),
        "from": grid_start.isoformat(),
        "to": grid_end.isoformat(),
        "expected_spend_cents": month_total,
        "events": sum(1 for r in rows if r.event.event_date.month == first.month),
        "days": days,
    }


def contact_counts(db: Session, ctx: FunctionsCtx, contact_ids) -> dict[str, dict]:
    """Per contact: enquiries (non-archived, at the caller's venues), their
    stages, live bookings, and the conversion rate on the same rule as the
    reports — confirmed ÷ (confirmed + lost)."""
    ids = list(contact_ids)
    out = {
        c: {"enquiries": 0, "open": 0, "confirmed": 0, "lost": 0, "events": 0}
        for c in ids
    }
    if not ids:
        return out
    # Their enquiries first (an index read), so the status select computes
    # facts for those alone rather than for the whole organisation.
    enquiry_ids = [
        r[0]
        for r in _enquiry_scope(db.query(FnEnquiry.id), ctx, None, False).filter(
            FnEnquiry.contact_id.in_(ids), FnEnquiry.archived_at.is_(None)
        )
    ]
    s = (
        status_svc.enquiry_status_select(
            ctx.org_id,
            _now(None),
            valid_tz=status_svc.valid_timezones(db, ctx.org_id),
            enquiry_ids=enquiry_ids,
        ).subquery("st")
        if enquiry_ids
        else None
    )
    for contact_id, stage, n in (
        db.query(FnEnquiry.contact_id, s.c.stage, func.count())
        .join(s, s.c.enquiry_id == FnEnquiry.id)
        .filter(
            FnEnquiry.organization_id == ctx.org_id,
            FnEnquiry.id.in_(enquiry_ids),
        )
        .group_by(FnEnquiry.contact_id, s.c.stage)
        if s is not None
        else []
    ):
        agg = out[contact_id]
        agg["enquiries"] += n
        if stage in ("confirmed", "lost"):
            agg[stage] += n
        else:
            agg["open"] += n
    for contact_id, n in (
        db.query(FnEvent.contact_id, func.count())
        .filter(
            FnEvent.organization_id == ctx.org_id,
            ctx.venue_clause(FnEvent.venue_id),
            FnEvent.contact_id.in_(ids),
            FnEvent.cancelled_at.is_(None),
        )
        .group_by(FnEvent.contact_id)
    ):
        out[contact_id]["events"] = int(n)
    for agg in out.values():
        agg["conversion_pct"] = _pct(agg["confirmed"], agg["confirmed"] + agg["lost"])
    return out


# ---------------------------------------------------------------------------
# Activity timeline (enquiry detail)
# ---------------------------------------------------------------------------


def enquiry_timeline(
    db: Session, ctx: FunctionsCtx, enquiry_id: str, *, mail_limit: int = 200
) -> list[dict]:
    """Everything that happened to an enquiry, newest first: its creation,
    closures, options and their approvals and customer answers, proposal
    sends and views, thread links, mail on its linked threads, and the life
    of the events it booked. Each item: ``{at, kind, title, detail, actor,
    ref}``."""
    enquiry = enquiries_svc.get_enquiry(db, ctx, enquiry_id)
    org = ctx.org_id
    items: list[dict] = []
    users: set[str] = (
        {enquiry.created_by_user_id} if enquiry.created_by_user_id else set()
    )

    def add(at, kind, title, *, detail=None, user_id=None, label=None, ref=None):
        if at is None:
            return
        if user_id:
            users.add(user_id)
        items.append(
            {
                "at": at,
                "kind": kind,
                "title": title,
                "detail": detail,
                "_user": user_id,
                "_label": label,
                "ref": ref or {},
            }
        )

    add(
        enquiry.created_at,
        "enquiry_created",
        "Enquiry created",
        detail=enquiry.subject,
        user_id=enquiry.created_by_user_id,
        label=enquiry.created_by_label,
    )
    for c in db.query(FnEnquiryClosure).filter(
        FnEnquiryClosure.organization_id == org,
        FnEnquiryClosure.enquiry_id == enquiry.id,
    ):
        add(
            c.occurred_at,
            "enquiry_lost" if c.kind == "lost" else "enquiry_reopened",
            "Closed as lost" if c.kind == "lost" else "Reopened",
            detail=c.reason,
            user_id=c.actor_user_id,
            label=c.actor_label,
        )

    options = (
        db.query(FnMinSpend)
        .filter(
            FnMinSpend.organization_id == org,
            FnMinSpend.enquiry_id == enquiry.id,
            ctx.venue_clause(FnMinSpend.venue_id),
        )
        .all()
    )
    option_by_id = {o.id: o for o in options}
    currency = get_company_settings(db, org).currency

    def _opt(o) -> str:
        return (
            f"{o.label or 'Minimum spend'} · {format_money(o.amount_cents, currency)}"
        )

    for o in options:
        add(
            o.created_at,
            "option_added",
            "Minimum-spend option added",
            detail=_opt(o),
            user_id=o.created_by_user_id,
            label=o.created_by_label,
            ref={"min_spend_id": o.id},
        )
        if o.withdrawn_at:
            add(
                o.withdrawn_at,
                "option_withdrawn",
                "Option withdrawn",
                detail=(o.withdraw_reason or _opt(o)),
                user_id=o.withdrawn_by_user_id,
                ref={"min_spend_id": o.id},
            )
    if option_by_id:
        titles = {
            "approve": "Option approved",
            "override": "Option approved (override)",
            "revoke": "Approval revoked",
        }
        for a in db.query(FnMinSpendApproval).filter(
            FnMinSpendApproval.organization_id == org,
            FnMinSpendApproval.min_spend_id.in_(list(option_by_id)),
        ):
            o = option_by_id[a.min_spend_id]
            add(
                a.occurred_at,
                f"option_{a.kind}",
                titles[a.kind] + (" — changed after approval" if a.automatic else ""),
                detail=" — ".join(x for x in (_opt(o), a.reason) if x),
                user_id=a.actor_user_id,
                label=a.actor_label,
                ref={"min_spend_id": o.id},
            )
        for r in db.query(FnMinSpendResponse).filter(
            FnMinSpendResponse.organization_id == org,
            FnMinSpendResponse.min_spend_id.in_(list(option_by_id)),
        ):
            o = option_by_id[r.min_spend_id]
            how = r.channel.replace("_", " ")
            add(
                r.occurred_at,
                "customer_confirmed" if r.kind == "confirm" else "customer_declined",
                ("Customer confirmed" if r.kind == "confirm" else "Customer declined")
                + f" ({how})",
                detail=" — ".join(x for x in (_opt(o), r.note) if x),
                user_id=r.recorded_by_user_id,
                label=r.customer_email if not r.recorded_by_user_id else None,
                ref={"min_spend_id": o.id},
            )

    numbers = dict(
        db.query(FnProposalVersion.id, FnProposalVersion.version_number).filter(
            FnProposalVersion.organization_id == org,
            FnProposalVersion.enquiry_id == enquiry.id,
        )
    )
    for p in db.query(FnProposalEvent).filter(
        FnProposalEvent.organization_id == org,
        FnProposalEvent.enquiry_id == enquiry.id,
    ):
        number = numbers.get(p.proposal_version_id)
        add(
            p.occurred_at,
            f"proposal_{p.kind}",
            ("Proposal sent" if p.kind == "sent" else "Proposal viewed")
            + (f" (version {number})" if number else ""),
            detail={
                "outbox": "From Norm",
                "gmail": "Sent from Gmail",
                "quote_page": "On the quote page",
                "import": "Imported",
            }.get(p.source),
            user_id=p.actor_user_id,
            label=p.actor_label,
            ref={"proposal_version_id": p.proposal_version_id},
        )

    links = (
        db.query(FnThreadLink, FnMailboxAccount.google_email)
        .join(
            FnMailboxAccount,
            and_(
                FnMailboxAccount.organization_id == FnThreadLink.organization_id,
                FnMailboxAccount.id == FnThreadLink.account_id,
            ),
        )
        .filter(
            FnThreadLink.organization_id == org,
            FnThreadLink.enquiry_id == enquiry.id,
            ctx.venue_clause(FnMailboxAccount.venue_id),
        )
        .all()
    )
    for link, mailbox in links:
        add(
            link.linked_at,
            "thread_linked",
            "Email thread linked",
            detail=mailbox,
            user_id=link.linked_by_user_id,
            label=link.linked_by_label,
            ref={"link_id": link.id, "gmail_thread_id": link.gmail_thread_id},
        )
        if link.unlinked_at:
            add(
                link.unlinked_at,
                "thread_unlinked",
                "Email thread unlinked",
                detail=mailbox,
                user_id=link.unlinked_by_user_id,
                ref={"link_id": link.id},
            )
    live = [
        (lk.account_id, lk.gmail_thread_id) for lk, _ in links if lk.unlinked_at is None
    ]
    if live:
        m = FnMailboxMessage
        msgs = (
            db.query(m)
            .filter(
                m.organization_id == org,
                or_(
                    *[and_(m.account_id == a, m.gmail_thread_id == t) for a, t in live]
                ),
            )
            .order_by(m.internal_date.desc())
            .limit(mail_limit)
            .all()
        )
        for msg in msgs:
            direction = "in" if msg.is_inbound else "out"
            add(
                msg.internal_date,
                f"email_{direction}",
                ("Email from " if msg.is_inbound else "Email sent by ")
                + (msg.from_name or msg.from_email or "unknown"),
                detail=msg.subject
                if not msg.snippet
                else f"{msg.subject or ''} — {msg.snippet}".strip(" —"),
                ref={
                    "account_id": msg.account_id,
                    "gmail_thread_id": msg.gmail_thread_id,
                    "gmail_message_id": msg.gmail_message_id,
                    "internal": bool(msg.is_internal),
                    "auto_reply": bool(msg.is_auto_reply),
                },
            )

    event_ids = [
        r[0]
        for r in db.query(FnEvent.id).filter(
            FnEvent.organization_id == org,
            FnEvent.enquiry_id == enquiry.id,
            ctx.venue_clause(FnEvent.venue_id),
        )
    ]
    if event_ids:
        _event_items(db, ctx, event_ids, add)

    names = user_names(db, users)
    for item in items:
        item["actor"] = _actor(names, item.pop("_user"), item.pop("_label"))
        item["at"] = _iso(item["at"])
    items.sort(key=lambda i: i["at"], reverse=True)
    return items


_CHANGE_TITLES = {
    "created": "Event booked",
    "edited": "Event edited",
    "link_sent": "Portal link sent to the client",
    "cancelled": "Event cancelled",
    "reinstated": "Event reinstated",
}


def _event_items(db: Session, ctx: FunctionsCtx, event_ids, add) -> None:
    for ch in db.query(FnEventChange).filter(
        FnEventChange.organization_id == ctx.org_id,
        FnEventChange.event_id.in_(event_ids),
    ):
        title = _CHANGE_TITLES.get(ch.kind, ch.kind)
        if ch.kind == "edited":
            who = "the client" if ch.actor_kind == "client" else None
            title = f"Event edited{' by ' + who if who else ''}" + (
                f" ({ch.section})" if ch.section else ""
            )
            if ch.after_confirmation:
                title += " after confirmation"
        detail = ch.note
        if ch.kind == "edited" and isinstance(ch.changes, dict):
            fields = ", ".join(sorted(ch.changes))[:200]
            detail = " — ".join(x for x in (fields, ch.note) if x)
        if ch.kind == "link_sent" and isinstance(ch.changes, dict):
            detail = ", ".join(ch.changes.get("to") or []) or ch.note
        add(
            ch.occurred_at,
            f"event_{ch.kind}",
            title,
            detail=detail,
            user_id=ch.actor_user_id,
            label=ch.client_email or ch.actor_label,
            ref={"event_id": ch.event_id},
        )
    for sc in db.query(FnEventSectionConfirmation).filter(
        FnEventSectionConfirmation.organization_id == ctx.org_id,
        FnEventSectionConfirmation.event_id.in_(event_ids),
    ):
        verb = {"confirmed": "confirmed", "skipped": "skipped", "reopened": "reopened"}[
            sc.kind
        ]
        who = " by the client" if sc.actor_kind == "client" else ""
        add(
            sc.occurred_at,
            f"section_{sc.kind}",
            f"{sc.section.capitalize()} {verb}{who}",
            detail=sc.note,
            user_id=sc.actor_user_id,
            label=sc.client_email or sc.actor_label,
            ref={"event_id": sc.event_id, "section": sc.section},
        )


def event_timeline(db: Session, ctx: FunctionsCtx, event_id: str) -> list[dict]:
    """The event's own history (changes and section facts), newest first."""
    events_svc.get_event(db, ctx, event_id)
    items: list[dict] = []
    users: set[str] = set()

    def add(at, kind, title, *, detail=None, user_id=None, label=None, ref=None):
        if user_id:
            users.add(user_id)
        items.append(
            {
                "at": at,
                "kind": kind,
                "title": title,
                "detail": detail,
                "_user": user_id,
                "_label": label,
                "ref": ref or {},
            }
        )

    _event_items(db, ctx, [event_id], add)
    names = user_names(db, users)
    for item in items:
        item["actor"] = _actor(names, item.pop("_user"), item.pop("_label"))
        item["at"] = _iso(item["at"])
    items.sort(key=lambda i: i["at"], reverse=True)
    return items


# ---------------------------------------------------------------------------
# Budget / last-year reference (LoadedHub, through Norm's own tools)
# ---------------------------------------------------------------------------


def loaded_call(
    db: Session, config_db: Session, venue: Venue, action: str, params: dict
) -> tuple[object | None, str | None]:
    """One read of Norm's ``loadedhub`` tools for one venue — through
    ``execute_connector_tool``, the executor charts, apps and MCP share
    (venue-strict credentials, the venue's organisation as caller scope).
    Returns ``(payload, None)`` or ``(None, reason)``: never raises, because
    a reference figure that can't be fetched must not break the page that
    shows it. Tests replace this function."""
    try:
        from app.connectors.tool_executor import execute_connector_tool

        result = execute_connector_tool(
            "loadedhub",
            action,
            dict(params),
            db,
            config_db,
            venue_id=venue.id,
            strict_venue=True,
        )
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning("functions loaded_call %s failed: %s", action, exc)
        return None, "LoadedHub isn't reachable right now."
    if not result.success:
        return None, result.error or "LoadedHub returned an error."
    payload = result.payload
    if isinstance(payload, dict) and payload.get("error"):
        return None, str(payload["error"])
    return payload, None


def _cents(value) -> int | None:
    if isinstance(value, (int, float)):
        return int(round(value * 100))
    return None


def sales_reference(
    db: Session, config_db: Session, ctx: FunctionsCtx, *, venue_id: str, date: dt.date
) -> dict:
    """What the venue usually takes on that trading day, beside a minimum
    spend: its Loaded **budget** for the day and **last year's sales** on the
    same weekday (exactly 364 days back — get_sales' own rule, so it never
    drifts). Both tax-inclusive, as Loaded keeps them.

    One ``get_sales`` call (breakdown total, compare budget + last_year) over
    the venue's trading day for ``date``. ``available`` is False, with a
    ``reason``, when the venue has no LoadedHub or the call fails."""
    from app.services.business_calendar import day_start_for
    from app.services.functions.venue_profile import get_venue

    venue = get_venue(db, ctx, venue_id)
    out = {
        "venue_id": venue.id,
        "date": date.isoformat(),
        "weekday": date.strftime("%A"),
        "last_year_date": (date - dt.timedelta(days=364)).isoformat(),
        "available": False,
        "reason": None,
        "budget_cents": None,
        "last_year_cents": None,
        "actual_cents": None,
        "tax": "included",
    }
    try:
        tz = venue_tz(venue)
    except Invalid as exc:
        out["reason"] = exc.message
        return out
    hh, mm = (int(x) for x in day_start_for(venue).split(":"))
    start = dt.datetime.combine(date, dt.time(hh, mm), tzinfo=tz)
    end = start + dt.timedelta(days=1) - dt.timedelta(seconds=1)
    payload, error = loaded_call(
        db,
        config_db,
        venue,
        "get_sales",
        {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "venue": venue.name,
            "breakdown": "total",
            "compare": ["budget", "last_year"],
        },
    )
    if error is not None or not isinstance(payload, dict):
        out["reason"] = error or "LoadedHub returned nothing for that day."
        return out
    # The venue's own row first: get_sales leaves a venue with any error out
    # of the group totals, but its row keeps whichever figures did arrive (a
    # future day's sales may fail while its budget and last year are fine).
    totals = payload.get("totals") or {}
    row = next((r for r in payload.get("rows") or [] if isinstance(r, dict)), {})
    if row.get("errors"):
        out["reason"] = "; ".join(str(e) for e in row["errors"])

    def _figure(key):
        value = row.get(key)
        return _cents(value if value is not None else totals.get(key))

    out["budget_cents"] = _figure("budget")
    out["last_year_cents"] = _figure("last_year")
    out["actual_cents"] = _figure("actual")
    out["available"] = (
        out["budget_cents"] is not None or out["last_year_cents"] is not None
    )
    if not out["available"] and out["reason"] is None:
        out["reason"] = "No budget or last-year sales for that day."
    return out


# ---------------------------------------------------------------------------
# Print sheets
# ---------------------------------------------------------------------------

PRINT_KINDS = ("full", "run_sheet", "food")


def print_sheet(db: Session, ctx: FunctionsCtx, event_id: str, kind: str) -> dict:
    """What a printed sheet shows, as data the web lays out.

    * ``full`` — the event sheet: details, run sheet, beverages, food.
    * ``run_sheet`` — the same without the food (Orbit's run sheet).
    * ``food`` — the kitchen's pre-order: event basics and the food only,
      with dish counts summed across menus.

    Fields the venue switched off for this booking type are left out (Orbit's
    prints ignored the setting). Set menus are priced guests × per person
    (Orbit printed them at $0), and dietary tags print with each dish. Skip
    notes are internal and appear only on staff sheets — these are staff
    sheets; the client's print is the portal's (F5).
    """
    if kind not in PRINT_KINDS:
        raise Invalid(f"Print must be one of {', '.join(PRINT_KINDS)}.")
    from app.services.functions import menus as menus_svc
    from app.services.functions.venue_profile import enabled_fields_for, get_settings

    event = events_svc.get_event(db, ctx, event_id)
    state = events_svc.event_state(db, ctx, event.id)
    money = money_svc.event_expected_spend(db, event)
    venue = db.query(Venue).filter(Venue.id == event.venue_id).first()
    area = (
        db.query(FnVenueArea)
        .filter(
            FnVenueArea.organization_id == ctx.org_id, FnVenueArea.id == event.area_id
        )
        .first()
        if event.area_id
        else None
    )
    contact = (
        db.query(FnContact)
        .filter(
            FnContact.organization_id == ctx.org_id, FnContact.id == event.contact_id
        )
        .first()
    )
    email = (
        db.query(FnContactEmail.email)
        .filter(
            FnContactEmail.organization_id == ctx.org_id,
            FnContactEmail.contact_id == event.contact_id,
        )
        .order_by(FnContactEmail.is_primary.desc(), FnContactEmail.created_at)
        .first()
    )
    on = enabled_fields_for(get_settings(db, ctx, event.venue_id), event.booking_type)
    company = get_company_settings(db, ctx.org_id)

    def _t(value):
        return value.strftime("%H:%M") if value is not None else None

    sheet: dict = {
        "kind": kind,
        "generated_at": dt.datetime.now(UTC).isoformat(),
        "currency": company.currency,
        "tax_label": company.tax_label,
        "event": {
            "id": event.id,
            "name": event.name,
            "event_date": event.event_date.isoformat(),
            "start_time": _t(event.start_time),
            "end_time": _t(event.end_time),
            "ends_next_day": bool(event.ends_next_day),
            "guests": event.guests,
            "status": state.status,
            "edited_since_confirmed": state.edited_since_confirmed,
            "booking_type": event.booking_type,
            "booking_label": events_svc.BOOKING_LABELS.get(event.booking_type or ""),
            "venue_name": venue.name if venue else None,
            "area_name": area.name if area and on["details.area"] else None,
            "contact": {
                "name": contact.name if contact else None,
                "company": contact.company if contact else None,
                "email": email[0] if email else None,
                "phone": contact.phone if contact else None,
            },
        },
        "details": None,
        "timings": None,
        "beverages": None,
        "food": None,
        "skips": {},
    }
    for sc in events_svc.section_confirmations(db, ctx, event.id):
        if sc.kind == "skipped":
            sheet["skips"][sc.section] = {
                "note": sc.note,
                "at": _iso(sc.occurred_at),
                "by": sc.actor_label,
                "by_user_id": sc.actor_user_id,
            }
        elif sc.section in sheet["skips"]:
            sheet["skips"].pop(sc.section)
    names = user_names(db, [s["by_user_id"] for s in sheet["skips"].values()])
    for skip in sheet["skips"].values():
        skip["by"] = _actor(names, skip.pop("by_user_id"), skip["by"])

    if event.cancelled_at is not None:
        sheet["cancelled"] = {
            "at": _iso(event.cancelled_at),
            "reason": event.cancel_reason,
        }
        return sheet

    children = events_svc.event_children(db, ctx, event.id)
    if kind in ("full", "run_sheet"):
        booked = (
            db.query(FnMinSpend)
            .filter(
                FnMinSpend.organization_id == ctx.org_id,
                FnMinSpend.event_id == event.id,
            )
            .first()
        )
        ent_kinds = {
            k for k in ("dj", "playlist") if on.get(f"details.entertainment.{k}", True)
        }
        av_kinds = {
            k
            for k in ("in_house_mic", "in_house_tv", "external_av")
            if on.get(f"details.av.{k}", True)
        }
        sheet["details"] = {
            "min_spend_cents": (booked.amount_cents if booked else None)
            if on["details.min_spend"]
            else None,
            "payment_method": event.payment_method if on["details.payment"] else None,
            "deposit": {"cents": event.deposit_cents, "status": event.deposit_status}
            if on["details.deposit"] and event.deposit_cents is not None
            else None,
            "security": [
                {
                    "name": s.name,
                    "start_time": _t(s.start_time),
                    "end_time": _t(s.end_time),
                    "notes": s.notes,
                }
                for s in children["security"]
            ]
            if on["details.security"]
            else None,
            "entertainment": [
                {
                    "kind": e.kind,
                    "name": e.name,
                    "link": e.link,
                    "start_time": _t(e.start_time),
                    "end_time": _t(e.end_time),
                    "notes": e.notes,
                }
                for e in children["entertainment"]
                if e.kind in ent_kinds
            ]
            if on["details.entertainment"]
            else None,
            "av": [
                {"kind": a.kind, "details": a.details}
                for a in children["av"]
                if a.kind in av_kinds
            ]
            if on["details.av"]
            else None,
            "notes": event.notes if on["details.notes"] else None,
            "special_instructions": event.special_instructions,
            "description": event.description,
            "table_plan": event.table_plan,
        }
        sheet["timings"] = {
            "arrival_time": _t(event.arrival_time)
            if on["timings.arrival_time"]
            else None,
            "seated_time": _t(event.seated_time) if on["timings.seated_time"] else None,
            "preference": event.timing_preference if on["timings.preference"] else None,
            "notes": event.timing_notes if on["timings.notes"] else None,
            "schedule": [
                {
                    "at_time": _t(t.at_time),
                    "ends_next_day": bool(t.ends_next_day),
                    "label": t.label,
                    "activity": t.activity,
                    "is_heading": bool(t.is_heading),
                }
                for t in children["timings"]
            ]
            if on["timings.schedule"]
            else None,
        }
        sheet["beverages"] = {
            "bar_tab_cents": event.bar_tab_cents if on["beverages.bar_tab"] else None,
            "authorised_people": event.bar_tab_authorised_people
            if on["beverages.authorised_people"]
            else None,
            "restrictions": event.bar_tab_restrictions
            if on["beverages.restrictions"]
            else None,
            "arrival_drinks": event.arrival_drinks
            if on["beverages.arrival_drinks"]
            else None,
            "table_ordering": {
                "on": bool(event.table_ordering),
                "limit_cents": event.table_ordering_limit_cents,
            }
            if on["beverages.table_ordering"]
            else None,
            "notes": event.bar_tab_notes if on["beverages.notes"] else None,
        }
    if kind in ("full", "food"):
        menus = []
        counts: dict[tuple, dict] = {}
        if on["food.preorder_menus"]:
            for menu, items in menus_svc.list_menus(db, ctx, event_id=event.id):
                lines = []
                pending_header = None
                for item in items:
                    if item.kind == "header":
                        pending_header = item.name
                        continue
                    if (item.quantity or 0) < 1:
                        continue
                    if pending_header is not None:
                        lines.append({"kind": "header", "name": pending_header})
                        pending_header = None
                    line_total = (
                        item.quantity * item.unit_price_cents
                        if item.unit_price_cents is not None
                        else None
                    )
                    lines.append(
                        {
                            "kind": "item",
                            "name": item.name,
                            "dietaries": list(item.dietaries or []),
                            "quantity": item.quantity,
                            "unit_price_cents": item.unit_price_cents,
                            "line_total_cents": line_total,
                        }
                    )
                    key = (item.name, tuple(item.dietaries or []))
                    agg = counts.setdefault(
                        key,
                        {
                            "name": item.name,
                            "dietaries": list(item.dietaries or []),
                            "quantity": 0,
                        },
                    )
                    agg["quantity"] += item.quantity
                menus.append(
                    {
                        "id": menu.id,
                        "name": menu.name,
                        "is_set_menu": bool(menu.is_set_menu),
                        "guest_count": menu.guest_count,
                        "price_per_person_cents": menu.price_per_person_cents,
                        "lines": lines,
                        "total_cents": money_svc.menu_food_cents(menu, items),
                    }
                )
        sheet["food"] = {
            "menus": menus,
            "kitchen_counts": sorted(counts.values(), key=lambda c: c["name"].lower()),
            "food_cents": money["food_cents"],
            "allergies": event.allergies if on["food.allergies"] else None,
            "timing": {
                "type": event.food_timing_type,
                "label": FOOD_TIMING_LABELS.get(event.food_timing_type or ""),
                "time": _t(event.food_time),
                "end_time": _t(event.food_end_time),
            }
            if on["food.timing_preference"]
            else None,
            "notes": event.food_notes if on["food.notes"] else None,
        }
    if kind == "full":
        sheet["money"] = {
            "min_spend_cents": money["min_spend_cents"],
            "food_cents": money["food_cents"],
            "bar_tab_cents": money["bar_tab_cents"],
            "expected_spend_cents": money["expected_spend_cents"],
        }
    return sheet
