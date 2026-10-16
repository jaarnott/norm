"""The Functions team member's tools — built-ins over Norm Functions.

Each tool is a thin layer over ``app/services/functions``: it builds the
caller's ``FunctionsCtx`` from the conversation's person (``build_ctx(...,
actor_label="Norm")`` — the same organisation, venues and mode the person has
on the Functions pages, with every fact recording that Norm did it for them),
calls the services, and shapes a compact answer for the model: ids, names,
the derived stage / next action / status, money as text plus cents. No rule
of Functions lives here; a refusal is the service's own message.

What each tool may do (the effects ``scripts/sync_approval_labels.py``
declares, which decide whether a person is asked):

* **Reads** — find/get enquiries and events, threads, the venue profile.
* **Drafts** — ``draft_email`` and ``build_proposal`` prepare something staff
  finish in Norm: a draft in the Inbox, a proposal on the enquiry. Nothing
  reaches a customer, so they run without a card.
* **Writes** — ``record_enquiry``, ``record_event`` and ``file_thread`` change
  records and ask first (a person may switch them to "always allow").
  ``send_email`` and ``send_proposal`` reach a customer: they always ask, and
  nobody can switch that off (the owner's rule: every customer send and every
  priced email needs approval).

Previews. A write that asks shows its card from live data without writing.
For the record tools the card is a **dry run**: the real service call inside a
SAVEPOINT that is rolled back, so the card shows exactly what the write would
do — the services' own validation, their own before → after diffs — and
nothing is kept. That only works for services that flush; the mailbox
services commit (they never hold a connection across Gmail), so the send and
filing cards are built by reading, and their writes are never wrapped in a
savepoint (a commit inside one ends it, and the service's next statement then
fails — SQLAlchemy 2.0).

A send's card pins an idempotency key into the approved call's parameters
(``run_params``): the outbox turns a retried or double-approved send into one
email. The card's fingerprint covers the draft's version, so a draft edited in
the Inbox after the card was shown is not sent.

No autonomous runs (owner, 12 Oct 2026): Functions acts when someone asks in
chat. A scheduled task's run may read, but never drafts, changes or sends.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import html
import json
import re
import uuid
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.agents.internal_tools import _opening, previews, register
from app.db.functions_models import (
    BOOKING_TYPES,
    ENQUIRY_SOURCES,
    EVENT_SECTIONS,
    FOLDERS,
    REPLY_TEMPLATE_STAGES,
    FnContact,
    FnContactEmail,
    FnEvent,
    FnMailboxAccount,
    FnMailboxMessage,
    FnMailboxThread,
    FnOutbox,
    FnThreadFolder,
    FnThreadLink,
    FnVenueArea,
)
from app.db.models import Venue
from app.services.entitlements import org_id_for_user
from app.services.functions import contacts as contacts_svc
from app.services.functions import enquiries as enquiries_svc
from app.services.functions import entitlement
from app.services.functions import events as events_svc
from app.services.functions import menus as menus_svc
from app.services.functions import min_spends as min_spends_svc
from app.services.functions import money as money_svc
from app.services.functions import proposals as proposals_svc
from app.services.functions import reports
from app.services.functions import status as status_svc
from app.services.functions import templates as templates_svc
from app.services.functions import venue_profile
from app.services.functions.company import format_money, get_company_settings
from app.services.functions.ctx import FunctionsCtx, build_ctx, require_live
from app.services.functions.errors import FunctionsError
from app.services.functions.events import BOOKING_LABELS, CLIENT_FIELDS
from app.services.functions.mailbox import accounts as accounts_svc
from app.services.functions.mailbox import drafts as drafts_svc
from app.services.functions.mailbox import inbox as inbox_svc
from app.services.functions.mailbox import pricing
from app.services.functions.mailbox.display import html_to_text
from app.services.functions.mailbox.gmail_client import (
    GmailError,
    GmailTransient,
    MailboxAuthError,
)
from app.services.functions.timeutil import to_instant

CONNECTOR = "norm_functions"

OFF = "Norm Functions isn't switched on for this organisation."
UNATTENDED = (
    "Functions only acts when someone asks in chat — a scheduled task can read "
    "enquiries, events and mail, but not draft, change or send anything."
)
APPROVED_READ_ONLY = (
    "{label} is approved, so it is read-only to Norm (approvals belong to the "
    "venue's approvers). Add a new option instead, and say why."
)

#: Rows a list returns unless asked for more, and the most it will return —
#: lists stay well inside the tool-result budget (tool_loop
#: MAX_TOOL_RESULT_CHARS); get_* gives the full picture of one record.
DEFAULT_ROWS = 25
MAX_ROWS = 100
#: read_thread's budget for message text, all messages together.
THREAD_TEXT_BUDGET = 24_000


class Refused(Exception):
    """A refusal the model reads: bad input, or nothing it may do."""


# ===========================================================================
# Plumbing
# ===========================================================================


@contextmanager
def _config_db():
    """The config DB, for the entitlement check (tests point this at their
    own session)."""
    from app.db.engine import _ConfigSessionLocal

    cdb = _ConfigSessionLocal()
    try:
        yield cdb
    finally:
        cdb.close()


def _ctx(db: Session, thread_id: str | None) -> FunctionsCtx:
    """The person acting, as a Functions caller — refused, like the pages'
    404, when Functions isn't on for their organisation.

    In chat that is the conversation's person. Over MCP there is no thread:
    the person is the token's user, carried on the caller scope by the MCP
    entry point (never a param), in the organisation the token names, and
    only at the venues they consented to share."""
    from app.agents.internal_tools import _user_for_thread
    from app.db.models import User
    from app.services import caller_scope

    user = _user_for_thread(thread_id, db)
    org_id = consented = None
    if user is None:
        scope = caller_scope.current()
        if scope is not None and scope.user_id:
            user = db.query(User).filter(User.id == scope.user_id).first()
            org_id, consented = scope.org_id, scope.venue_ids
    if user is None:
        raise Refused(
            "No person is attached to this conversation, so Norm can't open "
            "their Functions."
        )
    org_id = org_id or org_id_for_user(user.id, db)
    with _config_db() as cdb:
        if not entitlement.functions_enabled(db, cdb, org_id):
            raise Refused(OFF)
    ctx = build_ctx(db, user, org_id=org_id, actor_label="Norm")
    if consented is not None:
        venues = ctx.venue_ids & consented
        whole_org = {
            r[0] for r in db.query(Venue.id).filter(Venue.organization_id == ctx.org_id)
        }
        # Owner powers (every contact, all-venue templates, approval
        # governance) span the whole organisation, so they hold only when
        # the token shares all of it.
        ctx = dataclasses.replace(
            ctx, venue_ids=venues, is_owner=ctx.is_owner and venues >= whole_org
        )
    return ctx


def _unattended(db: Session, thread_id: str | None) -> bool:
    """A scheduled task's run (its thread is ``<member>.automated_task``),
    as opposed to a person in chat or in a task's own conversation."""
    from app.db.models import Thread

    if not thread_id:
        return False
    intent = db.query(Thread.intent).filter(Thread.id == thread_id).scalar()
    return bool(intent) and intent.endswith(".automated_task")


def _gmail_message(exc: GmailError) -> str:
    if isinstance(exc, MailboxAuthError):
        return "The venue mailbox needs reconnecting in the Functions Inbox."
    if isinstance(exc, GmailTransient):
        return "Gmail is busy or unreachable — try again in a moment."
    return f"Gmail said: {exc.message}"


def _tool(action: str, *, acts: bool = False):
    """Register a Functions tool. Domain refusals become the error the model
    reads; ``acts`` (drafts and writes) are refused in scheduled runs."""

    def decorator(fn):
        def handler(params: dict, db: Session, thread_id: str | None) -> dict:
            try:
                if acts and _unattended(db, thread_id):
                    raise Refused(UNATTENDED)
                ctx = _ctx(db, thread_id)
                data = fn(dict(params or {}), db, ctx)
            except FunctionsError as exc:
                return {"success": False, "data": {}, "error": exc.message}
            except Refused as exc:
                return {"success": False, "data": {}, "error": str(exc)}
            except GmailError as exc:
                return {"success": False, "data": {}, "error": _gmail_message(exc)}
            return {"success": True, "data": data, "error": None}

        handler.__name__ = fn.__name__
        handler.__doc__ = fn.__doc__
        register(CONNECTOR, action)(handler)
        return handler

    return decorator


def _preview(action: str):
    """Register a write's previewer: ``{"preview", "basis"?, "run_params"?}``,
    ``{"done": …}`` or ``{"error": why it can't run}`` — never a write."""

    def decorator(fn):
        def previewer(params: dict, db: Session, thread_id: str | None) -> dict:
            try:
                if _unattended(db, thread_id):
                    raise Refused(UNATTENDED)
                ctx = _ctx(db, thread_id)
                return fn(dict(params or {}), db, ctx)
            except FunctionsError as exc:
                return {"error": exc.message}
            except Refused as exc:
                return {"error": str(exc)}
            except GmailError as exc:
                return {"error": _gmail_message(exc)}

        previewer.__name__ = fn.__name__
        previews(CONNECTOR, action)(previewer)
        return previewer

    return decorator


@contextmanager
def _dry_run(db: Session):
    """Run real service writes and roll them back (see the module
    docstring). Only around services that flush — never the mailbox's."""
    savepoint = db.begin_nested()
    try:
        yield
    finally:
        savepoint.rollback()


@contextmanager
def _writing(db: Session):
    """A savepoint around a write made of flush-only service calls: a refusal
    half-way leaves nothing behind, and the conversation's session stays
    usable (the tool loop commits)."""
    with db.begin_nested():
        yield


def _compact(d: dict) -> dict:
    """Drop empty values — every token in a tool result is paid for."""
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


# ---------------------------------------------------------------- params --


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool(value, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _int(value, what: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(Decimal(str(value).strip().replace(",", "")))
    except (InvalidOperation, ValueError):
        raise Refused(f"{what} must be a whole number.")


def _list(value) -> list:
    """A list as the model may send it: a list, JSON text of one, or
    comma-separated text (a string param silently ignored is how list
    filters broke before — memory: tool-row-field-schema)."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [v for v in value if v not in (None, "")]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                return [v for v in parsed if v not in (None, "")]
        return [p.strip() for p in text.split(",") if p.strip()]
    return [value]


def _objects(value, what: str) -> list[dict]:
    items = _list(value) if not isinstance(value, dict) else [value]
    out = []
    for item in items:
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except ValueError:
                raise Refused(f"Each of {what} must be an object.")
        if not isinstance(item, dict):
            raise Refused(f"Each of {what} must be an object.")
        out.append(item)
    return out


def _choices(values: list, allowed, what: str) -> list[str]:
    bad = [v for v in values if v not in allowed]
    if bad:
        raise Refused(
            f"Unknown {what}: {', '.join(map(str, bad))}. Use: {', '.join(allowed)}."
        )
    return [str(v) for v in values]


def _date(value, what: str) -> dt.date | None:
    if value is None or value == "":
        return None
    try:
        return dt.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise Refused(f"{what} must be a date as YYYY-MM-DD.")


def _time(value, what: str) -> dt.time | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    try:
        return dt.time.fromisoformat(text if len(text) > 4 else text.zfill(5))
    except ValueError:
        raise Refused(f"{what} must be a 24-hour time as HH:MM.")


def _cents(value, what: str) -> int | None:
    """Dollars as the model writes them ("5500", 5500, "$5,500.50") ->
    integer cents (tools take dollars; Functions stores cents)."""
    if value is None or value == "":
        return None
    text = str(value).strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise Refused(f"{what} must be an amount in dollars, e.g. 5500.")
    return int((amount * 100).quantize(Decimal("1")))


def _required(params: dict, key: str, what: str) -> str:
    value = _text(params.get(key))
    if not value:
        raise Refused(f"{what} is required.")
    return value


# ------------------------------------------------------------- venues ----


def _venues(db: Session, ctx: FunctionsCtx) -> list[Venue]:
    return (
        db.query(Venue)
        .filter(
            Venue.organization_id == ctx.org_id, Venue.id.in_(sorted(ctx.venue_ids))
        )
        .order_by(Venue.name)
        .all()
    )


def _venue(db: Session, ctx: FunctionsCtx, value) -> Venue:
    """A venue the caller may use, by id or by its whole name (case,
    punctuation and a leading "The" aside — caller_scope's rule)."""
    from app.services.caller_scope import venue_key

    text = _text(value)
    venues = _venues(db, ctx)
    if not text:
        raise Refused("Say which venue.")
    for v in venues:
        if v.id == text:
            return v
    hits = [v for v in venues if (v.name or "").lower() == text.lower()] or [
        v for v in venues if venue_key(v.name) == venue_key(text)
    ]
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(v.name for v in venues) or "none"
    if hits:
        raise Refused(
            f"'{text}' names more than one venue — use its id. Yours: {names}."
        )
    raise Refused(f"No venue called '{text}' among yours: {names}.")


def _area_id(db: Session, ctx: FunctionsCtx, venue_id: str, value) -> str | None:
    """An area at the venue, by id or name."""
    text = _text(value)
    if not text:
        return None
    areas = venue_profile.list_areas(db, ctx, venue_id)
    for a in areas:
        if a.id == text:
            return a.id
    hits = [a for a in areas if (a.name or "").lower() == text.lower()]
    active = [a for a in hits if a.active]
    if len(active or hits) == 1:
        return (active or hits)[0].id
    names = ", ".join(a.name for a in areas if a.active) or "none set up"
    raise Refused(f"No area called '{text}' at that venue. Its areas: {names}.")


# ------------------------------------------------------------- shapes ----


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _day(value) -> str | None:
    """A timestamp as its date — lists don't need the seconds."""
    return value.date().isoformat() if isinstance(value, dt.datetime) else _iso(value)


def _hm(value: dt.time | None) -> str | None:
    return value.strftime("%H:%M") if value else None


def _window(start, end, next_day=False) -> str | None:
    if not start:
        return None
    text = _hm(start)
    if end:
        text += f"–{_hm(end)}" + (" (next day)" if next_day else "")
    return text


def _dates(start, end) -> str | None:
    if not start:
        return None
    return start.isoformat() if not end or end == start else f"{start} to {end}"


class _Money:
    """The organisation's currency, once per call."""

    def __init__(self, db: Session, ctx: FunctionsCtx):
        company = get_company_settings(db, ctx.org_id)
        self.currency = company.currency
        self.tax_label = company.tax_label

    def __call__(self, cents) -> str | None:
        return format_money(cents, self.currency) if cents is not None else None

    def pair(self, key: str, cents) -> dict:
        return {key: self(cents), f"{key}_cents": cents} if cents is not None else {}

    def note(self) -> dict:
        return {"currency": self.currency, "tax": self.tax_label}


def _enquiry_link(enquiry_id: str) -> str:
    return f"/app?page=functions-enquiries&enquiry={enquiry_id}"


def _event_link(event_id: str) -> str:
    return f"/app?page=functions-events&event={event_id}"


def _next_action(kind, label, due, overdue, due_today, why=None) -> dict | str:
    if kind == "none" and not why:
        return "none"
    return _compact(
        {
            "kind": kind,
            "label": label,
            "due": _iso(due),
            "overdue": overdue or None,
            "due_today": due_today or None,
            "why": why,
        }
    )


def _enquiry_row(r, value_cents, money: _Money) -> dict:
    e = r.enquiry
    return _compact(
        {
            "id": e.id,
            "subject": e.subject,
            "contact": r.contact_name
            + (f" ({r.contact_company})" if r.contact_company else ""),
            "venue": r.venue_name or "Group-wide",
            "booking_type": e.booking_type,
            "guests": e.guests,
            "event_date": _dates(e.preferred_date_start, e.preferred_date_end),
            "stage": r.stage,
            "next_action": _next_action(
                r.next_action,
                status_svc.NEXT_ACTION_LABELS.get(r.next_action),
                r.next_action_due,
                r.next_action_overdue,
                r.next_action_due_today,
            ),
            **money.pair("value", value_cents or None),
            "last_from_customer": _day(r.last_inbound_at),
            "last_from_us": _day(r.last_outbound_at),
            "quoted": _day(r.quote_sent_at),
            "follow_up": _iso(e.follow_up_date),
            "received": _day(e.created_at),
            "archived": True if e.archived_at else None,
        }
    )


def _event_row(r, money: _Money) -> dict:
    e = r.event
    return _compact(
        {
            "id": e.id,
            "name": e.name,
            "date": _iso(e.event_date),
            "time": _window(e.start_time, e.end_time, e.ends_next_day),
            "venue": r.venue_name,
            "area": r.area_name,
            "booking_type": e.booking_type,
            "guests": e.guests,
            "contact": r.contact_name,
            "status": r.status,
            "edited_since_confirmed": r.edited_since_confirmed or None,
            **money.pair("minimum_spend", r.min_spend_cents),
            **money.pair("food", r.food_cents or None),
            **money.pair("expected_spend", r.expected_spend_cents),
            "enquiry_id": e.enquiry_id,
        }
    )


def _names(db: Session, ids) -> dict[str, str]:
    return reports.user_names(db, ids)


def _approval_note(state, area, settings, names) -> str:
    """Who the option is waiting on, in words."""
    if state == "confirmed":
        return "booked — the customer confirmed it"
    if state == "withdrawn":
        return "withdrawn"
    if state == "approved":
        return "approved — the customer can confirm it"
    if area is not None and area.requires_approval is False:
        return "no approval needed in this area — the customer can confirm it"
    approver = settings.approver_user_id if settings else None
    who = names.get(approver) if approver else None
    prefix = "declined by the customer; " if state == "declined" else ""
    return prefix + (
        f"needs approval from {who} before the customer can confirm it"
        if who
        else "needs the venue's approver (none is set) before the customer can confirm it"
    )


def _options(db: Session, ctx: FunctionsCtx, enquiry_id: str, money: _Money) -> list:
    rows = min_spends_svc.list_min_spends(db, ctx, enquiry_id=enquiry_id)
    if not rows:
        return []
    areas = {
        a.id: a
        for a in db.query(FnVenueArea).filter(
            FnVenueArea.organization_id == ctx.org_id,
            FnVenueArea.id.in_({o.area_id for o, _, _ in rows if o.area_id} or {""}),
        )
    }
    venue_ids = {o.venue_id for o, _, _ in rows}
    venues = {v.id: v.name for v in db.query(Venue).filter(Venue.id.in_(venue_ids))}
    settings = venue_profile.settings_for_venues(db, ctx, venue_ids)
    names = _names(db, [s.approver_user_id for s in settings.values()])
    food = {
        v: money_svc.enquiry_food_cents(db, ctx.org_id, enquiry_id, venue_id=v)
        for v in venue_ids
    }
    out = []
    for o, state, confirmable in rows:
        area = areas.get(o.area_id)
        total = money_svc.option_total_cents(o.amount_cents, food.get(o.venue_id, 0))
        out.append(
            _compact(
                {
                    "id": o.id,
                    "label": o.label,
                    "venue": venues.get(o.venue_id),
                    "area": area.name if area else None,
                    "date": _iso(o.event_date),
                    "time": _window(o.start_time, o.end_time, o.ends_next_day),
                    **money.pair("minimum_spend", o.amount_cents),
                    **money.pair("food", food.get(o.venue_id) or None),
                    **money.pair("total", total),
                    "state": state,
                    "confirmable": confirmable,
                    "approval": _approval_note(
                        state, area, settings.get(o.venue_id), names
                    ),
                    "editable_by_norm": state in ("proposed", "declined"),
                    "event_id": o.event_id,
                    "notes": o.notes,
                }
            )
        )
    return out


def _menu(menu, items, money: _Money, *, with_items: bool) -> dict:
    out = {
        "id": menu.id,
        "name": menu.name,
        "set_menu": menu.is_set_menu or None,
        **money.pair("per_person", menu.price_per_person_cents),
        "guests": menu.guest_count or None,
        **money.pair("value", money_svc.menu_food_cents(menu, items)),
        "dishes": sum(1 for i in items if i.kind != "header"),
    }
    if with_items:
        out["items"] = [
            _compact(
                {
                    "id": i.id,
                    "section": i.name if i.kind == "header" else None,
                    "dish": i.name if i.kind != "header" else None,
                    "qty": i.quantity if i.kind != "header" else None,
                    "price": money(i.unit_price_cents),
                    "dietaries": list(i.dietaries or []) or None,
                }
            )
            for i in items
        ]
    return _compact(out)


def _thread_summaries(db: Session, ctx: FunctionsCtx, links) -> list[dict]:
    """Linked threads with their mirror rollups; mailboxes at venues the
    caller can't see are left out."""
    if not links:
        return []
    accounts = {
        a.id: a
        for a in accounts_svc.visible_accounts(db, ctx).filter(
            FnMailboxAccount.id.in_({lk.account_id for lk in links})
        )
    }
    threads = {
        (t.account_id, t.gmail_thread_id): t
        for t in db.query(FnMailboxThread).filter(
            FnMailboxThread.organization_id == ctx.org_id,
            FnMailboxThread.account_id.in_(list(accounts) or [""]),
            FnMailboxThread.gmail_thread_id.in_({lk.gmail_thread_id for lk in links}),
        )
    }
    out = []
    for lk in links:
        account = accounts.get(lk.account_id)
        if account is None:
            continue
        t = threads.get((lk.account_id, lk.gmail_thread_id))
        out.append(
            _compact(
                {
                    "link_id": lk.id,
                    "account_id": lk.account_id,
                    "mailbox": account.google_email,
                    "gmail_thread_id": lk.gmail_thread_id,
                    "subject": t.subject if t else None,
                    "last_message_at": _iso(t.last_message_at) if t else None,
                    "last_from": (t.last_from_name or t.last_from_email) if t else None,
                    "messages": t.message_count if t else None,
                }
            )
        )
    return out


def _draft_row(d) -> dict:
    return _compact(
        {
            "id": d.id,
            "kind": d.kind,
            "subject": d.subject,
            "to": list(d.to_emails or []),
            "status": d.status,
            "in_gmail": d.owner == "gmail" or None,
            "account_id": d.account_id,
            "gmail_thread_id": d.gmail_thread_id,
            "updated_at": _iso(d.updated_at),
        }
    )


def _card(title, target, *, venue=None, changes=(), warnings=(), note=None) -> dict:
    return {
        "title": title,
        "target": target,
        "venue": venue,
        "changes": list(changes),
        "warnings": [w for w in warnings if w],
        "note": note,
    }


def _shown(value):
    """A value as a card cell (services' diffs carry ISO strings already)."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return value


def _shown_value(value):
    """A parsed date or time back as the text a card (and its basis) holds."""
    return value.isoformat() if isinstance(value, (dt.date, dt.time)) else value


_FIELD_LABELS = {
    "venue_id": "Venue",
    "area_id": "Area",
    "booking_type": "Booking type",
    "preferred_date_start": "Event date (from)",
    "preferred_date_end": "Event date (to)",
    "preferred_start_time": "Start time",
    "preferred_end_time": "End time",
    "preferred_ends_next_day": "Ends next day",
    "follow_up_date": "Follow-up date",
    "follow_up_reason": "Follow-up reason",
    "client_note": "Note for the client",
    "notes": "Internal notes",
    "deposit_cents": "Deposit",
    "bar_tab_cents": "Bar tab",
    "amount_cents": "Minimum spend",
    "event_date": "Event date",
}


def _label(field: str) -> str:
    return _FIELD_LABELS.get(field) or field.replace("_", " ").capitalize()


def _diff_rows(db: Session, ctx: FunctionsCtx, changes: dict, money: _Money) -> list:
    """A service's ``{field: [old, new]}`` as card rows — ids as names,
    cents as money."""
    venue_ids = {v for f, pair in changes.items() if f == "venue_id" for v in pair if v}
    area_ids = {v for f, pair in changes.items() if f == "area_id" for v in pair if v}
    venues = (
        {v.id: v.name for v in db.query(Venue).filter(Venue.id.in_(venue_ids))}
        if venue_ids
        else {}
    )
    areas = (
        {
            a.id: a.name
            for a in db.query(FnVenueArea).filter(
                FnVenueArea.organization_id == ctx.org_id, FnVenueArea.id.in_(area_ids)
            )
        }
        if area_ids
        else {}
    )

    def show(field, value):
        if value is None:
            return "—"
        if field == "venue_id":
            return venues.get(value, value)
        if field == "area_id":
            return areas.get(value, value)
        if field.endswith("_cents"):
            return money(value)
        if field == "booking_type":
            return BOOKING_LABELS.get(value, value)
        return _shown(value)

    return [
        {"field": _label(f), "before": show(f, old), "after": show(f, new)}
        for f, (old, new) in changes.items()
    ]


# ===========================================================================
# Enquiries
# ===========================================================================

ENQUIRY_SORTS = (
    "created_desc",
    "created_asc",
    "due",
    "preferred_date",
    "last_activity",
)


@_tool("find_enquiries")
def find_enquiries(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """Function enquiries with their derived stage and next action."""
    sort = _text(p.get("sort")) or "created_desc"
    if sort not in ENQUIRY_SORTS:
        raise Refused(f"sort must be one of {', '.join(ENQUIRY_SORTS)}.")
    limit = max(1, min(_int(p.get("limit"), "limit") or DEFAULT_ROWS, MAX_ROWS))
    offset = max(0, _int(p.get("offset"), "offset") or 0)
    tz = reports.org_tz(db, ctx)
    received_from = _date(p.get("received_from"), "received_from")
    received_to = _date(p.get("received_to"), "received_to")
    venue_ids = [_venue(db, ctx, p["venue"]).id] if _text(p.get("venue")) else None
    rows = enquiries_svc.list_enquiries(
        db,
        ctx,
        venue_ids=venue_ids,
        stages=_choices(_list(p.get("stage")), status_svc.STAGES, "stage") or None,
        next_actions=_choices(
            _list(p.get("next_action")), status_svc.NEXT_ACTION_KINDS, "next_action"
        )
        or None,
        due_only=_bool(p.get("due_only")),
        sources=_choices(_list(p.get("source")), ENQUIRY_SOURCES, "source") or None,
        booking_types=_choices(
            _list(p.get("booking_type")), BOOKING_TYPES, "booking_type"
        )
        or None,
        created_from=to_instant(tz, received_from) if received_from else None,
        created_to=to_instant(tz, received_to + dt.timedelta(days=1))
        if received_to
        else None,
        preferred_from=_date(p.get("date_from"), "date_from"),
        preferred_to=_date(p.get("date_to"), "date_to"),
        q=_text(p.get("q")),
        include_archived=_bool(p.get("include_archived")),
        sort=sort,
        limit=limit + 1,
        offset=offset,
    )
    more = len(rows) > limit
    rows = rows[:limit]
    money = _Money(db, ctx)
    values = reports.enquiry_values(db, ctx, [r.enquiry.id for r in rows])
    return {
        "enquiries": [_enquiry_row(r, values.get(r.enquiry.id), money) for r in rows],
        "count": len(rows),
        "offset": offset,
        "has_more": more,
        "money": money.note(),
    }


def _enquiry_id(db: Session, ctx: FunctionsCtx, p: dict) -> str:
    """The enquiry asked for: by id, by a thread linked to it, or the
    contact's most recent (visible) enquiry."""
    if _text(p.get("enquiry_id")):
        return enquiries_svc.get_enquiry(db, ctx, _text(p["enquiry_id"])).id
    thread = _text(p.get("gmail_thread_id"))
    if thread:
        links = (
            db.query(FnThreadLink)
            .filter(
                FnThreadLink.organization_id == ctx.org_id,
                FnThreadLink.gmail_thread_id == thread,
                FnThreadLink.enquiry_id.isnot(None),
                FnThreadLink.unlinked_at.is_(None),
            )
            .order_by(FnThreadLink.linked_at.desc())
            .all()
        )
        for link in links:
            found = enquiries_svc.get_enquiry_or_none(db, ctx, link.enquiry_id)
            if found is not None:
                return found.id
        raise Refused("That thread isn't linked to an enquiry you can see.")
    email = _text(p.get("contact_email"))
    if email:
        contact = contacts_svc.find_contact_by_email(db, ctx, email)
        rows = (
            enquiries_svc.list_enquiries(
                db, ctx, contact_id=contact.id, include_archived=True, limit=1
            )
            if contact is not None
            else []
        )
        if not rows:
            raise Refused(f"No enquiry you can see for {email}.")
        return rows[0].enquiry.id
    raise Refused("Give an enquiry_id, a gmail_thread_id or a contact_email.")


@_tool("get_enquiry")
def get_enquiry(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """Everything about one enquiry."""
    enquiry_id = _enquiry_id(db, ctx, p)
    enquiry = enquiries_svc.get_enquiry(db, ctx, enquiry_id)
    stage, na = enquiries_svc.enquiry_state(db, ctx, enquiry_id)
    money = _Money(db, ctx)
    contact = contacts_svc.get_contact(db, ctx, enquiry.contact_id)
    emails = [e.email for e in contacts_svc.contact_emails(db, ctx, contact.id)]
    venue = (
        db.query(Venue).filter(Venue.id == enquiry.venue_id).first()
        if enquiry.venue_id
        else None
    )
    others = [
        _compact(
            {
                "id": r.enquiry.id,
                "subject": r.enquiry.subject,
                "venue": r.venue_name or "Group-wide",
                "stage": r.stage,
                "received": _day(r.enquiry.created_at),
            }
        )
        for r in enquiries_svc.list_enquiries(
            db, ctx, contact_id=contact.id, include_archived=True, limit=11
        )
        if r.enquiry.id != enquiry_id
    ][:10]
    bookings = events_svc.list_events(
        db,
        ctx,
        contact_id=contact.id,
        include_cancelled=True,
        sort="date_desc",
        limit=10,
    )
    closures = enquiries_svc.closures(db, ctx, enquiry_id)
    food = reports.enquiry_food(db, ctx, enquiry_id)
    value = reports.enquiry_values(db, ctx, [enquiry_id]).get(enquiry_id)
    owner = _names(db, [enquiry.owner_user_id]).get(enquiry.owner_user_id)
    timeline = reports.enquiry_timeline(db, ctx, enquiry_id, mail_limit=20)[:10]
    return _compact(
        {
            "id": enquiry.id,
            "link": _enquiry_link(enquiry.id),
            "subject": enquiry.subject,
            "stage": stage,
            "next_action": na.as_dict() if na else None,
            "venue": {"id": venue.id, "name": venue.name} if venue else "Group-wide",
            "booking_type": enquiry.booking_type,
            "booking_label": BOOKING_LABELS.get(enquiry.booking_type),
            "guests": enquiry.guests,
            "event_date": _dates(
                enquiry.preferred_date_start, enquiry.preferred_date_end
            ),
            "time": _window(
                enquiry.preferred_start_time,
                enquiry.preferred_end_time,
                enquiry.preferred_ends_next_day,
            ),
            "source": enquiry.source,
            "owner": owner,
            "follow_up": _compact(
                {
                    "date": _iso(enquiry.follow_up_date),
                    "reason": enquiry.follow_up_reason,
                }
            ),
            "client_note": enquiry.client_note,
            "notes": enquiry.notes,
            "archived": True if enquiry.archived_at else None,
            "received": _iso(enquiry.created_at),
            "contact": _compact(
                {
                    "id": contact.id,
                    "name": contact.name,
                    "company": contact.company,
                    "phone": contact.phone,
                    "emails": emails,
                }
            ),
            "contact_history": _compact(
                {
                    "enquiries": others,
                    "events": [
                        _compact(
                            {
                                "id": b.event.id,
                                "name": b.event.name,
                                "date": _iso(b.event.event_date),
                                "venue": b.venue_name,
                                "status": b.status,
                            }
                        )
                        for b in bookings
                    ],
                }
            ),
            "options": _options(db, ctx, enquiry_id, money),
            "menus": [
                _menu(m, items, money, with_items=False)
                for m, items in menus_svc.list_menus(db, ctx, enquiry_id=enquiry_id)
            ],
            **money.pair("food", food or None),
            **money.pair("value", value or None),
            "events": [
                _event_row(r, money)
                for r in events_svc.list_events(
                    db, ctx, enquiry_id=enquiry_id, include_cancelled=True
                )
            ],
            "closed": _compact(
                {
                    "kind": closures[-1].kind,
                    "reason": closures[-1].reason,
                    "at": _iso(closures[-1].occurred_at),
                }
            )
            if closures
            else None,
            "proposal": _compact(proposals_svc.summary(db, ctx, enquiry_id)),
            "threads": _thread_summaries(
                db, ctx, enquiries_svc.thread_links(db, ctx, enquiry_id=enquiry_id)
            ),
            "drafts": [
                _draft_row(d)
                for d in drafts_svc.list_drafts(db, ctx, enquiry_id=enquiry_id)
            ],
            "recent_activity": [
                _compact(
                    {
                        "at": i["at"],
                        "what": i["title"],
                        "detail": (i.get("detail") or "")[:160] or None,
                        "by": i.get("actor"),
                    }
                )
                for i in timeline
            ],
            "money": money.note(),
        }
    )


# --------------------------------------------------------- record_enquiry --

ENQUIRY_ACTIONS = ("create", "update", "close", "reopen", "confirm", "decline")
STAFF_CHANNELS = ("email", "phone", "in_person")

#: record_enquiry params -> the service's enquiry fields, with their parsers.
_ENQUIRY_PARAMS = {
    "booking_type": ("booking_type", None),
    "subject": ("subject", None),
    "guests": ("guests", "int"),
    "date_from": ("preferred_date_start", "date"),
    "date_to": ("preferred_date_end", "date"),
    "start_time": ("preferred_start_time", "time"),
    "end_time": ("preferred_end_time", "time"),
    "ends_next_day": ("preferred_ends_next_day", "bool"),
    "follow_up_date": ("follow_up_date", "date"),
    "follow_up_reason": ("follow_up_reason", None),
    "client_note": ("client_note", None),
    "notes": ("notes", None),
}


def _enquiry_fields(db: Session, ctx: FunctionsCtx, p: dict) -> dict:
    """The enquiry fields named in ``p`` (present-but-empty clears one)."""
    fields: dict = {}
    for param, (field, kind) in _ENQUIRY_PARAMS.items():
        if param not in p:
            continue
        raw = p[param]
        if kind == "int":
            value = _int(raw, param)
        elif kind == "date":
            value = _date(raw, param)
        elif kind == "time":
            value = _time(raw, param)
        elif kind == "bool":
            value = _bool(raw)
        else:
            value = _text(raw)
        if field == "booking_type" and value is not None and value not in BOOKING_TYPES:
            raise Refused(f"booking_type must be one of {', '.join(BOOKING_TYPES)}.")
        fields[field] = value
    if "venue" in p:
        fields["venue_id"] = (
            _venue(db, ctx, p["venue"]).id if _text(p["venue"]) else None
        )
    return fields


def _thread_to_link(p: dict) -> tuple[str | None, str | None]:
    account, thread = _text(p.get("account_id")), _text(p.get("gmail_thread_id"))
    if bool(account) != bool(thread):
        raise Refused("To attach a thread give both account_id and gmail_thread_id.")
    return account, thread


def _create_enquiry(db: Session, ctx: FunctionsCtx, p: dict):
    fields = _enquiry_fields(db, ctx, p)
    source = _text(p.get("source")) or "agent"
    if source not in ENQUIRY_SOURCES:
        raise Refused(f"source must be one of {', '.join(ENQUIRY_SOURCES)}.")
    email = _text(p.get("contact_email"))
    name = _text(p.get("contact_name"))
    if not (email or name):
        raise Refused(
            "A new enquiry needs the customer: contact_name and contact_email."
        )
    enquiry = enquiries_svc.create_enquiry(
        db,
        ctx,
        contact={
            "name": name or (email or "").split("@")[0],
            "email": email,
            "phone": _text(p.get("contact_phone")),
            "company": _text(p.get("contact_company")),
        },
        source=source,
        **fields,
    )
    account, thread = _thread_to_link(p)
    if thread:
        enquiries_svc.link_thread(
            db, ctx, account_id=account, gmail_thread_id=thread, enquiry_id=enquiry.id
        )
    return enquiry, fields


def _confirm(db: Session, ctx: FunctionsCtx, p: dict, enquiry):
    """The customer's yes, recorded by staff: confirming an option (or a
    booking without a minimum spend) creates the event."""
    channel = _text(p.get("channel")) or ""
    if channel not in STAFF_CHANNELS:
        raise Refused(
            f"channel must be one of {', '.join(STAFF_CHANNELS)} — how the customer said yes."
        )
    option_id = _text(p.get("option_id"))
    if option_id:
        option = min_spends_svc.get_min_spend(db, ctx, option_id)
        if option.enquiry_id != enquiry.id:
            raise Refused("That option belongs to another enquiry.")
        return min_spends_svc.confirm_option(
            db,
            ctx,
            option_id,
            channel=channel,
            customer_email=_text(p.get("customer_email")),
            note=_text(p.get("note")),
            name=_text(p.get("event_name")),
        )
    area_id = None
    if _text(p.get("area")):
        if not enquiry.venue_id:
            raise Refused("A group-wide enquiry has no venue to find that area at.")
        area_id = _area_id(db, ctx, enquiry.venue_id, p["area"])
    return events_svc.confirm_booking(
        db,
        ctx,
        enquiry.id,
        channel=channel,
        area_id=area_id,
        name=_text(p.get("event_name")),
        note=_text(p.get("note")),
    )


def _record_enquiry(db: Session, ctx: FunctionsCtx, p: dict) -> dict:
    """Do the action; return what the result (and the card) need."""
    action = _text(p.get("action")) or ""
    if action not in ENQUIRY_ACTIONS:
        raise Refused(f"action must be one of {', '.join(ENQUIRY_ACTIONS)}.")
    if action == "create":
        enquiry, fields = _create_enquiry(db, ctx, p)
        return {"action": action, "enquiry": enquiry, "fields": fields}
    enquiry = enquiries_svc.get_enquiry(
        db, ctx, _required(p, "enquiry_id", "enquiry_id")
    )
    if action == "update":
        fields = _enquiry_fields(db, ctx, p)
        account, thread = _thread_to_link(p)
        if not fields and not thread:
            raise Refused("Nothing to change — name the fields to update.")
        changes = enquiries_svc.update_enquiry(db, ctx, enquiry.id, **fields)
        if thread:
            enquiries_svc.link_thread(
                db,
                ctx,
                account_id=account,
                gmail_thread_id=thread,
                enquiry_id=enquiry.id,
            )
        return {
            "action": action,
            "enquiry": enquiry,
            "changes": changes,
            "thread": thread,
        }
    if action in ("close", "reopen"):
        stage, _ = enquiries_svc.enquiry_state(db, ctx, enquiry.id)
        reason = _required(p, "reason", "A reason")
        if action == "close":
            enquiries_svc.close_enquiry(db, ctx, enquiry.id, reason=reason)
        else:
            enquiries_svc.reopen_enquiry(db, ctx, enquiry.id, reason=reason)
        after, _ = enquiries_svc.enquiry_state(db, ctx, enquiry.id)
        return {
            "action": action,
            "enquiry": enquiry,
            "before": stage,
            "after": after,
            "reason": reason,
        }
    if action == "confirm":
        event, created = _confirm(db, ctx, p, enquiry)
        return {
            "action": action,
            "enquiry": enquiry,
            "event": event,
            "created": created,
        }
    option_id = _required(
        p, "option_id", "option_id (the option the customer declined)"
    )
    option = min_spends_svc.get_min_spend(db, ctx, option_id)
    if option.enquiry_id != enquiry.id:
        raise Refused("That option belongs to another enquiry.")
    channel = _text(p.get("channel")) or ""
    if channel not in STAFF_CHANNELS:
        raise Refused(f"channel must be one of {', '.join(STAFF_CHANNELS)}.")
    min_spends_svc.decline_option(
        db,
        ctx,
        option_id,
        channel=channel,
        customer_email=_text(p.get("customer_email")),
        note=_text(p.get("note")),
    )
    return {"action": action, "enquiry": enquiry, "option": option}


@_preview("record_enquiry")
def _record_enquiry_preview(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """The card: a dry run of the real write, rolled back."""
    money = _Money(db, ctx)
    action = _text(p.get("action")) or ""
    existing_contact = None
    open_siblings: list[str] = []
    if action == "create" and _text(p.get("contact_email")):
        found = contacts_svc.find_contact_by_email(db, ctx, p["contact_email"])
        existing_contact = (found.id, found.name) if found is not None else None
    if action == "confirm" and _text(p.get("option_id")):
        open_siblings = [
            (o.label or "an option")
            for o, state, _ in min_spends_svc.list_min_spends(
                db, ctx, enquiry_id=_required(p, "enquiry_id", "enquiry_id")
            )
            if o.id != p["option_id"] and state in ("proposed", "approved", "declined")
        ]
    with _dry_run(db):
        out = _record_enquiry(db, ctx, p)
        enquiry = out["enquiry"]
        target = enquiry.subject or "the enquiry"
        venue = (
            db.query(Venue.name).filter(Venue.id == enquiry.venue_id).scalar()
            if enquiry.venue_id
            else None
        )
        if action == "create":
            fields = out["fields"]
            contact = (
                f"{existing_contact[1]} (existing contact)"
                if existing_contact
                else f"{_text(p.get('contact_name')) or ''} <{_text(p.get('contact_email')) or 'no email'}> (new contact)"
            )
            rows = [{"field": "Customer", "after": contact}] + [
                {"field": r["field"], "after": r["after"]}
                for r in _diff_rows(
                    db,
                    ctx,
                    {
                        k: [None, _shown_value(v)]
                        for k, v in fields.items()
                        if v is not None
                    },
                    money,
                )
            ]
            if _text(p.get("gmail_thread_id")):
                rows.append({"field": "Thread", "after": "linked to the new enquiry"})
            card = _card("Create an enquiry", target, venue=venue, changes=rows)
            basis = {
                "action": action,
                "fields": {k: _shown_value(v) for k, v in fields.items()},
                "contact": existing_contact[0]
                if existing_contact
                else _text(p.get("contact_email")),
                "thread": _text(p.get("gmail_thread_id")),
            }
            return {"preview": card, "basis": basis}
        if action == "update":
            changes = out["changes"]
            if not changes and not out["thread"]:
                return {"done": {"enquiry_id": enquiry.id, "result": "no change"}}
            rows = _diff_rows(db, ctx, changes, money)
            if out["thread"]:
                rows.append({"field": "Thread", "after": "linked to this enquiry"})
            card = _card("Change an enquiry", target, venue=venue, changes=rows)
            return {
                "preview": card,
                "basis": {"changes": changes, "thread": out["thread"]},
            }
        if action in ("close", "reopen"):
            rows = [
                {"field": "Stage", "before": out["before"], "after": out["after"]},
                {"field": "Reason", "after": out["reason"]},
            ]
            warnings = [enquiries_svc.REOPEN_RULE] if action == "reopen" else []
            card = _card(
                "Close an enquiry as lost"
                if action == "close"
                else "Reopen a lost enquiry",
                target,
                venue=venue,
                changes=rows,
                warnings=warnings,
            )
            return {
                "preview": card,
                "basis": {k: out[k] for k in ("before", "after", "reason")},
            }
        if action == "confirm":
            event, created = out["event"], out["created"]
            if not created:
                return {
                    "done": {
                        "enquiry_id": enquiry.id,
                        "event_id": event.id,
                        "result": "already booked — the event exists",
                    }
                }
            area = (
                db.query(FnVenueArea.name)
                .filter(FnVenueArea.id == event.area_id)
                .scalar()
                if event.area_id
                else None
            )
            booked = (
                min_spends_svc.booked_option(db, ctx, event.id)
                if _text(p.get("option_id"))
                else None
            )
            rows = [
                {"field": "Customer said yes by", "after": p.get("channel")},
                {"field": "Event", "after": event.name},
                {"field": "Date", "after": _iso(event.event_date)},
                {
                    "field": "Time",
                    "after": _window(
                        event.start_time, event.end_time, event.ends_next_day
                    ),
                },
                {"field": "Area", "after": area},
                {"field": "Guests", "after": event.guests},
            ]
            if booked is not None:
                rows.append(
                    {"field": "Minimum spend", "after": money(booked.amount_cents)}
                )
            warnings = []
            if open_siblings:
                warnings.append(
                    "The other options are withdrawn: " + ", ".join(open_siblings) + "."
                )
            warnings.append("This books it in Norm — the customer is not emailed.")
            card = _card(
                "Book it — the customer confirmed",
                target,
                venue=venue,
                changes=rows,
                warnings=warnings,
            )
            return {
                "preview": card,
                "basis": {
                    "option": _text(p.get("option_id")),
                    "date": _iso(event.event_date),
                    "minimum": booked.amount_cents if booked is not None else None,
                    "channel": p.get("channel"),
                },
            }
        option = out["option"]
        card = _card(
            "Record that the customer declined an option",
            target,
            venue=venue,
            changes=[
                {
                    "field": "Option",
                    "after": f"{option.label or 'Option'} — {money(option.amount_cents)}",
                },
                {"field": "Declined by", "after": p.get("channel")},
            ],
            note=_text(p.get("note")),
        )
        return {
            "preview": card,
            "basis": {"option": option.id, "channel": p.get("channel")},
        }


@_tool("record_enquiry", acts=True)
def record_enquiry(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """Create, change, close, reopen, or record the customer's answer."""
    with _writing(db):
        out = _record_enquiry(db, ctx, p)
    enquiry = out["enquiry"]
    stage, na = enquiries_svc.enquiry_state(db, ctx, enquiry.id)
    result = {
        "enquiry_id": enquiry.id,
        "link": _enquiry_link(enquiry.id),
        "stage": stage,
        "next_action": na.as_dict() if na else None,
    }
    if out["action"] == "update":
        result["changed"] = sorted(out["changes"])
    if out["action"] == "confirm":
        result.update(
            {
                "event_id": out["event"].id,
                "event_link": _event_link(out["event"].id),
                "created": out["created"],
                "next": "The event is booked. Send the client their portal link from the "
                "event page when you're ready.",
            }
        )
    return _compact(result)


# ===========================================================================
# Events
# ===========================================================================

EVENT_SORTS = ("date_asc", "date_desc", "created_desc")


@_tool("find_events")
def find_events(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """Booked events with their derived status and expected spend."""
    sort = _text(p.get("sort")) or "date_asc"
    if sort not in EVENT_SORTS:
        raise Refused(f"sort must be one of {', '.join(EVENT_SORTS)}.")
    limit = max(1, min(_int(p.get("limit"), "limit") or DEFAULT_ROWS, MAX_ROWS))
    offset = max(0, _int(p.get("offset"), "offset") or 0)
    rows = events_svc.list_events(
        db,
        ctx,
        venue_ids=[_venue(db, ctx, p["venue"]).id] if _text(p.get("venue")) else None,
        date_from=_date(p.get("date_from"), "date_from"),
        date_to=_date(p.get("date_to"), "date_to"),
        statuses=_choices(_list(p.get("status")), status_svc.EVENT_STATUSES, "status")
        or None,
        include_cancelled=_bool(p.get("include_cancelled")),
        booking_types=_choices(
            _list(p.get("booking_type")), BOOKING_TYPES, "booking_type"
        )
        or None,
        enquiry_id=_text(p.get("enquiry_id")),
        q=_text(p.get("q")),
        sort=sort,
        limit=limit + 1,
        offset=offset,
    )
    more = len(rows) > limit
    rows = rows[:limit]
    money = _Money(db, ctx)
    live = [r for r in rows if r.status != "cancelled"]
    return {
        "events": [_event_row(r, money) for r in rows],
        "count": len(rows),
        "offset": offset,
        "has_more": more,
        "totals": _compact(
            {
                "events": len(live),
                "guests": sum(r.event.guests or 0 for r in live),
                **money.pair(
                    "expected_spend", sum(r.expected_spend_cents or 0 for r in live)
                ),
            }
        ),
        "money": money.note(),
    }


EVENT_VIEWS = ("details", "full", "run_sheet", "food")
ALLERGIES_HIDDEN = (
    "recorded — ask for them (include_allergies) only if the person needs them"
)


@_tool("get_event")
def get_event(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """One event in full, or one of its printed sheets."""
    event_id = _required(p, "event_id", "event_id")
    view = _text(p.get("view")) or "details"
    if view not in EVENT_VIEWS:
        raise Refused(f"view must be one of {', '.join(EVENT_VIEWS)}.")
    allergies = _bool(p.get("include_allergies"))
    if view != "details":
        sheet = reports.print_sheet(db, ctx, event_id, view)
        food = sheet.get("food")
        if isinstance(food, dict) and food.get("allergies") and not allergies:
            food["allergies"] = ALLERGIES_HIDDEN
        sheet["link"] = _event_link(event_id)
        return sheet
    event = events_svc.get_event(db, ctx, event_id)
    money = _Money(db, ctx)
    state = events_svc.event_state(db, ctx, event_id)
    totals = money_svc.event_expected_spend(db, event)
    contact = contacts_svc.get_contact(db, ctx, event.contact_id)
    emails = [e.email for e in contacts_svc.contact_emails(db, ctx, contact.id)]
    venue = db.query(Venue).filter(Venue.id == event.venue_id).first()
    area = (
        db.query(FnVenueArea).filter(FnVenueArea.id == event.area_id).first()
        if event.area_id
        else None
    )
    enquiry = enquiries_svc.get_enquiry_or_none(db, ctx, event.enquiry_id)
    children = events_svc.event_children(db, ctx, event_id)
    sections = {}
    facts = events_svc.section_confirmations(db, ctx, event_id)
    names = _names(db, [c.actor_user_id for c in facts])
    for c in facts:
        sections[c.section] = _compact(
            {
                "state": c.kind,
                "by": "client"
                if c.actor_kind == "client"
                else names.get(c.actor_user_id) or c.actor_label,
                "at": _day(c.occurred_at),
                "note": c.note,
            }
        )
    changes = events_svc.event_changes(db, ctx, event_id)[-8:]
    change_names = _names(db, [c.actor_user_id for c in changes])
    booked = min_spends_svc.booked_option(db, ctx, event_id)
    return _compact(
        {
            "id": event.id,
            "link": _event_link(event.id),
            "name": event.name,
            "status": state.status,
            "edited_since_confirmed": state.edited_since_confirmed or None,
            "sections": {
                s: sections.get(s, {"state": "waiting"}) for s in EVENT_SECTIONS
            },
            "booking_type": event.booking_type,
            "venue": venue.name if venue else None,
            "area": area.name if area else None,
            "date": _iso(event.event_date),
            "time": _window(event.start_time, event.end_time, event.ends_next_day),
            "guests": event.guests,
            "cancelled": _compact(
                {"at": _day(event.cancelled_at), "reason": event.cancel_reason}
            )
            if event.cancelled_at
            else None,
            "contact": _compact(
                {
                    "name": contact.name,
                    "company": contact.company,
                    "phone": contact.phone,
                    "emails": emails,
                }
            ),
            "enquiry": _compact({"id": enquiry.id, "subject": enquiry.subject})
            if enquiry is not None
            else None,
            "booked_option": _compact(
                {
                    "label": booked.label,
                    **money.pair("minimum_spend", booked.amount_cents),
                }
            )
            if booked is not None
            else None,
            "details": _compact(
                {
                    "description": event.description,
                    "notes": event.notes,
                    "special_instructions": event.special_instructions,
                    "payment_method": event.payment_method,
                    **money.pair("deposit", event.deposit_cents),
                    "deposit_status": event.deposit_status,
                    "table_plan": event.table_plan,
                }
            ),
            "timings": _compact(
                {
                    "arrival": _hm(event.arrival_time),
                    "seated": _hm(event.seated_time),
                    "preference": event.timing_preference,
                    "notes": event.timing_notes,
                    "run_sheet": [
                        _compact(
                            {
                                "time": _hm(t.at_time),
                                "next_day": t.ends_next_day or None,
                                "label": t.label,
                                "activity": t.activity,
                                "heading": t.is_heading or None,
                            }
                        )
                        for t in children["timings"]
                    ],
                }
            ),
            "beverages": _compact(
                {
                    **money.pair("bar_tab", event.bar_tab_cents),
                    "authorised_people": event.bar_tab_authorised_people,
                    "restrictions": event.bar_tab_restrictions,
                    "notes": event.bar_tab_notes,
                    "arrival_drinks": event.arrival_drinks,
                    "table_ordering": event.table_ordering or None,
                    **money.pair(
                        "table_ordering_limit", event.table_ordering_limit_cents
                    ),
                }
            ),
            "food": _compact(
                {
                    "timing": event.food_timing_type,
                    "time": _window(event.food_time, event.food_end_time),
                    "notes": event.food_notes,
                    "allergies": (event.allergies if allergies else ALLERGIES_HIDDEN)
                    if event.allergies
                    else None,
                    "menus": [
                        _menu(m, items, money, with_items=True)
                        for m, items in menus_svc.list_menus(db, ctx, event_id=event_id)
                    ],
                }
            ),
            "front_of_house": _compact(
                {
                    "security": [
                        _compact(
                            {
                                "name": s.name,
                                "time": _window(
                                    s.start_time, s.end_time, s.ends_next_day
                                ),
                                "notes": s.notes,
                            }
                        )
                        for s in children["security"]
                    ],
                    "entertainment": [
                        _compact(
                            {
                                "kind": x.kind,
                                "name": x.name,
                                "time": _window(
                                    x.start_time, x.end_time, x.ends_next_day
                                ),
                                "link": x.link,
                                "notes": x.notes,
                            }
                        )
                        for x in children["entertainment"]
                    ],
                    "av": [
                        _compact({"kind": a.kind, "details": a.details})
                        for a in children["av"]
                    ],
                }
            ),
            "money": {
                **money.pair("minimum_spend", totals["min_spend_cents"]),
                **money.pair("food", totals["food_cents"]),
                **money.pair("bar_tab", totals["bar_tab_cents"]),
                **money.pair("expected_spend", totals["expected_spend_cents"]),
                **money.note(),
            },
            "recent_changes": [
                _compact(
                    {
                        "at": _day(c.occurred_at),
                        "kind": c.kind,
                        "section": c.section,
                        "fields": sorted(c.changes or {}) or None,
                        "by": "client"
                        if c.actor_kind == "client"
                        else change_names.get(c.actor_user_id) or c.actor_label,
                        "after_confirmation": c.after_confirmation or None,
                        "note": c.note,
                    }
                )
                for c in reversed(changes)
            ],
            "threads": _thread_summaries(
                db, ctx, enquiries_svc.thread_links(db, ctx, event_id=event_id)
            ),
        }
    )


# ----------------------------------------------------------- record_event --

EVENT_ACTIONS = (
    "update",
    "confirm_section",
    "skip_section",
    "reopen_section",
    "cancel",
    "reinstate",
)

#: The staff side of a booking. The client's own answers (CLIENT_FIELDS:
#: guests, notes, allergies, timings, bar-tab people, food timing…) are
#: theirs to give on the portal, so Norm never overwrites them.
_EVENT_PARAMS = {
    "name": ("name", None),
    "event_date": ("event_date", "date"),
    "start_time": ("start_time", "time"),
    "end_time": ("end_time", "time"),
    "ends_next_day": ("ends_next_day", "bool"),
    "description": ("description", None),
    "payment_method": ("payment_method", None),
    "deposit": ("deposit_cents", "money"),
    "deposit_status": ("deposit_status", None),
    "bar_tab": ("bar_tab_cents", "money"),
    "table_plan": ("table_plan", None),
}


def _event_fields(db: Session, ctx: FunctionsCtx, event, p: dict) -> dict:
    refused = [k for k in p if k in CLIENT_FIELDS]
    if refused:
        raise Refused(
            "Those are the client's own answers, given on their portal: "
            + ", ".join(sorted(refused))
            + ". Norm doesn't change them — ask the client, or edit them on the event page."
        )
    fields: dict = {}
    for param, (field, kind) in _EVENT_PARAMS.items():
        if param not in p:
            continue
        raw = p[param]
        if kind == "date":
            fields[field] = _date(raw, param)
        elif kind == "time":
            fields[field] = _time(raw, param)
        elif kind == "bool":
            fields[field] = _bool(raw)
        elif kind == "money":
            fields[field] = _cents(raw, param)
        else:
            fields[field] = _text(raw)
    if "area" in p:
        fields["area_id"] = _area_id(db, ctx, event.venue_id, p["area"])
    return fields


def _record_event(db: Session, ctx: FunctionsCtx, p: dict) -> dict:
    action = _text(p.get("action")) or ""
    if action not in EVENT_ACTIONS:
        raise Refused(f"action must be one of {', '.join(EVENT_ACTIONS)}.")
    event = events_svc.get_event(db, ctx, _required(p, "event_id", "event_id"))
    before = events_svc.event_state(db, ctx, event.id).status
    out = {"action": action, "event": event, "before": before}
    if action == "update":
        fields = _event_fields(db, ctx, event, p)
        if not fields:
            raise Refused("Nothing to change — name the fields to update.")
        rows = events_svc.update_event(
            db, ctx, event.id, note=_text(p.get("note")), **fields
        )
        changes: dict = {}
        for row in rows:
            changes.update(row.changes or {})
        out["changes"] = changes
        out["after_confirmation"] = any(r.after_confirmation for r in rows)
    elif action.endswith("_section"):
        section = _text(p.get("section")) or ""
        if section not in EVENT_SECTIONS:
            raise Refused(f"section must be one of {', '.join(EVENT_SECTIONS)}.")
        note = _text(p.get("note"))
        if action == "confirm_section":
            events_svc.confirm_section(db, ctx, event.id, section, note=note)
        elif action == "skip_section":
            events_svc.skip_section(db, ctx, event.id, section, note=note or "")
        else:
            events_svc.reopen_section(db, ctx, event.id, section, note=note)
        out["section"] = section
    elif action == "cancel":
        events_svc.cancel_event(
            db, ctx, event.id, reason=_required(p, "reason", "A reason")
        )
    else:
        events_svc.reinstate_event(db, ctx, event.id, reason=_text(p.get("reason")))
    out["after"] = events_svc.event_state(db, ctx, event.id).status
    return out


@_preview("record_event")
def _record_event_preview(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """The card: a dry run of the real write, rolled back."""
    money = _Money(db, ctx)
    with _dry_run(db):
        out = _record_event(db, ctx, p)
        event = out["event"]
        venue = db.query(Venue.name).filter(Venue.id == event.venue_id).scalar()
        target = f"{event.name} — {event.event_date}"
        action = out["action"]
        warnings = []
        if action == "update":
            if not out["changes"]:
                return {"done": {"event_id": event.id, "result": "no change"}}
            rows = _diff_rows(db, ctx, out["changes"], money)
            if out["after_confirmation"]:
                warnings.append(
                    "The client has confirmed this event — the change is logged as made after confirmation."
                )
            card = _card(
                "Change an event",
                target,
                venue=venue,
                changes=rows,
                warnings=warnings,
                note=_text(p.get("note")),
            )
            return {"preview": card, "basis": {"changes": out["changes"]}}
        titles = {
            "confirm_section": f"Record the client's confirmation of {out.get('section')}",
            "skip_section": f"Skip the client's {out.get('section')} section",
            "reopen_section": f"Reopen the {out.get('section')} section for the client",
            "cancel": "Cancel an event",
            "reinstate": "Reinstate a cancelled event",
        }
        rows = [{"field": "Status", "before": out["before"], "after": out["after"]}]
        if _text(p.get("reason")):
            rows.append({"field": "Reason", "after": p["reason"]})
        if action == "cancel" and event.enquiry_id:
            warnings.append("Its enquiry is closed as lost with the same reason.")
        card = _card(
            titles[action],
            target,
            venue=venue,
            changes=rows,
            warnings=warnings,
            note=_text(p.get("note")),
        )
        return {
            "preview": card,
            "basis": {k: out.get(k) for k in ("action", "section", "before", "after")},
        }


@_tool("record_event", acts=True)
def record_event(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """Staff-side changes to a booked event."""
    with _writing(db):
        out = _record_event(db, ctx, p)
    result = {
        "event_id": out["event"].id,
        "link": _event_link(out["event"].id),
        "status": out["after"],
    }
    if out["action"] == "update":
        result["changed"] = sorted(out["changes"])
    return _compact(result)


# ===========================================================================
# Mail
# ===========================================================================


def _from(name, email) -> str | None:
    if name and email:
        return f"{name} <{email}>"
    return name or email


def _snippet(text, limit: int) -> str | None:
    """Gmail's snippets arrive HTML-escaped ("it&#39;s")."""
    plain = html.unescape(text or "").strip()
    return (plain[: limit - 1] + "…" if len(plain) > limit else plain) or None


def _minute(value) -> str | None:
    """An ISO timestamp to the minute — lists don't need the seconds."""
    return value[:16] if isinstance(value, str) else value


def _thread_row(t: dict) -> dict:
    return _compact(
        {
            "account_id": t["account_id"],
            "gmail_thread_id": t["gmail_thread_id"],
            "venue": t.get("venue_name"),
            "subject": t.get("subject"),
            "from": _from(t.get("last_from_name"), t.get("last_from_email")),
            "last_message_at": _minute(t.get("last_message_at")),
            "snippet": _snippet(t.get("snippet"), 120),
            "messages": t.get("message_count"),
            "unread": t.get("unread_count") or None,
            "folder": t.get("folder") or "unfiled",
            "linked_to": [
                _compact(
                    {
                        "enquiry_id": lk.get("enquiry_id"),
                        "event_id": lk.get("event_id"),
                        "label": lk.get("label"),
                    }
                )
                for lk in t.get("links") or []
                if lk.get("visible")
            ],
            "awaiting_reply": t.get("awaiting_reply") or None,
            "norm_draft": t.get("has_norm_draft") or None,
        }
    )


#: How many threads a count pages through before saying "more than".
COUNT_CAP = 1000


@_tool("find_threads")
def find_threads(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """Threads in the venue mailboxes (the mirror), or a live Gmail search."""
    account_id = _text(p.get("account_id"))
    gmail_query = _text(p.get("gmail_query"))
    limit = max(1, min(_int(p.get("limit"), "limit") or DEFAULT_ROWS, MAX_ROWS))
    if gmail_query:
        if not account_id:
            raise Refused("A live Gmail search needs the mailbox's account_id.")
        found = inbox_svc.search_gmail(
            db, ctx, account_id, gmail_query, limit=min(limit, 50)
        )
        return {
            "threads": [
                _compact(
                    {
                        "account_id": t["account_id"],
                        "gmail_thread_id": t["gmail_thread_id"],
                        "subject": t.get("subject"),
                        "from": _from(
                            t.get("last_from_name"), t.get("last_from_email")
                        ),
                        "last_message_at": t.get("last_message_at"),
                        "snippet": _snippet(t.get("snippet"), 120),
                        "messages": t.get("message_count"),
                        "mirrored": t.get("mirrored"),
                    }
                )
                for t in found
            ],
            "count": len(found),
            "source": "live Gmail search",
        }
    folder = _text(p.get("folder"))
    if folder is not None and folder not in (*FOLDERS, "unfiled"):
        raise Refused(f"folder must be one of {', '.join((*FOLDERS, 'unfiled'))}.")
    since = None
    if _text(p.get("since")):
        try:
            since = dt.datetime.fromisoformat(str(p["since"]).strip())
        except ValueError:
            raise Refused("since must be an ISO date or date-time.")
        if since.tzinfo is None:
            since = since.replace(tzinfo=dt.timezone.utc)
    offset = max(0, _int(p.get("offset"), "offset") or 0)
    filters = dict(
        venue_id=_venue(db, ctx, p["venue"]).id if _text(p.get("venue")) else None,
        account_id=account_id,
        folder=folder,
        linked=None if p.get("linked") in (None, "") else _bool(p.get("linked")),
        unread=True if _bool(p.get("unread")) else None,
        q=_text(p.get("q")),
        since=since,
    )
    page = inbox_svc.find_threads(db, ctx, limit=limit, offset=offset, **filters)
    rows = page["threads"]
    out = {
        "threads": [_thread_row(t) for t in rows],
        "count": len(rows),
        "offset": offset,
        "has_more": page["has_more"],
    }
    if not page["has_more"]:
        out["total"] = offset + len(rows)
    elif _bool(p.get("count")):
        # A sweep's denominator: page through the same filter (the mirror
        # only — cheap), up to COUNT_CAP.
        total, start, more = 0, 0, True
        while more and start < COUNT_CAP:
            chunk = inbox_svc.find_threads(db, ctx, limit=200, offset=start, **filters)
            total += len(chunk["threads"])
            more = chunk["has_more"]
            start += 200
        out["total"] = total if not more else f"more than {total}"
    return out


def _direction(m: dict) -> str:
    if m.get("is_auto_reply"):
        return "auto-reply"
    if m.get("is_internal"):
        return "internal"
    if m.get("is_inbound") is None:
        return "unknown"
    return "from customer" if m["is_inbound"] else "from us"


@_tool("read_thread")
def read_thread(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """One thread as a conversation — live bodies as plain text."""
    account_id = _required(p, "account_id", "account_id")
    thread_id = _required(p, "gmail_thread_id", "gmail_thread_id")
    detail = _text(p.get("detail")) or "digest"
    if detail not in ("digest", "full"):
        raise Refused("detail must be digest or full.")
    per_message = max(200, min(_int(p.get("max_chars"), "max_chars") or 4000, 12000))
    thread = inbox_svc.read_thread(db, ctx, account_id, thread_id)
    messages = thread.get("messages") or []
    # Newest first gets the budget: what we owe a reply to is at the end.
    budget = THREAD_TEXT_BUDGET
    texts: list[str | None] = [None] * len(messages)
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        text = (m.get("text") or "").strip() if detail == "full" else ""
        if not text or budget <= 0:
            texts[i] = None
            continue
        cut = text[: min(per_message, budget)]
        if len(cut) < len(text):
            cut += " […]"
        texts[i] = cut
        budget -= len(cut)
    real = [
        m for m in messages if not m.get("is_internal") and not m.get("is_auto_reply")
    ]
    venue = (
        db.query(Venue.name).filter(Venue.id == thread.get("venue_id")).scalar()
        if thread.get("venue_id")
        else None
    )
    return _compact(
        {
            "account_id": thread["account_id"],
            "gmail_thread_id": thread["gmail_thread_id"],
            "mailbox": thread.get("mailbox"),
            "venue": venue,
            "folder": thread.get("folder") or "unfiled",
            "linked_to": [
                _compact(
                    {
                        "link_id": lk.get("link_id"),
                        "enquiry_id": lk.get("enquiry_id"),
                        "event_id": lk.get("event_id"),
                        "label": lk.get("label"),
                    }
                )
                for lk in thread.get("links") or []
                if lk.get("visible")
            ],
            "awaiting_reply": bool(real and real[-1].get("is_inbound")) or None,
            "norm_drafts": [
                _compact(
                    {
                        "id": d["id"],
                        "kind": d["kind"],
                        "subject": d.get("subject"),
                        "in_gmail": d.get("locked") or None,
                    }
                )
                for d in thread.get("drafts") or []
            ],
            "bodies": "live"
            if thread.get("bodies_available")
            else thread.get("bodies_error"),
            "messages": [
                _compact(
                    {
                        "id": m.get("gmail_message_id"),
                        "at": m.get("internal_date"),
                        "from": _from(m.get("from_name"), m.get("from_email")),
                        "to": m.get("to"),
                        "cc": m.get("cc"),
                        "subject": m.get("subject"),
                        "direction": _direction(m),
                        "text": texts[i],
                        "snippet": None
                        if texts[i]
                        else _snippet(m.get("snippet"), 200),
                        "attachments": [
                            a.get("filename") for a in m.get("attachments") or []
                        ],
                    }
                )
                for i, m in enumerate(messages)
            ],
        }
    )


# --------------------------------------------------------------- filing --


def _file_plan(db: Session, ctx: FunctionsCtx, p: dict) -> dict:
    """What file_thread will do, checked without writing: the mailbox,
    the folder, links to add or end, an enquiry to create."""
    account = accounts_svc.get_account(
        db, ctx, _required(p, "account_id", "account_id")
    )
    thread_id = _required(p, "gmail_thread_id", "gmail_thread_id")
    plan: dict = {"account": account, "thread_id": thread_id}
    if "folder" in p:
        folder = _text(p.get("folder"))
        folder = None if folder in (None, "unfiled") else folder
        if folder is not None and folder not in FOLDERS:
            raise Refused(f"folder must be one of {', '.join((*FOLDERS, 'unfiled'))}.")
        plan["folder"] = folder
    link_enquiry, link_event = (
        _text(p.get("link_enquiry_id")),
        _text(p.get("link_event_id")),
    )
    create = _bool(p.get("create_enquiry"))
    if sum(map(bool, (link_enquiry, link_event, create))) > 1:
        raise Refused(
            "Link to one enquiry, one event, or create an enquiry — one at a time."
        )
    if link_enquiry:
        plan["link_enquiry"] = enquiries_svc.get_enquiry(db, ctx, link_enquiry)
    if link_event:
        plan["link_event"] = events_svc.get_event(db, ctx, link_event)
    if create:
        booking_type = _text(p.get("booking_type"))
        if booking_type is not None and booking_type not in BOOKING_TYPES:
            raise Refused(f"booking_type must be one of {', '.join(BOOKING_TYPES)}.")
        plan["create"] = {
            "booking_type": booking_type,
            "subject": _text(p.get("subject")),
            "venue_id": _venue(db, ctx, p["venue"]).id
            if _text(p.get("venue"))
            else None,
            "contact_name": _text(p.get("contact_name")),
            "contact_email": _text(p.get("contact_email")),
        }
    live = (
        db.query(FnThreadLink)
        .filter(
            FnThreadLink.organization_id == ctx.org_id,
            FnThreadLink.account_id == account.id,
            FnThreadLink.gmail_thread_id == thread_id,
            FnThreadLink.unlinked_at.is_(None),
        )
        .all()
    )
    plan["links"] = live
    plan["unlink"] = live if _bool(p.get("unlink")) else []
    if (
        not any(k in plan for k in ("folder", "link_enquiry", "link_event", "create"))
        and not plan["unlink"]
    ):
        raise Refused(
            "Nothing to do — give a folder, a link, unlink, or create_enquiry."
        )
    require_live(ctx)  # the card is computed by reading; shadow can't file
    return plan


def _link_label(db: Session, ctx: FunctionsCtx, link) -> str:
    if link.enquiry_id:
        e = enquiries_svc.get_enquiry_or_none(db, ctx, link.enquiry_id)
        return f"enquiry “{e.subject or e.id}”" if e else "an enquiry you can't see"
    ev = (
        db.query(FnEvent)
        .filter(FnEvent.organization_id == ctx.org_id, FnEvent.id == link.event_id)
        .first()
    )
    return f"event “{ev.name}”" if ev else "an event"


def _customer_message(db: Session, account_id: str, thread_id: str):
    """The thread's newest message from the customer (not internal, not an
    auto-reply) — who wrote in."""
    return (
        db.query(FnMailboxMessage)
        .filter(
            FnMailboxMessage.account_id == account_id,
            FnMailboxMessage.gmail_thread_id == thread_id,
            FnMailboxMessage.is_inbound.is_(True),
            FnMailboxMessage.is_internal.is_(False),
            FnMailboxMessage.is_auto_reply.is_(False),
        )
        .order_by(FnMailboxMessage.internal_date.desc())
        .first()
    )


def _thread_customer(db: Session, account_id: str, thread_id: str) -> str | None:
    """Who wrote in, as a card shows it (the service decides for real)."""
    m = _customer_message(db, account_id, thread_id)
    return _from(m.from_name, m.reply_to_email or m.from_email) if m else None


@_preview("file_thread")
def _file_thread_preview(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """The card, by reading — the mailbox services commit, so no dry run."""
    plan = _file_plan(db, ctx, p)
    account, thread_id = plan["account"], plan["thread_id"]
    t = (
        db.query(FnMailboxThread)
        .filter(
            FnMailboxThread.account_id == account.id,
            FnMailboxThread.gmail_thread_id == thread_id,
        )
        .first()
    )
    folder_row = (
        db.query(FnThreadFolder)
        .filter(
            FnThreadFolder.organization_id == ctx.org_id,
            FnThreadFolder.account_id == account.id,
            FnThreadFolder.gmail_thread_id == thread_id,
        )
        .first()
    )
    current = folder_row.folder if folder_row else None
    rows = []
    if "folder" in plan and plan["folder"] != current:
        rows.append(
            {
                "field": "Folder",
                "before": current or "unfiled",
                "after": plan["folder"] or "unfiled",
            }
        )
    for link in plan["unlink"]:
        rows.append({"field": "Unlink from", "after": _link_label(db, ctx, link)})
    if plan.get("link_enquiry") is not None:
        e = plan["link_enquiry"]
        rows.append({"field": "Link to", "after": f"enquiry “{e.subject or e.id}”"})
    if plan.get("link_event") is not None:
        rows.append({"field": "Link to", "after": f"event “{plan['link_event'].name}”"})
    if "create" in plan:
        c = plan["create"]
        who = (
            _from(c["contact_name"], c["contact_email"])
            or _thread_customer(db, account.id, thread_id)
            or "the customer who wrote in"
        )
        rows.append({"field": "New enquiry for", "after": who})
        if c["booking_type"]:
            rows.append(
                {"field": "Booking type", "after": BOOKING_LABELS[c["booking_type"]]}
            )
    if not rows:
        return {
            "done": {
                "gmail_thread_id": thread_id,
                "folder": current,
                "result": "already filed that way",
            }
        }
    warnings = []
    if not accounts_svc.is_connected(account) and "folder" in plan:
        warnings.append(
            "The mailbox isn't connected, so Gmail's labels won't change — only Norm's filing."
        )
    card = _card(
        "File an inbox thread",
        (t.subject if t else None) or thread_id,
        venue=db.query(Venue.name).filter(Venue.id == account.venue_id).scalar(),
        changes=rows,
        warnings=warnings,
    )
    basis = {
        "thread": [account.id, thread_id],
        "folder_before": current,
        "links_before": sorted(lk.id for lk in plan["links"]),
        "changes": rows,
    }
    return {"preview": card, "basis": basis}


@_tool("file_thread", acts=True)
def file_thread(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """File a thread, link or unlink it, or make an enquiry from it."""
    plan = _file_plan(db, ctx, p)
    account, thread_id = plan["account"], plan["thread_id"]
    result: dict = {"gmail_thread_id": thread_id}
    # The mailbox services commit (they never hold a connection while Gmail
    # works), so they run outside any savepoint — the new link first, so a
    # refused one leaves the thread's old links as they were.
    if "create" in plan:
        made = inbox_svc.create_enquiry_from_thread(
            db, ctx, account.id, thread_id, **plan["create"]
        )
        result.update(
            enquiry_id=made["enquiry_id"],
            link=_enquiry_link(made["enquiry_id"]),
            contact_id=made["contact_id"],
        )
    elif plan.get("link_enquiry") is not None:
        inbox_svc.link_thread(
            db, ctx, account.id, thread_id, enquiry_id=plan["link_enquiry"].id
        )
        result["linked_enquiry_id"] = plan["link_enquiry"].id
    elif plan.get("link_event") is not None:
        inbox_svc.link_thread(
            db, ctx, account.id, thread_id, event_id=plan["link_event"].id
        )
        result["linked_event_id"] = plan["link_event"].id
    kept = {result.get("linked_enquiry_id"), result.get("linked_event_id")} - {None}
    ending = [lk for lk in plan["unlink"] if (lk.enquiry_id or lk.event_id) not in kept]
    if ending:
        with _writing(db):
            for link in ending:
                enquiries_svc.unlink_thread(db, ctx, link.id)
        result["unlinked"] = len(ending)
    if "folder" in plan:
        filed = inbox_svc.file_thread(db, ctx, account.id, thread_id, plan["folder"])
        result.update(
            folder=filed["folder"] or "unfiled",
            gmail_label_updated=filed["gmail_synced"],
            gmail_note=filed["gmail_error"],
        )
    return _compact(result)


# --------------------------------------------------------------- drafts --


def _route(db: Session, ctx: FunctionsCtx, *, enquiry=None, event=None):
    """(mailbox, thread) a draft about a record goes on: the record's newest
    linked thread on a connected mailbox, else a new email from the venue's
    mailbox — the outbox's own rule (sending._route). (None, None) when the
    venue has no connected mailbox: the draft is still written."""
    from app.services.functions.mailbox import sending

    link_filter = (
        FnThreadLink.enquiry_id == enquiry.id
        if enquiry is not None
        else FnThreadLink.event_id == event.id
    )
    if event is not None and event.enquiry_id:
        link_filter = (FnThreadLink.event_id == event.id) | (
            FnThreadLink.enquiry_id == event.enquiry_id
        )
    venue_id = (enquiry or event).venue_id
    try:
        return sending._route(
            db,
            ctx,
            venue_id=venue_id,
            account_id=None,
            gmail_thread_id=None,
            link_filter=link_filter,
        )
    except accounts_svc.MailboxUnavailable:
        return None, None


def _primary_email(db: Session, ctx: FunctionsCtx, contact_id: str) -> str | None:
    row = (
        db.query(FnContactEmail.email)
        .filter(
            FnContactEmail.organization_id == ctx.org_id,
            FnContactEmail.contact_id == contact_id,
        )
        .order_by(FnContactEmail.is_primary.desc(), FnContactEmail.created_at)
        .first()
    )
    return row[0] if row else None


#: Reply-template placeholders, by the Inbox composer's rules
#: (web inbox/text.ts): ``{{client_name}}`` and ``{{sender_name}}`` are filled
#: with first names; the outbox fills ``{{quote_link}}`` / ``{{portal_link}}``
#: when it sends; any other token, or a bracketed blank like "[DATE]", must be
#: filled by whoever writes the email before a customer sees it.
_TOKEN = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}", re.I)
#: Wider than the composer's pattern on purpose: a flagged spot such as
#: "[SPEND — check with James]" (the inbox sweep's convention) has a dash.
_BRACKET_BLANK = re.compile(r"\[[A-Z][^\[\]\n]{0,60}\]")
SERVER_TOKENS = ("quote_link", "portal_link")


def _first_name(name) -> str:
    text = (name or "").strip()
    return "" if not text or "@" in text else text.split()[0]


def _fill_names(body: str, values: dict) -> str:
    """Fill the known tokens (escaped); an unknown or empty one stays as
    written, so _blanks reports it."""

    def fill(m):
        value = values.get(m.group(1).lower())
        return html.escape(value) if value else m.group(0)

    return _TOKEN.sub(fill, body or "")


def _blanks(*texts) -> list[str]:
    """Placeholders still unfilled in what a customer would read."""
    found: list[str] = []
    for text in texts:
        plain = html_to_text(text)
        found += [
            m.group(0)
            for m in _TOKEN.finditer(plain)
            if m.group(1).lower() not in SERVER_TOKENS
        ]
        found += [m.group(0) for m in _BRACKET_BLANK.finditer(plain)]
    return list(dict.fromkeys(found))


def _name_values(
    db: Session, ctx: FunctionsCtx, *, contact_id=None, account_id=None, thread_id=None
) -> dict:
    """{{client_name}}: the record's contact, else whoever wrote in on the
    thread; {{sender_name}}: the person the email goes out from."""
    client = None
    if contact_id:
        client = (
            db.query(FnContact.name)
            .filter(FnContact.organization_id == ctx.org_id, FnContact.id == contact_id)
            .scalar()
        )
    elif account_id and thread_id:
        m = _customer_message(db, account_id, thread_id)
        client = m.from_name if m else None
    sender = getattr(ctx.user, "full_name", None)
    return {
        "client_name": _first_name(client),
        "sender_name": _first_name(sender) or (sender or ""),
    }


def _draft_contact_id(db: Session, ctx: FunctionsCtx, draft) -> str | None:
    if draft.enquiry_id:
        return enquiries_svc.get_enquiry(db, ctx, draft.enquiry_id).contact_id
    if draft.event_id:
        return events_svc.get_event(db, ctx, draft.event_id).contact_id
    return None


def _draft_result(db: Session, ctx: FunctionsCtx, draft, notes: list[str]) -> dict:
    account = (
        db.query(FnMailboxAccount)
        .filter(FnMailboxAccount.id == draft.account_id)
        .first()
        if draft.account_id
        else None
    )
    warnings = list(notes)
    about = draft.enquiry_id or pricing.linked_enquiry(
        db, ctx, draft.account_id, draft.gmail_thread_id
    )
    if (
        draft.kind == "email"
        and about
        and pricing.contains_price(html_to_text(draft.body_html))
        and not pricing.quote_sent(db, ctx.org_id, about)
    ):
        warnings.append(
            "This email quotes a price and no proposal has been sent for the enquiry. "
            "A price belongs in a proposal (build_proposal, then a proposal draft); sent "
            "as a plain email it needs a reason the person gives, and the enquiry stays "
            "replied, not quoted."
        )
    if (
        draft.kind == "proposal"
        and proposals_svc.get_proposal(db, ctx, draft.enquiry_id) is None
    ):
        warnings.append(
            "The enquiry has no proposal yet — build it (build_proposal) before this is sent."
        )
    blanks = _blanks(draft.subject, draft.body_html)
    if blanks:
        warnings.append(
            "Unfilled blanks: " + ", ".join(blanks) + " — fill them (draft_email with "
            "draft_id) before it's sent; Norm won't send it with blanks."
        )
    if account is None:
        warnings.append(
            "No connected venue mailbox to send it from yet — connect one in the Inbox."
        )
    elif not accounts_svc.is_connected(account):
        warnings.append(
            f"{account.google_email} isn't connected — it can't be sent until it is."
        )
    return _compact(
        {
            "draft_id": draft.id,
            "version": draft.version,
            "kind": draft.kind,
            "status": draft.status,
            "in_gmail": draft.owner == "gmail" or None,
            "mailbox": account.google_email if account else None,
            "mailbox_signature": bool(account and account.signature_html),
            "reply_on_thread": draft.gmail_thread_id,
            "to": list(draft.to_emails or []),
            "cc": list(draft.cc_emails or []),
            "subject": draft.subject,
            "enquiry_id": draft.enquiry_id,
            "event_id": draft.event_id,
            "warnings": warnings,
            "next": (
                "Waiting in the Functions Inbox for staff to review and send. To send it from "
                "chat, call send_proposal (a proposal) or send_email with this draft_id — the "
                "person approves it on a card."
            ),
        }
    )


@_tool("draft_email", acts=True)
def draft_email(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """Write (or revise, discard, hand to Gmail) a Norm draft — never sends."""
    draft_id = _text(p.get("draft_id"))
    if draft_id and _bool(p.get("discard")):
        drafts_svc.discard_draft(db, ctx, draft_id)
        return {"draft_id": draft_id, "status": "discarded"}
    notes: list[str] = []
    if draft_id:
        fields = {}
        for param, field in (
            ("to", "to_emails"),
            ("cc", "cc_emails"),
            ("bcc", "bcc_emails"),
        ):
            if param in p:
                fields[field] = [str(a) for a in _list(p[param])]
        if "subject" in p:
            fields["subject"] = _text(p["subject"]) or ""
        if "body" in p:
            existing = drafts_svc.get_draft(db, ctx, draft_id)
            fields["body_html"] = _fill_names(
                p.get("body") or "",
                _name_values(
                    db,
                    ctx,
                    contact_id=_draft_contact_id(db, ctx, existing),
                    account_id=existing.account_id,
                    thread_id=existing.gmail_thread_id,
                ),
            )
        if fields:
            with _writing(db):
                draft = drafts_svc.update_draft(db, ctx, draft_id, **fields)
        else:
            draft = drafts_svc.get_draft(db, ctx, draft_id)
    else:
        body = p.get("body")
        if not _text(body):
            raise Refused("Write the email's body.")
        kind = _text(p.get("kind")) or "email"
        if kind not in ("email", "proposal"):
            raise Refused("kind must be email or proposal.")
        enquiry = (
            enquiries_svc.get_enquiry(db, ctx, p["enquiry_id"])
            if _text(p.get("enquiry_id"))
            else None
        )
        event = (
            events_svc.get_event(db, ctx, p["event_id"])
            if _text(p.get("event_id"))
            else None
        )
        if kind == "proposal" and enquiry is None:
            raise Refused("A proposal email belongs to an enquiry — give enquiry_id.")
        account_id, thread_id = (
            _text(p.get("account_id")),
            _text(p.get("gmail_thread_id")),
        )
        if thread_id and not account_id:
            raise Refused("A reply needs the mailbox the thread is in (account_id).")
        if not thread_id and (enquiry is not None or event is not None):
            routed, routed_thread = _route(db, ctx, enquiry=enquiry, event=event)
            if routed is not None and not account_id:
                account_id, thread_id = routed.id, routed_thread
        to = [str(a) for a in _list(p.get("to"))]
        cc = [str(a) for a in _list(p.get("cc"))]
        subject = _text(p.get("subject"))
        if thread_id and not to:
            # The reply's recipients, read from the live thread by the
            # composer's own rule, so the draft (and its approval card) say
            # exactly who it goes to.
            try:
                defaults = inbox_svc.prepare_compose(
                    db, ctx, account_id, mode="reply", gmail_thread_id=thread_id
                )
                to, cc = defaults["to"], cc or defaults["cc"]
                subject = subject or defaults["subject"]
            except (FunctionsError, GmailError):
                notes.append(
                    "Norm couldn't read the thread's recipients just now — the reply goes "
                    "to whoever wrote last on the thread when it's sent."
                )
        if not thread_id and not to:
            record = enquiry or event
            primary = (
                _primary_email(db, ctx, record.contact_id)
                if record is not None
                else None
            )
            if not primary:
                raise Refused(
                    "Who is this to? Give `to` (the customer has no email address on file)."
                )
            to = [primary]
        if not thread_id and not subject:
            subject = (
                (enquiry.subject if enquiry is not None else None)
                or (event.name if event is not None else None)
                or "Your enquiry"
            )
        with _writing(db):
            draft = drafts_svc.create_draft(
                db,
                ctx,
                account_id=account_id,
                enquiry_id=enquiry.id if enquiry is not None else None,
                event_id=event.id if event is not None else None,
                gmail_thread_id=thread_id,
                kind=kind,
                to=to,
                cc=cc,
                bcc=[str(a) for a in _list(p.get("bcc"))],
                subject=subject or "",
                body_html=_fill_names(
                    body,
                    _name_values(
                        db,
                        ctx,
                        contact_id=(enquiry or event).contact_id
                        if (enquiry or event) is not None
                        else None,
                        account_id=account_id,
                        thread_id=thread_id,
                    ),
                ),
            )
    if _bool(p.get("hand_to_gmail")):
        # Commits and calls Gmail — last, outside any savepoint.
        draft = drafts_svc.send_to_gmail(db, ctx, draft.id)
        notes.append(
            "Handed to Gmail: it's now a draft in the venue mailbox, locked in Norm."
        )
    return _draft_result(db, ctx, draft, notes)


# ---------------------------------------------------------------- sends --


def _idempotency_key(p: dict) -> str:
    """The key pinned into the approved call (the card's run params)."""
    return _text(p.get("idempotency_key")) or f"norm-agent-{uuid.uuid4()}"


def _send_in_flight(db: Session, ctx: FunctionsCtx, draft_id: str, key: str | None):
    """An earlier send of this draft that went (or may have gone) — a second
    card for the same draft must not send it twice."""
    q = db.query(FnOutbox).filter(
        FnOutbox.organization_id == ctx.org_id,
        FnOutbox.email_draft_id == draft_id,
        FnOutbox.status.in_(("sending", "sent")),
    )
    if key:
        q = q.filter(FnOutbox.idempotency_key != key)
    return q.first()


def _sendable_draft(db: Session, ctx: FunctionsCtx, p: dict, kind: str):
    draft = drafts_svc.get_draft(db, ctx, _required(p, "draft_id", "draft_id"))
    if draft.status != "open":
        raise Refused(f"That draft was already {draft.status}.")
    if draft.owner != "norm":
        raise Refused(
            "That draft is in Gmail now — send it there, or bring it back to Norm first."
        )
    if draft.kind != kind:
        other = "send_proposal" if draft.kind == "proposal" else "send_email"
        raise Refused(f"That is a {draft.kind} draft — send it with {other}.")
    if not draft.account_id:
        raise Refused(
            "The draft has no mailbox to send from — connect the venue's mailbox in the Inbox."
        )
    account = accounts_svc.get_account(db, ctx, draft.account_id)
    accounts_svc.require_sendable(db, ctx, account)
    blanks = _blanks(draft.subject, draft.body_html)
    if blanks:
        raise Refused(
            "The draft still has unfilled blanks: "
            + ", ".join(blanks)
            + " — fill them (draft_email with draft_id) before it goes to the customer."
        )
    earlier = _send_in_flight(db, ctx, draft.id, _text(p.get("idempotency_key")))
    if earlier is not None:
        raise Refused(
            "This draft was already sent."
            if earlier.status == "sent"
            else "This draft is being sent — Norm confirms it within about ten minutes; don't send it again."
        )
    return draft, account


def _recipients(db: Session, draft) -> str:
    if draft.to_emails:
        return ", ".join(draft.to_emails)
    customer = (
        _thread_customer(db, draft.account_id, draft.gmail_thread_id)
        if draft.gmail_thread_id
        else None
    )
    return (
        f"whoever wrote last on the thread ({customer})"
        if customer
        else "whoever wrote last on the thread"
    )


def _send_rows(db: Session, ctx: FunctionsCtx, draft, account) -> list[dict]:
    thread = (
        db.query(FnMailboxThread.subject)
        .filter(
            FnMailboxThread.account_id == account.id,
            FnMailboxThread.gmail_thread_id == draft.gmail_thread_id,
        )
        .scalar()
        if draft.gmail_thread_id
        else None
    )
    rows = [
        {"field": "From", "after": account.google_email},
        {"field": "To", "after": _recipients(db, draft)},
    ]
    if draft.cc_emails:
        rows.append({"field": "Cc", "after": ", ".join(draft.cc_emails)})
    if draft.bcc_emails:
        rows.append({"field": "Bcc", "after": ", ".join(draft.bcc_emails)})
    rows.append(
        {
            "field": "Subject",
            "after": draft.subject or (f"Re: {thread}" if thread else "—"),
        }
    )
    rows.append(
        {
            "field": "Sent as",
            "after": f"a reply on “{thread or 'the thread'}”"
            if draft.gmail_thread_id
            else "a new email",
        }
    )
    return rows


def _send_basis(draft, extra: dict) -> dict:
    return {
        "draft": draft.id,
        "version": draft.version,
        "to": list(draft.to_emails or []),
        "subject": draft.subject,
        "body": hashlib.sha256((draft.body_html or "").encode()).hexdigest()[:16],
        **extra,
    }


def _sent(result: dict) -> dict:
    status = result.get("status")
    out = {
        "status": status,
        "outbox_id": result.get("outbox_id"),
        "gmail_thread_id": result.get("gmail_thread_id"),
        "duplicate": result.get("duplicate") or None,
    }
    if status == "sending":
        out["note"] = (
            "Gmail's answer was lost — Norm confirms the send within about ten "
            "minutes. Don't send it again."
        )
    return _compact(out)


@_preview("send_email")
def _send_email_preview(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """The card: exactly the draft, its recipients and any pricing warning.
    Pins the send's idempotency key into the approved call."""
    draft, account = _sendable_draft(db, ctx, p, "email")
    enquiry_id = draft.enquiry_id or pricing.linked_enquiry(
        db, ctx, account.id, draft.gmail_thread_id
    )
    try:
        override = pricing.check(
            db, ctx, enquiry_id, draft.body_html, _text(p.get("price_override_reason"))
        )
    except pricing.PricingWarning as exc:
        raise Refused(
            f"{exc.message} Prefer a proposal (build_proposal, then a proposal draft). "
            "Only if the person still wants this email sent, call send_email again "
            "with price_override_reason set to the reason THEY give."
        )
    warnings = []
    if override:
        warnings.append(
            f"It quotes a price and no proposal has been sent — sending anyway, with this reason recorded: {override}"
        )
    venue = db.query(Venue.name).filter(Venue.id == account.venue_id).scalar()
    card = _card(
        "Send an email to a customer",
        draft.subject or "a reply",
        venue=venue,
        changes=_send_rows(db, ctx, draft, account),
        warnings=warnings,
        note=_opening(draft.body_html),
    )
    key = _idempotency_key(p)
    return {
        "preview": card,
        "basis": _send_basis(draft, {"override": override}),
        "run_params": {**p, "idempotency_key": key, "draft_version": draft.version},
    }


def _require_key_and_version(p: dict, draft) -> str:
    key = _text(p.get("idempotency_key"))
    if not key:
        raise Refused(
            "A customer email is sent only from an approval card — ask again so the person can approve it."
        )
    if (
        p.get("draft_version") not in (None, "")
        and int(p["draft_version"]) != draft.version
    ):
        raise Refused(
            "The draft changed after it was approved — show it again before sending."
        )
    return key


@_tool("send_email", acts=True)
def send_email(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """Send an approved email draft through the venue mailbox's outbox."""
    draft, _account = _sendable_draft(db, ctx, p, "email")
    key = _require_key_and_version(p, draft)
    result = drafts_svc.send_draft(
        db,
        ctx,
        draft.id,
        idempotency_key=key,
        price_override_reason=_text(p.get("price_override_reason")),
    )
    return {"draft_id": draft.id, **_sent(result.as_dict())}


@_preview("send_proposal")
def _send_proposal_preview(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """The card: the draft, the options the customer can confirm and the
    money check. Pins the send's idempotency key into the approved call."""
    draft, account = _sendable_draft(db, ctx, p, "proposal")
    enquiry = enquiries_svc.get_enquiry(db, ctx, draft.enquiry_id)
    enquiries_svc.require_open_enquiry(db, ctx, enquiry)
    proposal = proposals_svc.get_proposal(db, ctx, enquiry.id)
    if proposal is None or not (proposal.html or "").strip():
        raise Refused(
            "The enquiry has no proposal yet — build it with build_proposal first."
        )
    money = _Money(db, ctx)
    offered = proposals_svc.option_snapshot(db, ctx, enquiry.id)
    check = proposals_svc.check_consistency(db, ctx, enquiry.id)
    rows = _send_rows(db, ctx, draft, account)
    for o in offered:
        state = "can confirm" if o["confirmable"] else "can't confirm until approved"
        rows.append(
            {
                "field": f"Option: {o['label'] or o['area_name'] or 'option'}",
                "after": " · ".join(
                    x
                    for x in (
                        o["event_date"],
                        _window(
                            dt.time.fromisoformat(o["start_time"])
                            if o["start_time"]
                            else None,
                            dt.time.fromisoformat(o["end_time"])
                            if o["end_time"]
                            else None,
                            o["ends_next_day"],
                        ),
                        f"{money(o['total_cents'])} total",
                        state,
                    )
                    if x
                ),
            }
        )
    warnings = list(check["warnings"])
    if not offered:
        warnings.append(
            "No option is on offer — the customer can read the proposal but has nothing to confirm."
        )
    card = _card(
        "Send a proposal to a customer",
        enquiry.subject or draft.subject or "the proposal",
        venue=db.query(Venue.name).filter(Venue.id == account.venue_id).scalar(),
        changes=rows,
        warnings=warnings,
        note=_opening(draft.body_html)
        + " · The quote link goes where {{quote_link}} sits, or after the message.",
    )
    basis = _send_basis(
        draft,
        {
            "proposal_version": proposal.version,
            "options": [
                [o["min_spend_id"], o["amount_cents"], o["total_cents"], o["state"]]
                for o in offered
            ],
        },
    )
    return {
        "preview": card,
        "basis": basis,
        "run_params": {
            **p,
            "idempotency_key": _idempotency_key(p),
            "draft_version": draft.version,
        },
    }


@_tool("send_proposal", acts=True)
def send_proposal(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """Send an approved proposal draft: freezes the proposal, carries the
    quote link, and makes the enquiry quoted once Gmail confirms."""
    draft, _account = _sendable_draft(db, ctx, p, "proposal")
    key = _require_key_and_version(p, draft)
    result = drafts_svc.send_draft(db, ctx, draft.id, idempotency_key=key)
    out = {"draft_id": draft.id, "enquiry_id": draft.enquiry_id, **_sent(result)}
    out["quote_url"] = result.get("quote_url")
    return _compact(out)


# ===========================================================================
# Proposals
# ===========================================================================


def _option_fields(db: Session, ctx: FunctionsCtx, item: dict, venue_id: str) -> dict:
    fields: dict = {}
    if "label" in item:
        fields["label"] = _text(item["label"])
    if "amount" in item:
        cents = _cents(item["amount"], "amount")
        if cents is None:
            raise Refused("An option's amount can't be empty.")
        fields["amount_cents"] = cents
    for key, field, parse in (
        ("date", "event_date", _date),
        ("start_time", "start_time", _time),
        ("end_time", "end_time", _time),
    ):
        if key in item:
            fields[field] = parse(item[key], key)
    if "ends_next_day" in item:
        fields["ends_next_day"] = _bool(item["ends_next_day"])
    if "notes" in item:
        fields["notes"] = _text(item["notes"])
    if "area" in item:
        fields["area_id"] = _area_id(db, ctx, venue_id, item["area"])
    return fields


def _apply_options(
    db: Session, ctx: FunctionsCtx, enquiry, items: list[dict]
) -> list[str]:
    """Add, change or withdraw minimum-spend options. Anything approved (or
    booked) is read-only to Norm — Orbit's incident: an agent once edited an
    approved $6,000 option to $7,500 and it kept the approval."""
    done = []
    states = {
        o.id: (o, state)
        for o, state, _ in min_spends_svc.list_min_spends(
            db, ctx, enquiry_id=enquiry.id
        )
    }
    for item in items:
        option_id = _text(item.get("option_id"))
        if option_id:
            if option_id not in states:
                raise Refused(f"No option {option_id} on this enquiry.")
            option, state = states[option_id]
            label = option.label or "That option"
            if state in ("approved", "confirmed"):
                raise Refused(APPROVED_READ_ONLY.format(label=label))
            if state == "withdrawn":
                raise Refused(f"{label} was withdrawn — add a new option instead.")
            if _bool(item.get("withdraw")):
                min_spends_svc.withdraw_min_spend(
                    db, ctx, option_id, reason=_text(item.get("reason"))
                )
                done.append(f"withdrew {label}")
                continue
            venue_id = (
                _venue(db, ctx, item["venue"]).id
                if _text(item.get("venue"))
                else option.venue_id
            )
            fields = _option_fields(db, ctx, item, venue_id)
            if venue_id != option.venue_id:
                fields["venue_id"] = venue_id
            if fields:
                min_spends_svc.update_min_spend(db, ctx, option_id, **fields)
                done.append(f"changed {label}")
            continue
        venue_id = (
            _venue(db, ctx, item["venue"]).id
            if _text(item.get("venue"))
            else enquiry.venue_id
        )
        if not venue_id:
            raise Refused("A group-wide enquiry's option needs a venue.")
        if not _text(item.get("area")):
            raise Refused(
                "Every new option names its area (get_venue_profile lists them)."
            )
        fields = _option_fields(db, ctx, item, venue_id)
        if "amount_cents" not in fields:
            raise Refused("A new option needs its minimum spend (amount, in dollars).")
        min_spends_svc.add_min_spend(
            db,
            ctx,
            enquiry_id=enquiry.id,
            venue_id=venue_id,
            amount_cents=fields.pop("amount_cents"),
            **fields,
        )
        done.append(f"added {fields.get('label') or 'an option'}")
    return done


def _set_quantities(db: Session, ctx: FunctionsCtx, menu_id: str, wanted: dict) -> None:
    """Dish quantities by item id or by (unique) dish name."""
    _menu_row, items = menus_svc.get_menu(db, ctx, menu_id)
    dishes = [i for i in items if i.kind != "header"]
    by_id = {i.id: i for i in dishes}
    quantities = {}
    for key, qty in wanted.items():
        item = by_id.get(key)
        if item is None:
            hits = [
                i for i in dishes if (i.name or "").lower() == str(key).lower()
            ] or [i for i in dishes if str(key).lower() in (i.name or "").lower()]
            if len(hits) != 1:
                raise Refused(
                    f"'{key}' matches {'no' if not hits else 'more than one'} dish on that menu — use the item id."
                )
            item = hits[0]
        quantities[item.id] = _int(qty, f"quantity for {key}") or 0
    menus_svc.set_item_quantities(db, ctx, menu_id, quantities)


def _apply_menus(
    db: Session, ctx: FunctionsCtx, enquiry, items: list[dict]
) -> list[str]:
    done = []
    for item in items:
        menu_id = _text(item.get("menu_id"))
        guests = _int(item.get("guest_count"), "guest_count")
        if menu_id:
            menu, _ = menus_svc.get_menu(db, ctx, menu_id)
            if menu.enquiry_id != enquiry.id:
                raise Refused("That menu belongs to another enquiry.")
            if _bool(item.get("remove")):
                menus_svc.delete_menu(db, ctx, menu_id)
                done.append(f"removed {menu.name}")
                continue
            if guests is not None:
                menus_svc.update_menu(db, ctx, menu_id, guest_count=guests)
        else:
            template_id = _required(
                item,
                "template_id",
                "A menu's template_id (get_venue_profile lists them)",
            )
            menu = menus_svc.add_menu_from_template(
                db,
                ctx,
                template_id=template_id,
                enquiry_id=enquiry.id,
                guest_count=guests or 0,
            )
            menu_id = menu.id
            done.append(f"added {menu.name}")
        if menu.is_set_menu and _bool(item.get("distribute"), default=True):
            menus_svc.distribute_set_menu(db, ctx, menu_id)
        quantities = item.get("quantities")
        if isinstance(quantities, str):
            try:
                quantities = json.loads(quantities)
            except ValueError:
                raise Refused(
                    "quantities must map dish names (or item ids) to numbers."
                )
        if quantities:
            if not isinstance(quantities, dict):
                raise Refused(
                    "quantities must map dish names (or item ids) to numbers."
                )
            _set_quantities(db, ctx, menu_id, quantities)
            done.append(f"set quantities on {menu.name}")
    return done


def _replace(html: str, edits: list[dict]) -> str:
    for edit in edits:
        find = edit.get("find")
        if not find:
            raise Refused("Each replacement needs the exact text to find.")
        if find not in html:
            raise Refused(
                f"Couldn't find {find[:80]!r} in the proposal — read it with include_text first."
            )
        html = html.replace(
            find, str(edit.get("replace") or ""), -1 if _bool(edit.get("all")) else 1
        )
    return html


@_tool("build_proposal", acts=True)
def build_proposal(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """Options, food and the free-HTML proposal for one enquiry — never sends."""
    enquiry = enquiries_svc.get_enquiry(
        db, ctx, _required(p, "enquiry_id", "enquiry_id")
    )
    options = _objects(p.get("options"), "options")
    menus = _objects(p.get("menus"), "menus")
    edits = _objects(p.get("replacements"), "replacements")
    brand_venue = (
        _venue(db, ctx, p["brand_venue"]).id if _text(p.get("brand_venue")) else None
    )
    done: list[str] = []
    with _writing(db):
        done += _apply_options(db, ctx, enquiry, options)
        done += _apply_menus(db, ctx, enquiry, menus)
        proposal = proposals_svc.get_proposal(db, ctx, enquiry.id)
        if proposal is None or _bool(p.get("restart")):
            # Started AFTER the options and menus, so the brand template's
            # blocks are filled from them; from then on the HTML is free and
            # doesn't follow later option changes (restart rebuilds it).
            proposal = proposals_svc.create_proposal(
                db,
                ctx,
                enquiry.id,
                venue_id=brand_venue,
                restart=_bool(p.get("restart")),
            )
            done.append(
                "restarted the proposal from the brand"
                if _bool(p.get("restart"))
                else "started the proposal from the venue brand"
            )
        html = p.get("html") if _text(p.get("html")) else None
        css = p.get("css") if "css" in p and p.get("css") is not None else None
        if edits:
            html = _replace(html if html is not None else proposal.html or "", edits)
        if html is not None or css is not None:
            proposal = proposals_svc.update_proposal(
                db, ctx, enquiry.id, html=html, css=css
            )
            done.append("saved the document (sanitised)")
    money = _Money(db, ctx)
    check = proposals_svc.check_consistency(db, ctx, enquiry.id)
    out = {
        "enquiry_id": enquiry.id,
        "link": _enquiry_link(enquiry.id),
        "done": done or ["nothing changed"],
        "proposal": _compact(proposals_svc.summary(db, ctx, enquiry.id)),
        "options": _options(db, ctx, enquiry.id, money),
        "menus": [
            _menu(m, items, money, with_items=True)
            for m, items in menus_svc.list_menus(db, ctx, enquiry_id=enquiry.id)
        ],
        **money.pair("food", reports.enquiry_food(db, ctx, enquiry.id) or None),
        "check": {
            "ok": check["ok"],
            "warnings": check["warnings"],
            "figures_in_document": [money(c) for c in check["stated_cents"]],
        },
        "money": money.note(),
        "next": (
            "Nothing is sent. Draft the covering email with draft_email (kind proposal, "
            "{{quote_link}} where the link goes); the person sends it, or approves "
            "send_proposal on a card."
        ),
    }
    if _bool(p.get("include_text")):
        out["text"] = proposals_svc.html_text(proposal.html)[:6000]
    if _bool(p.get("include_html")):
        out["html"] = (proposal.html or "")[:20000]
        out["css"] = (proposal.css or "")[:8000]
    return _compact(out)


# ===========================================================================
# The venue
# ===========================================================================


@_tool("get_venue_profile")
def get_venue_profile(p: dict, db: Session, ctx: FunctionsCtx) -> dict:
    """What a venue offers and how it replies, for quoting and drafting."""
    if _text(p.get("venue")):
        venue = _venue(db, ctx, p["venue"])
    else:
        mine = _venues(db, ctx)
        if len(mine) != 1:
            raise Refused("Say which venue: " + ", ".join(v.name for v in mine) + ".")
        venue = mine[0]
    booking_type = _text(p.get("booking_type"))
    if booking_type is not None and booking_type not in BOOKING_TYPES:
        raise Refused(f"booking_type must be one of {', '.join(BOOKING_TYPES)}.")
    stage = _text(p.get("stage"))
    if stage is not None and stage not in REPLY_TEMPLATE_STAGES:
        raise Refused(f"stage must be one of {', '.join(REPLY_TEMPLATE_STAGES)}.")
    money = _Money(db, ctx)
    settings = venue_profile.get_settings(db, ctx, venue.id)
    names = _names(
        db,
        [settings.approver_user_id, settings.override_approver_user_id]
        if settings
        else [],
    )
    areas = [
        a
        for a in venue_profile.list_areas(db, ctx, venue.id, include_inactive=False)
        if not booking_type or not a.booking_types or booking_type in a.booking_types
    ]
    brand = venue_profile.get_brand(db, ctx, venue.id)
    menus = templates_svc.list_templates(db, ctx, venue_id=venue.id)
    menu_items = templates_svc.template_items(db, ctx, [m.id for m in menus])
    replies = templates_svc.list_reply_templates(
        db, ctx, venue_id=venue.id, booking_type=booking_type, stage=stage
    )
    off = (
        sorted(
            k
            for k, on in venue_profile.enabled_fields_for(
                settings, booking_type
            ).items()
            if not on
        )
        if booking_type
        else None
    )
    out = {
        "venue": {"id": venue.id, "name": venue.name, "timezone": venue.timezone},
        "policies": settings.policies if settings else None,
        "approvers": _compact(
            {
                "minimum_spend_approver": names.get(settings.approver_user_id)
                if settings
                else None,
                "override_approver": names.get(settings.override_approver_user_id)
                if settings
                else None,
            }
        ),
        "areas": [
            _compact(
                {
                    "id": a.id,
                    "name": a.name,
                    "seated": a.capacity_seated,
                    "standing": a.capacity_standing,
                    "suits": a.booking_types or None,
                    "requires_approval": a.requires_approval,
                    "minimum_spend_notes": a.min_spend_notes,
                    "description": a.description,
                }
            )
            for a in areas
        ],
        "menu_links": [
            _compact(
                {
                    "label": m.get("label"),
                    "url": m.get("url"),
                    "kind": m.get("kind"),
                    "when": m.get("description"),
                }
            )
            for m in (settings.menu_links if settings else None) or []
            if isinstance(m, dict) and m.get("active", True) is not False
        ],
        "menus": [
            _compact(
                {
                    "template_id": m.id,
                    "name": m.name,
                    "set_menu": m.is_set_menu or None,
                    **money.pair("per_person", m.price_per_person_cents),
                    "description": m.description,
                    "dishes": [
                        _compact(
                            {
                                "section": i.name if i.kind == "header" else None,
                                "dish": i.name if i.kind != "header" else None,
                                "price": None
                                if m.is_set_menu
                                else money(i.unit_price_cents),
                                "share_pct": i.default_percent
                                if m.is_set_menu
                                else None,
                                "dietaries": list(i.dietaries or []) or None,
                            }
                        )
                        for i in menu_items.get(m.id, [])[:60]
                    ],
                }
            )
            for m in menus
        ],
        "brand": _compact(
            {
                "wordmark": brand.wordmark,
                "tagline": brand.tagline,
                "valid_days": brand.valid_days,
                "inclusions": brand.inclusions,
                "terms": brand.terms,
                "signoff": brand.signoff,
                "has_template": bool(brand.template_html) or None,
            }
        )
        if brand is not None
        else None,
        "reply_templates": [
            _compact(
                {
                    "id": r.id,
                    "name": r.name,
                    "stage": r.stage,
                    "booking_type": r.booking_type or "any",
                    "venue": "this venue" if r.venue_id else "all venues",
                    "subject": r.subject,
                    "body_html": (r.body_html or "")[:3000],
                }
            )
            for r in replies
        ],
        "fields_switched_off": off,
        "money": money.note(),
    }
    date = _date(p.get("date"), "date")
    if date is not None:
        with _config_db() as cdb:
            ref = reports.sales_reference(db, cdb, ctx, venue_id=venue.id, date=date)
        out["sales_reference"] = _compact(
            {
                "date": ref["date"],
                "weekday": ref["weekday"],
                **money.pair("budget", ref["budget_cents"]),
                **money.pair("last_year", ref["last_year_cents"]),
                "last_year_date": ref["last_year_date"],
                "tax": ref["tax"],
                "unavailable": None if ref["available"] else ref["reason"],
            }
        )
    return _compact(out)


#: Every tool this module registers — the norm_functions spec rows and the
#: Functions App's claims must name exactly these (tests pin all three).
ACTIONS = (
    "find_enquiries",
    "get_enquiry",
    "record_enquiry",
    "find_events",
    "get_event",
    "record_event",
    "find_threads",
    "read_thread",
    "file_thread",
    "draft_email",
    "send_email",
    "build_proposal",
    "send_proposal",
    "get_venue_profile",
)
