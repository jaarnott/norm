"""The Loaded Payroll App's entertainer tools — built-ins over
``app/services/entertainers`` (plan §12; DESIGN-CONTRACT §4, F5).

Two tools, both on venue access (no ``requires`` — D15): the person in the
conversation reaches only their own venues through the fail-closed ctx.

* ``norm_payroll.get_entertainers`` (read) — ``view`` ∈ ``performances``
  (the roster: "who's playing at F&G this weekend?", "what's waiting for
  approval?"), ``onboarding`` ("has the new DJ filled in the form?") and
  ``emails`` ("what did we send Sam last week?"). Never a bank account, IRD
  number or date of birth; never a money email's body.
* ``norm_payroll.record_performance`` (write, ask) — ``op`` ∈ ``book``,
  ``approve``, ``did_not_perform``, ``mark_paid``. Mark-paid refuses unless
  the person holds ``payroll:write`` (and the module is live). A scheduled
  task's run is refused: entertainers act when someone asks in chat.

Refusals are the services' own messages; the model reads them as the
tool's error. ``scripts/sync_norm_finance.py`` installs ``TOOL_ROWS``.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from contextlib import contextmanager

from app.agents.internal_tools import previews, register
from app.db.models import Venue
from app.services.entertainers import email_links, emails
from app.services.entertainers import payments as payments_svc
from app.services.entertainers import performances as performances_svc
from app.services.entertainers import performers as performers_svc
from app.services.finance_core import modes as modes_svc
from app.services.finance_core.ctx import FinanceCtx, build_ctx
from app.services.finance_core.errors import FinanceError, Invalid

CONNECTOR = "norm_payroll"

OFF = "Entertainers isn't switched on for this organisation."
UNATTENDED = (
    "Entertainers acts when someone asks in chat — a scheduled task's run can "
    "read the roster, but not book, approve or pay anything."
)
DEFAULT_ROWS = 40


class Refused(Exception):
    pass


def _unattended(db: Session, thread_id: str | None) -> bool:
    from app.db.models import Thread

    if not thread_id:
        return False
    intent = db.query(Thread.intent).filter(Thread.id == thread_id).scalar()
    return bool(intent) and intent.endswith(".automated_task")


def _ctx(db: Session, thread_id: str | None) -> FinanceCtx:
    """The person acting: the conversation's user, or over MCP the token's
    user on the caller scope, limited to the venues they consented to."""
    import dataclasses

    from app.agents.internal_tools import _user_for_thread
    from app.db.models import User
    from app.services import caller_scope
    from app.services.entitlements import org_id_for_user

    user = _user_for_thread(thread_id, db)
    org_id = consented = None
    if user is None:
        scope = caller_scope.current()
        if scope is not None and scope.user_id:
            user = db.query(User).filter(User.id == scope.user_id).first()
            org_id, consented = scope.org_id, scope.venue_ids
    if user is None:
        raise Refused(
            "No person is attached to this conversation, so Norm can't open their entertainers."
        )
    org_id = org_id or org_id_for_user(user.id, db)
    if not org_id or modes_svc.mode(db, org_id, "entertainers") == "off":
        raise Refused(OFF)
    ctx = build_ctx(db, user, org_id=org_id, actor_label="Norm")
    if consented is not None:
        ctx = dataclasses.replace(ctx, venue_ids=ctx.venue_ids & consented)
    return ctx


def _tool(action: str, *, acts: bool = False):
    def decorator(fn):
        def handler(params: dict, db: Session, thread_id: str | None) -> dict:
            try:
                if acts and _unattended(db, thread_id):
                    raise Refused(UNATTENDED)
                ctx = _ctx(db, thread_id)
                data = fn(dict(params or {}), db, ctx)
            except FinanceError as exc:
                return {"success": False, "data": {}, "error": exc.message}
            except Refused as exc:
                return {"success": False, "data": {}, "error": str(exc)}
            return {"success": True, "data": data, "error": None}

        handler.__name__ = fn.__name__
        handler.__doc__ = fn.__doc__
        register(CONNECTOR, action)(handler)
        return handler

    return decorator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _venue(db: Session, ctx: FinanceCtx, value) -> Venue | None:
    """A venue by id or name among the caller's venues — never another
    organisation's, never one the person can't see."""
    if not value:
        return None
    text = str(value).strip()
    rows = (
        db.query(Venue).filter(Venue.id.in_(sorted(ctx.venue_ids))).all()
        if ctx.venue_ids
        else []
    )
    for v in rows:
        if v.id == text:
            return v
    key = text.lower()
    exact = [v for v in rows if v.name.lower() == key]
    if len(exact) == 1:
        return exact[0]
    partial = [v for v in rows if key in v.name.lower()]
    if len(partial) == 1:
        return partial[0]
    raise Invalid(
        f"Which venue is '{text}'? Your venues: {', '.join(sorted(v.name for v in rows)) or 'none'}."
    )


def _performer(db: Session, ctx: FinanceCtx, value):
    from app.db.entertainers_models import EntPerformer

    text = str(value or "").strip()
    if not text:
        raise Invalid("Name the performer.")
    rows = (
        db.query(EntPerformer)
        .filter(
            EntPerformer.organization_id == ctx.org_id, EntPerformer.is_active.is_(True)
        )
        .all()
    )
    for r in rows:
        if r.id == text:
            return r
    key = text.lower()
    exact = [r for r in rows if r.name.lower() == key]
    if len(exact) == 1:
        return exact[0]
    partial = [
        r
        for r in rows
        if key in r.name.lower() or key in (r.trading_name or "").lower()
    ]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise Invalid(f"No active performer called '{text}' on the register.")
    raise Invalid(
        f"'{text}' matches {len(partial)} performers: "
        + ", ".join(sorted(r.name for r in partial)[:8])
        + "."
    )


def _date(value, what: str) -> dt.date | None:
    if value in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise Invalid(f"{what} must be YYYY-MM-DD.") from exc


def _cents(value, what: str) -> int:
    try:
        return int(round(float(str(value).replace("$", "").replace(",", "")) * 100))
    except (TypeError, ValueError) as exc:
        raise Invalid(f"{what} must be a dollar amount.") from exc


def _money(cents: int | None) -> str | None:
    return None if cents is None else f"${cents / 100:,.2f}"


def _performance_row(p: dict) -> dict:
    return {
        "id": p["id"],
        "venue": p["venue_name"],
        "performer": p["performer_name"],
        "lane": p["lane"],
        "date": p["performance_date"],
        "booked": f"{p['start_time']}–{p['end_time']}",
        "actual": f"{p['actual_start_time']}–{p['actual_end_time']}"
        if p["actual_start_time"]
        else None,
        "rate": _money(p["rate_cents"]),
        "agreed": _money(p["agreed_cents"]),
        "approved_amount": _money(p["actual_cents"]),
        "status": p["status"],
        "performer_confirmed": bool(p["performer_confirmed_at"]),
        "mismatch": p["mismatch"],
        "closing_manager": (p["closing_manager"] or {}).get("name"),
        "paid": p["payment_status"] == "paid",
        "link": f"/?page=ent-roster&performance={p['id']}",
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@_tool("get_entertainers")
def get_entertainers(params: dict, db: Session, ctx: FinanceCtx) -> dict:
    """The roster, the onboarding register or the sent emails — at the
    person's venues."""
    view = str(params.get("view") or "performances").strip().lower()
    limit = max(1, min(int(params.get("limit") or DEFAULT_ROWS), 200))
    if view == "performances":
        venue = _venue(db, ctx, params.get("venue"))
        since = _date(params.get("from"), "from") or dt.date.today() - dt.timedelta(
            days=14
        )
        until = _date(params.get("to"), "to") or dt.date.today() + dt.timedelta(days=14)
        performer = (
            _performer(db, ctx, params["performer"])
            if params.get("performer")
            else None
        )
        out = performances_svc.list_roster(
            db,
            ctx,
            venue_id=venue.id if venue else None,
            since=since,
            until=until,
            performer_id=performer.id if performer else None,
        )
        rows = out["performances"]
        wanted = str(params.get("status") or "").strip().lower()
        if wanted:
            rows = [p for p in rows if p["status"] == wanted]
        return {
            "view": "performances",
            "from": since.isoformat(),
            "to": until.isoformat(),
            "venue": venue.name if venue else "all your venues",
            "count": len(rows),
            "performances": [_performance_row(p) for p in rows[:limit]],
            "statuses": [
                "scheduled",
                "awaiting_approvals",
                "awaiting_manager",
                "awaiting_performer",
                "mismatch",
                "ready_for_payment",
                "did_not_perform",
                "paid",
            ],
        }
    if view == "onboarding":
        q = str(params.get("q") or params.get("performer") or "").strip() or None
        rows = performers_svc.list_performers(
            db, ctx, q=q, include_inactive=bool(params.get("include_inactive"))
        )
        return {
            "view": "onboarding",
            "count": len(rows),
            "performers": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "lane": r["lane"],
                    "performer_type": r["performer_type"],
                    "onboarding": r["onboarding_status"],
                    "submitted_at": r["submitted_at"],
                    "accepted_at": r["accepted_at"],
                    "needs_review": r["needs_review"],
                    "review_reason": r["review_reason"],
                    "payable": r["payable"],
                    "payable_reason": r["payable_reason"],
                    "has_declaration": r["has_declaration"],
                    "invite_sent_at": r["invite_sent_at"],
                    "email": r["email"],
                    "phone": r["phone"],
                }
                for r in rows[:limit]
            ],
        }
    if view == "emails":
        performer = (
            _performer(db, ctx, params["performer"])
            if params.get("performer")
            else None
        )
        since = _date(params.get("since"), "since")
        rows, total = email_links.list_links(
            db,
            ctx,
            performer_id=performer.id if performer else None,
            kind=params.get("kind") or None,
            since=since,
            limit=limit,
        )
        return {
            "view": "emails",
            "total": total,
            "emails": [
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "to": r["recipient_email"],
                    "sent_at": r["sent_at"],
                    "status": r["status"],
                    "subject": r["subject"],
                    "performer": r["performer_name"],
                    "venue": r["venue_name"],
                    "opened": r["link_opened_at"],
                }
                for r in rows
            ],
        }
    raise Invalid("view must be performances, onboarding or emails.")


def _record(params: dict, db: Session, ctx: FinanceCtx) -> dict:
    """Book, approve, mark did-not-perform, or mark paid."""
    op = str(params.get("op") or "").strip().lower()
    if op == "book":
        venue = _venue(db, ctx, params.get("venue"))
        if venue is None:
            raise Invalid("Name the venue.")
        performer = _performer(db, ctx, params.get("performer"))
        date = _date(params.get("date"), "date")
        if date is None:
            raise Invalid("date is required (YYYY-MM-DD).")
        perf, _series = performances_svc.book(
            db,
            ctx,
            venue_id=venue.id,
            performer_id=performer.id,
            performance_date=date,
            start_time=params.get("start_time"),
            end_time=params.get("end_time"),
            ends_next_day=str(params.get("ends_next_day") or "").lower()
            in ("true", "1", "yes"),
            rate_cents=_cents(params.get("rate"), "rate"),
            notes=params.get("notes"),
        )
        db.flush()
        return {
            "booked": _performance_row(performances_svc.performance_dict(db, ctx, perf))
        }
    if op == "approve":
        perf = performances_svc.approve(
            db,
            ctx,
            str(params.get("performance_id") or ""),
            actual_start_time=params.get("actual_start_time"),
            actual_end_time=params.get("actual_end_time"),
            actual_ends_next_day=str(params.get("actual_ends_next_day") or "").lower()
            in ("true", "1", "yes"),
            note=params.get("note"),
            version=None,
        )
        return {
            "approved": _performance_row(
                performances_svc.performance_dict(db, ctx, perf)
            )
        }
    if op == "did_not_perform":
        perf = performances_svc.did_not_perform(
            db,
            ctx,
            str(params.get("performance_id") or ""),
            reason=str(params.get("reason") or ""),
            version=None,
        )
        return {
            "did_not_perform": _performance_row(
                performances_svc.performance_dict(db, ctx, perf)
            )
        }
    if op == "mark_paid":
        if not ctx.holds("payroll:write"):
            raise Refused(
                "Marking a performance paid needs the payroll:write permission."
            )
        perf = payments_svc.mark_paid(
            db,
            ctx,
            str(params.get("performance_id") or ""),
            reference=params.get("reference"),
            version=None,
        )
        return {
            "paid": _performance_row(performances_svc.performance_dict(db, ctx, perf))
        }
    raise Invalid("op must be book, approve, did_not_perform or mark_paid.")


@_tool("record_performance", acts=True)
def record_performance(params: dict, db: Session, ctx: FinanceCtx) -> dict:
    """Book, approve, mark did-not-perform, or mark paid."""
    with db.begin_nested():
        return _record(params, db, ctx)


@contextmanager
def _dry_run(db: Session):
    """The real service call inside a savepoint that is rolled back, with
    the emails it would queue collected and dropped — the card shows what
    the write would do and nothing is kept or sent."""
    savepoint = db.begin_nested()
    try:
        with emails.collecting():
            yield
    finally:
        savepoint.rollback()


_OP_TITLES = {
    "book": "Book an entertainer",
    "approve": "Approve a performance",
    "did_not_perform": "Mark a performance as not performed",
    "mark_paid": "Mark a performance paid",
}
_CARD_FIELDS = (
    ("venue", "Venue"),
    ("performer", "Performer"),
    ("date", "Date"),
    ("booked", "Booked"),
    ("actual", "Actual times"),
    ("rate", "Rate"),
    ("agreed", "Agreed"),
    ("approved_amount", "Approved amount"),
    ("status", "Status"),
)


@previews(CONNECTOR, "record_performance")
def _record_performance_preview(
    params: dict, db: Session, thread_id: str | None
) -> dict:
    """What the write would change, per op, read by running it and rolling
    it back — the services' own refusals, their own figures."""
    try:
        if _unattended(db, thread_id):
            raise Refused(UNATTENDED)
        ctx = _ctx(db, thread_id)
        p = dict(params or {})
        op = str(p.get("op") or "").strip().lower()
        before = None
        if op in ("approve", "did_not_perform", "mark_paid") and p.get(
            "performance_id"
        ):
            current = performances_svc.get(db, ctx, str(p["performance_id"]))[
                "performance"
            ]
            before = _performance_row(current)
        with _dry_run(db):
            out = _record(p, db, ctx)
        after = next(iter(out.values()))
    except FinanceError as exc:
        return {"error": exc.message}
    except Refused as exc:
        return {"error": str(exc)}
    changes = []
    for key, label in _CARD_FIELDS:
        new = after.get(key)
        old = (before or {}).get(key)
        if new in (None, "") or (before is not None and new == old):
            continue
        changes.append(
            {
                "field": label,
                "before": old if before is not None else None,
                "after": new,
            }
        )
    if op == "mark_paid":
        changes.append({"field": "Paid", "before": "no", "after": "yes"})
    return {
        "preview": {
            "title": _OP_TITLES.get(op, "Change a performance"),
            "target": f"{after.get('performer')} — {after.get('venue')}, {after.get('date')}",
            "venue": after.get("venue"),
            "changes": changes,
            "note": "Recorded in Norm only — nothing changes in Loaded or Xero."
            + (" The performer is emailed as usual." if op == "approve" else ""),
        },
        "basis": {
            "performance_id": after.get("id"),
            "status": (before or {}).get("status"),
        },
    }


# ---------------------------------------------------------------------------
# The rows scripts/sync_norm_finance.py installs
# ---------------------------------------------------------------------------

TOOL_ROWS: list[dict] = [
    {
        "action": "get_entertainers",
        "method": "GET",
        "description": (
            "Entertainers at the person's venues: view=performances (the roster — "
            "bookings, approvals, confirmations, what's ready to pay; filters venue, "
            "from, to, status, performer), view=onboarding (the register — who has "
            "filled in the setup form, who is accepted, who needs review; filter q), "
            "view=emails (what Norm sent a performer; filters performer, kind, since). "
            "Never bank, IRD or date-of-birth details."
        ),
        "required_fields": [],
        "optional_fields": [
            "view",
            "venue",
            "from",
            "to",
            "status",
            "performer",
            "q",
            "kind",
            "since",
            "limit",
            "include_inactive",
        ],
        "field_schema": {
            "view": {
                "type": "string",
                "enum": ["performances", "onboarding", "emails"],
                "description": "What to list (default performances).",
            },
            "venue": {
                "type": "string",
                "description": "Venue name or id (performances).",
            },
            "from": {
                "type": "string",
                "description": "YYYY-MM-DD (performances; default 14 days ago).",
            },
            "to": {
                "type": "string",
                "description": "YYYY-MM-DD (performances; default 14 days ahead).",
            },
            "status": {
                "type": "string",
                "description": "Keep one derived status: scheduled, awaiting_approvals, awaiting_manager, awaiting_performer, mismatch, ready_for_payment, did_not_perform, paid.",
            },
            "performer": {"type": "string", "description": "Performer name or id."},
            "q": {"type": "string", "description": "Search the register (onboarding)."},
            "kind": {
                "type": "string",
                "description": "An email kind, e.g. ent_invite, ent_confirm_request (emails).",
            },
            "since": {"type": "string", "description": "YYYY-MM-DD (emails)."},
            "limit": {
                "type": "integer",
                "description": "Rows to return (default 40, max 200).",
            },
            "include_inactive": {
                "type": "boolean",
                "description": "Include retired performers (onboarding).",
            },
        },
        "requires": [],
        "engine_only": False,
        "max_result_chars": 40_000,
    },
    {
        "action": "record_performance",
        "method": "POST",
        "description": (
            "Change the entertainer roster at the person's venues: op=book (venue, "
            "performer, date, start_time, end_time, rate per hour), op=approve "
            "(performance_id, actual_start_time, actual_end_time — after the "
            "performance ended; pay is rate × real hours), op=did_not_perform "
            "(performance_id, reason), op=mark_paid (performance_id, reference — "
            "payroll:write only). Asks first."
        ),
        "required_fields": ["op"],
        "optional_fields": [
            "venue",
            "performer",
            "date",
            "start_time",
            "end_time",
            "ends_next_day",
            "rate",
            "notes",
            "performance_id",
            "actual_start_time",
            "actual_end_time",
            "actual_ends_next_day",
            "note",
            "reason",
            "reference",
        ],
        "field_schema": {
            "op": {
                "type": "string",
                "enum": ["book", "approve", "did_not_perform", "mark_paid"],
            },
            "venue": {"type": "string"},
            "performer": {"type": "string", "description": "Performer name or id."},
            "date": {"type": "string", "description": "YYYY-MM-DD."},
            "start_time": {"type": "string", "description": "HH:MM venue-local."},
            "end_time": {"type": "string", "description": "HH:MM venue-local."},
            "ends_next_day": {"type": "boolean"},
            "rate": {
                "type": "string",
                "description": "Hourly rate in dollars, e.g. 120.",
            },
            "notes": {"type": "string"},
            "performance_id": {"type": "string"},
            "actual_start_time": {"type": "string", "description": "HH:MM."},
            "actual_end_time": {"type": "string", "description": "HH:MM."},
            "actual_ends_next_day": {"type": "boolean"},
            "note": {"type": "string"},
            "reason": {
                "type": "string",
                "description": "Why the performance didn't happen.",
            },
            "reference": {
                "type": "string",
                "description": "Payment reference (mark_paid).",
            },
        },
        "requires": [],
        "engine_only": False,
        "max_result_chars": 20_000,
    },
]
