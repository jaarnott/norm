"""The Loaded Payroll built-ins (connector ``norm_payroll``) — the Finance
member's view of a pay week, over ``app/services/payroll``.

Each tool builds the caller's ``FinanceCtx`` from the conversation's person
(``build_ctx(..., actor_label="Norm")`` — the same organisation, venues and
scopes they have on the Payroll pages), calls the services, and shapes a
small answer. No rule of payroll lives here; a refusal is the service's own
message. Rows and their ``field_schema`` are declared beside the handlers for
``scripts/sync_norm_finance.py`` (F1's registry contract).

* ``get_week`` — a read (``requires: payroll:read``): the snapshot state,
  hygiene from the snapshot, the inputs, payouts, gratuities, pushes and the
  last parity run — small; the pages hold the detail.
* ``record_week`` — a write (``requires: payroll:write``, asks first):
  ``op`` ∈ leave_payout | adjustment | ace_inputs | gratuity_batch | accrual
  with ``fields`` as the matching route takes them; ``preview`` runs the real
  service in a savepoint that is rolled back, so the card shows exactly what
  the write would do. Refused in shadow unless ``fields.rehearsal`` is true,
  and refused in an unattended (task-run) thread.
* ``get_mode`` — engine-only: ``{"mode"}``, and ``{"mode", "error":
  "unattended run"}`` for a task-run thread, so a consolidator or another
  built-in that must refuse unless live can ask.
"""

from __future__ import annotations

import datetime as dt
import json
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.agents.internal_tools import previews, register
from app.db.models import Venue
from app.services.finance_core import modes as modes_svc
from app.services.finance_core import parity as core_parity
from app.services.finance_core.ctx import FinanceCtx, build_ctx
from app.services.finance_core.errors import FinanceError
from app.services.payroll import entities as entities_svc
from app.services.payroll import gratuities as gratuities_svc
from app.services.payroll import hygiene as hygiene_svc
from app.services.payroll import inputs as inputs_svc
from app.services.payroll import leave as leave_svc
from app.services.payroll import snapshot as snapshot_svc
from app.services.payroll import weeks as weeks_svc

CONNECTOR = "norm_payroll"

OFF = "Loaded Payroll isn't switched on for this organisation."
UNATTENDED = (
    "Payroll inputs are recorded when someone asks in chat — a scheduled task "
    "can read a pay week, but not record anything."
)
OPS = ("leave_payout", "adjustment", "ace_inputs", "gratuity_batch", "accrual")


class Refused(Exception):
    """A refusal the model reads."""


# ===========================================================================
# Rows (scripts/sync_norm_finance.py installs these)
# ===========================================================================

TOOL_ROWS: list[dict] = [
    {
        "action": "get_week",
        "method": "GET",
        "description": (
            "One payroll entity's pay week as Norm holds it: the Monday and close "
            "snapshots (shift count, dropped clock-ins, drift since close), what "
            "the snapshot says needs fixing in Loaded, the typed inputs (ACE "
            "totals, adjustments, accrual), leave payouts, gratuity shares, Xero "
            "pushes and the last parity run. Pay figures come from here or the "
            "Payroll pages — never quote one you haven't read."
        ),
        "required_fields": [],
        "optional_fields": ["venue", "venue_id", "week_start"],
        "field_schema": {
            "venue": {
                "type": "string",
                "description": "The payroll entity (venue name, exact).",
            },
            "venue_id": {
                "type": "string",
                "description": "The venue id, instead of the name.",
            },
            "week_start": {
                "type": "string",
                "description": "The pay week's Monday, YYYY-MM-DD (default: the last completed week).",
            },
        },
        "requires": ["payroll:read"],
        "engine_only": False,
        "max_result_chars": 20_000,
    },
    {
        "action": "record_week",
        "method": "POST",
        "description": (
            "Record a payroll input for a pay week: a leave payout decision, a "
            "reconciliation adjustment, the ACE pay-run totals, a gratuity batch "
            "or the accrual. `fields` is the same object the Payroll page sends "
            "(payroll-api.md). Asks the person first; in shadow mode only a "
            "rehearsal row (fields.rehearsal = true) is accepted. Never changes "
            "Loaded or Xero."
        ),
        "required_fields": ["op", "fields"],
        "optional_fields": ["venue", "venue_id", "week_start", "preview"],
        "field_schema": {
            "venue": {
                "type": "string",
                "description": "The payroll entity (venue name, exact).",
            },
            "venue_id": {
                "type": "string",
                "description": "The venue id, instead of the name.",
            },
            "week_start": {
                "type": "string",
                "description": "The pay week's Monday, YYYY-MM-DD.",
            },
            "op": {
                "type": "string",
                "enum": list(OPS),
                "description": "What to record.",
            },
            "fields": {
                "type": "object",
                "description": "The input's fields — the body the matching /api/payroll route takes.",
            },
            "preview": {
                "type": "boolean",
                "description": "true: show what would be recorded without recording it.",
            },
        },
        "requires": ["payroll:write"],
        "engine_only": False,
    },
    {
        "action": "get_mode",
        "method": "GET",
        "description": (
            "Engine-only: Loaded Payroll's mode for the caller's organisation "
            "(off | shadow | live). In an unattended task run the answer carries "
            "error 'unattended run', so a write that asks first refuses."
        ),
        "required_fields": [],
        "optional_fields": [],
        "field_schema": {},
        "requires": [],
        "engine_only": True,
    },
]


# ===========================================================================
# Plumbing
# ===========================================================================


def _person(db: Session, thread_id: str | None):
    """The person acting: the thread's user, else the caller scope's (MCP,
    a page), else nobody."""
    from app.agents.internal_tools import _user_for_thread
    from app.db.models import User
    from app.services import caller_scope

    user = _user_for_thread(thread_id, db)
    org_id = None
    if user is None:
        scope = caller_scope.current()
        if scope is not None and scope.user_id:
            user = db.query(User).filter(User.id == scope.user_id).first()
            org_id = scope.org_id
    return user, org_id


def _ctx(db: Session, thread_id: str | None) -> FinanceCtx:
    user, org_id = _person(db, thread_id)
    if user is None:
        raise Refused(
            "No person is attached to this conversation, so Norm can't open their payroll."
        )
    try:
        ctx = build_ctx(db, user, org_id=org_id, actor_label="Norm")
    except FinanceError as exc:
        raise Refused(exc.message)
    if ctx.mode("payroll") == "off":
        raise Refused(OFF)
    return ctx


def _unattended(db: Session, thread_id: str | None) -> bool:
    """A scheduled task's run (its thread is ``<member>.automated_task``)."""
    from app.db.models import Thread

    if not thread_id:
        return False
    intent = db.query(Thread.intent).filter(Thread.id == thread_id).scalar()
    return bool(intent) and intent.endswith(".automated_task")


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


@contextmanager
def _dry_run(db: Session):
    """Run the real service and keep nothing."""
    savepoint = db.begin_nested()
    try:
        yield
    finally:
        savepoint.rollback()


@contextmanager
def _writing(db: Session):
    with db.begin_nested():
        yield


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "on")


def _fields(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            raise Refused("fields must be a JSON object.")
        if isinstance(parsed, dict):
            return parsed
    raise Refused("fields must be an object.")


def _venue(db: Session, ctx: FinanceCtx, p: dict) -> Venue:
    from app.services import caller_scope

    with caller_scope.use(caller_scope.Scope(ctx.org_id, user_id=ctx.user_id)):
        venue, err = caller_scope.find_venue(
            db, name=_text(p.get("venue")), venue_id=_text(p.get("venue_id"))
        )
    if venue is None:
        raise Refused(err or "Name the payroll entity (venue).")
    if venue.id not in ctx.venue_ids:
        raise Refused("You do not have access to that venue.")
    return venue


def _week_start(p: dict, venue) -> dt.date:
    value = _text(p.get("week_start"))
    if not value:
        return weeks_svc.last_completed_week_start(venue)
    try:
        return weeks_svc.validate_week_start(value)
    except FinanceError as exc:
        raise Refused(exc.message)


# ===========================================================================
# get_mode (engine-only)
# ===========================================================================


@register(CONNECTOR, "get_mode")
def get_mode(params: dict, db: Session, thread_id: str | None) -> dict:
    """Loaded Payroll's mode for the caller's organisation; an unattended
    run is named so a write refuses."""
    from app.services.entitlements import org_id_for_user

    user, org_id = _person(db, thread_id)
    if org_id is None and user is not None:
        org_id = org_id_for_user(user.id, db)
    mode = modes_svc.mode(db, org_id, "payroll") if org_id else "off"
    if _unattended(db, thread_id):
        return {
            "success": False,
            "data": {"mode": mode, "error": "unattended run"},
            "error": "unattended run",
        }
    return {"success": True, "data": {"mode": mode}, "error": None}


# ===========================================================================
# get_week
# ===========================================================================


@_tool("get_week")
def get_week(p: dict, db: Session, ctx: FinanceCtx) -> dict:
    """The week as Norm holds it."""
    ctx.require("payroll:read")  # the row's `requires` says so too; gate twice
    venue = _venue(db, ctx, p)
    week_start = _week_start(p, venue)
    week = weeks_svc.get_or_create(db, ctx, venue.id, week_start)
    current = weeks_svc.current_snapshot(db, week)
    close = weeks_svc.close_snapshot(db, week)
    drift = snapshot_svc.drift(db, week)
    payouts = leave_svc.list_payouts(db, ctx, venue_id=venue.id, week_start=week_start)
    shares = gratuities_svc.payable_by(
        db, ctx, venue.id, week_start, include_rehearsal=True
    )
    runs = [
        r
        for r in core_parity.list_runs(db, ctx, limit=50)
        if r.module in ("payroll", "inputs")
        and r.period == f"{venue.id}:{week_start.isoformat()}"
    ]
    last_run = runs[0] if runs else None
    out = {
        "venue": venue.name,
        "venue_id": venue.id,
        "mode": ctx.mode("payroll"),
        "week_start": week_start.isoformat(),
        "week_ending": weeks_svc.week_ending(week_start).isoformat(),
        "status": weeks_svc.status_of(db, week),
        "closed_at": week.closed_at.isoformat() if week.closed_at else None,
        "snapshot": _snapshot(db, current),
        "close_snapshot": _snapshot(db, close),
        "drift": {
            "added": len(drift["added"]),
            "removed": len(drift["removed"]),
            "changed": len(drift["changed"]),
            "cost_delta_cents": drift["totals"]["delta"]["cost_cents"],
        }
        if drift
        else None,
        "hygiene_from_snapshot": _hygiene(db, current),
        "inputs": {
            "ace_inputs": inputs_svc.ace_inputs_dict(
                inputs_svc.get_ace_inputs(db, ctx, week)
            ),
            "adjustments": [
                {
                    k: v
                    for k, v in inputs_svc.adjustment_dict(a).items()
                    if k
                    in ("id", "label", "amount_cents", "report_target", "rehearsal")
                }
                for a in inputs_svc.list_adjustments(db, ctx, week)
            ],
            "accruals": inputs_svc.accrual_dict(
                db, ctx, week, inputs_svc.get_accrual(db, ctx, week)
            ),
        },
        "payouts": [
            {
                "id": x.id,
                "staff_name": x.staff_name,
                "leave_type": x.leave_type,
                "workflow_kind": x.workflow_kind,
                "requested_cents": x.requested_cents,
                "paid_cents": x.paid_cents,
                "rehearsal": x.rehearsal,
            }
            for x in payouts[:20]
        ],
        "gratuities": [
            {
                "batch_id": b.id,
                "period": b.period,
                "staff_name": a.staff_name,
                "amount_cents": a.amount_cents,
                "rehearsal": b.rehearsal,
            }
            for b, a in shares[:40]
        ],
        "exports": [
            {
                "kind": e.kind,
                "filename": e.filename,
                "line_count": e.line_count,
                "generated_at": e.generated_at.isoformat() if e.generated_at else None,
            }
            for e in weeks_svc.exports_of(db, week)[:8]
        ],
        "pushes": [
            {
                "kind": x.kind,
                "xero_number": x.xero_number,
                "xero_status": x.xero_status,
                "voided": x.voided_at is not None,
            }
            for x in weeks_svc.pushes_of(db, week)
        ],
        "parity": {
            "last_run": last_run.ran_at.isoformat()
            if last_run and last_run.ran_at
            else None,
            "module": last_run.module if last_run else None,
            "unexplained": core_parity.unexplained_count(db, ctx, last_run.id)
            if last_run
            else None,
        },
    }
    db.commit()
    return out


def _snapshot(db, snapshot) -> dict | None:
    if snapshot is None:
        return None
    d = snapshot_svc.snapshot_dict(db, snapshot)
    return {
        "id": d["id"],
        "taken_at": d["taken_at"],
        "source": d["source"],
        "shift_count": d["shift_count"],
        "dropped": {
            "deleted": d["dropped_deleted"],
            "unfinished": d["dropped_unfinished"],
        },
        "totals": d["totals"],
    }


def _hygiene(db, snapshot) -> dict | None:
    if snapshot is None:
        return None
    facts = hygiene_svc.from_facts(db, snapshot)
    return {
        "unapproved": len(facts["unapproved_clockins"]),
        "without_pay_code": facts["staff_without_pay_code"],
        "public_holiday_dates": facts["public_holiday_dates"],
        "salaried_count": facts["salaried_count"],
    }


# ===========================================================================
# record_week
# ===========================================================================


def _record(db: Session, ctx: FinanceCtx, p: dict) -> dict:
    ctx.require("payroll:write")
    op = _text(p.get("op"))
    if op not in OPS:
        raise Refused(f"op is one of {', '.join(OPS)}.")
    fields = _fields(p.get("fields"))
    venue = _venue(
        db,
        ctx,
        {
            **p,
            **{
                k: fields.get(k)
                for k in ("venue", "venue_id")
                if not p.get(k) and fields.get(k)
            },
        },
    )
    week_start = _week_start(
        {"week_start": p.get("week_start") or fields.get("week_start")}, venue
    )
    rehearsal = _bool(fields.get("rehearsal"))
    fields.pop("venue", None)
    fields.pop("venue_id", None)
    fields.pop("week_start", None)
    week = weeks_svc.get_or_create(db, ctx, venue.id, week_start)
    if op == "leave_payout":
        fields.pop("rehearsal", None)
        row = leave_svc.record_payout(
            db,
            ctx,
            venue_id=venue.id,
            week_start=week_start,
            rehearsal=rehearsal,
            **fields,
        )
        return {"op": op, "payout": leave_svc.payout_dict(row, ctx)}
    if op == "adjustment":
        fields.pop("rehearsal", None)
        fields.pop("version", None)
        row = inputs_svc.add_adjustment(db, ctx, week, rehearsal=rehearsal, **fields)
        return {"op": op, "adjustment": inputs_svc.adjustment_dict(row)}
    if op == "ace_inputs":
        fields.pop("rehearsal", None)
        row = inputs_svc.set_ace_inputs(db, ctx, week, rehearsal=rehearsal, **fields)
        return {"op": op, "ace_inputs": inputs_svc.ace_inputs_dict(row)}
    if op == "gratuity_batch":
        fields.pop("rehearsal", None)
        batch = gratuities_svc.create_batch(
            db,
            ctx,
            venue_id=venue.id,
            week_start=week_start,
            rehearsal=rehearsal,
            **fields,
        )
        return {"op": op, "batch": gratuities_svc.batch_dict(db, batch)}
    fields.pop("rehearsal", None)
    row = inputs_svc.set_accrual(db, ctx, week, rehearsal=rehearsal, **fields)
    return {"op": op, "accruals": inputs_svc.accrual_dict(db, ctx, week, row)}


def _card(out: dict, venue: str) -> dict:
    op = out["op"]
    body = out[next(k for k in out if k != "op")]
    skip = {
        "id",
        "week_id",
        "venue_id",
        "created_by",
        "decided_by",
        "decided_at",
        "entered_by",
        "entered_at",
        "source",
        "version",
        "workflow_inputs",
        "allocations",
    }
    changes = [
        {"field": k.replace("_", " "), "after": v}
        for k, v in body.items()
        if k not in skip and v not in (None, "", [], {})
    ]
    return {
        "title": f"Record {op.replace('_', ' ')}",
        "target": f"{venue} — week {body.get('week_start') or ''}".strip(" —"),
        "venue": venue,
        "changes": changes[:40],
        "note": "Recorded in Norm only — nothing changes in Loaded or Xero.",
    }


@previews(CONNECTOR, "record_week")
def _record_week_preview(params: dict, db: Session, thread_id: str | None) -> dict:
    try:
        if _unattended(db, thread_id):
            raise Refused(UNATTENDED)
        ctx = _ctx(db, thread_id)
        p = dict(params or {})
        with _dry_run(db):
            out = _record(db, ctx, p)
            venue = (
                out.get("payout", out.get("batch", {})).get("venue_id")
                if isinstance(out, dict)
                else None
            )
            name = (
                db.query(Venue.name).filter(Venue.id == venue).scalar()
                if venue
                else _text(p.get("venue"))
            )
            return {"preview": _card(out, name or "")}
    except FinanceError as exc:
        return {"error": exc.message}
    except Refused as exc:
        return {"error": str(exc)}


@_tool("record_week", acts=True)
def record_week(p: dict, db: Session, ctx: FinanceCtx) -> dict:
    """Record one payroll input; ``preview`` keeps nothing."""
    if _bool(p.get("preview")):
        with _dry_run(db):
            out = _record(db, ctx, p)
        return {**out, "preview": True}
    with _writing(db):
        out = _record(db, ctx, p)
    return out


def refresh_entities(db: Session, ctx: FinanceCtx) -> None:
    """Make every visible venue's entity row (the pages do this on load)."""
    entities_svc.list_entities(db, ctx)
