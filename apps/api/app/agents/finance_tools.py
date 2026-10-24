"""The Budget App's built-ins — ``norm_finance.*`` (plan §12, DESIGN-CONTRACT §4).

Thin layers over ``app/services/finance``: each tool builds the caller's
``FinanceCtx`` from the conversation's person (or, over MCP, the scope's
user), calls the services and shapes a compact answer. No rule lives here.

* ``get_budget_plan`` (read) — the locked budget and prorated lines for a
  week; what ``loadedhub.get_weekly_financials`` calls per venue.
* ``get_forecast`` (read) — the forecast grid for a range, and with
  ``explain`` one day's components and a templated "why".
* ``record_budget`` (write, ask) — ``op`` ∈ lines / days / driver /
  override, previewed as a card of before → after; refused in a scheduled
  run (Functions' rule: Norm acts when someone asks).
* ``get_mode`` (engine-only) — the organisation's ``finance_mode``; for a
  task-run thread it carries ``"error": "unattended run"`` so the Loaded
  push consolidators refuse (they check for any ``error``).
* ``record_budget_push`` (engine-only) — the ledger the push consolidators
  call after writing. ``source`` is derived here from which row ran and
  whether a thread is present, never from a parameter; ``pushed_by`` is the
  caller scope's user.

Setting Loaded's budget is NOT a tool here: that is ``loadedhub.set_budgets``
(a card every time) and its engine-only twin for the day grid (D6).
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.agents.internal_tools import previews, register
from app.db.finance_models import BUDGET_LINE_ITEMS, DRIVER_KINDS
from app.db.models import User, Venue
from app.services.entitlements import org_id_for_user
from app.services.finance import budgets, drivers as drivers_svc
from app.services.finance import forecast as engine
from app.services.finance import pushes, scoring, summary
from app.services.finance_core import modes as modes_svc
from app.services.finance_core.ctx import FinanceCtx, build_ctx
from app.services.finance_core.errors import FinanceError

logger = logging.getLogger(__name__)

CONNECTOR = "norm_finance"

OFF = "Budget isn't switched on for this organisation."
UNATTENDED = (
    "Norm changes budgets only when someone asks in chat — a scheduled "
    "task can read the budget and the forecast, but not change them."
)
#: Rows a forecast read returns unless asked for a range.
DEFAULT_AHEAD_DAYS = 28
MAX_RANGE_DAYS = 120


class Refused(Exception):
    """A refusal the model reads."""


# ===========================================================================
# Plumbing
# ===========================================================================


def unattended(db: Session, thread_id: str | None) -> bool:
    """A scheduled task's run — its thread is ``<member>.automated_task`` or
    has an AutomatedTaskRun row — as opposed to a person in chat."""
    from app.db.models import AutomatedTaskRun, Thread

    if not thread_id:
        return False
    intent = db.query(Thread.intent).filter(Thread.id == thread_id).scalar()
    if intent and str(intent).endswith(".automated_task"):
        return True
    return (
        db.query(AutomatedTaskRun.id)
        .filter(AutomatedTaskRun.thread_id == thread_id)
        .first()
        is not None
    )


def _person(
    db: Session, thread_id: str | None
) -> tuple[User | None, str | None, frozenset | None]:
    """The person acting: the thread's user, else the caller scope's."""
    from app.agents.internal_tools import _user_for_thread
    from app.services import caller_scope

    user = _user_for_thread(thread_id, db)
    org_id = consented = None
    if user is None:
        scope = caller_scope.current()
        if scope is not None and scope.user_id:
            user = db.query(User).filter(User.id == scope.user_id).first()
            org_id, consented = scope.org_id, scope.venue_ids
    return user, org_id, consented


def _ctx(db: Session, thread_id: str | None) -> FinanceCtx:
    import dataclasses

    user, org_id, consented = _person(db, thread_id)
    if user is None:
        raise Refused(
            "No person is attached to this conversation, so Norm can't open their Finance pages."
        )
    org_id = org_id or org_id_for_user(user.id, db)
    if not org_id:
        raise Refused("You are not a member of an organisation using Budget.")
    if modes_svc.mode(db, org_id, "finance") == "off":
        raise Refused(OFF)
    ctx = build_ctx(db, user, org_id=org_id, actor_label="Norm")
    if consented is not None:
        ctx = dataclasses.replace(ctx, venue_ids=ctx.venue_ids & consented)
    return ctx


def _tool(action: str, *, acts: bool = False):
    def decorator(fn):
        def handler(params: dict, db: Session, thread_id: str | None) -> dict:
            try:
                if acts and unattended(db, thread_id):
                    raise Refused(UNATTENDED)
                ctx = _ctx(db, thread_id)
                data = fn(dict(params or {}), db, ctx, thread_id)
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


def _preview(action: str):
    def decorator(fn):
        def previewer(params: dict, db: Session, thread_id: str | None) -> dict:
            try:
                if unattended(db, thread_id):
                    raise Refused(UNATTENDED)
                ctx = _ctx(db, thread_id)
                return fn(dict(params or {}), db, ctx)
            except FinanceError as exc:
                return {"error": exc.message}
            except Refused as exc:
                return {"error": str(exc)}

        previewer.__name__ = fn.__name__
        previews(CONNECTOR, action)(previewer)
        return previewer

    return decorator


def _venue(db: Session, ctx: FinanceCtx, params: dict) -> Venue:
    from app.services.caller_scope import venue_key

    venue_id = params.get("venue_id")
    name = params.get("venue") or params.get("venue_name")
    q = db.query(Venue).filter(Venue.organization_id == ctx.org_id)
    row = None
    if venue_id:
        row = q.filter(Venue.id == str(venue_id)).first()
    elif name:
        key = venue_key(str(name))
        hits = [v for v in q.all() if venue_key(v.name) == key]
        row = hits[0] if len(hits) == 1 else None
    else:
        mine = [v for v in q.all() if v.id in ctx.venue_ids]
        if len(mine) == 1:
            row = mine[0]
        else:
            raise Refused("Say which venue — you have access to more than one.")
    if row is None or row.id not in ctx.venue_ids:
        raise Refused(f"No venue called {name or venue_id!r} that you can open.")
    return row


def _date(value, what: str) -> dt.date:
    return engine.parse_date(value, what)


def _int(value, what: str) -> int:
    if isinstance(value, bool):
        raise Refused(f"{what} must be a whole number.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise Refused(f"{what} must be a whole number.") from exc


def _cents(params: dict, key: str, what: str) -> int | None:
    """``<key>_cents`` or ``<key>`` in dollars — a model sends either."""
    if params.get(f"{key}_cents") not in (None, ""):
        return _int(params[f"{key}_cents"], f"{key}_cents")
    if params.get(key) not in (None, ""):
        try:
            return int(round(float(params[key]) * 100))
        except (TypeError, ValueError) as exc:
            raise Refused(f"{what} must be an amount.") from exc
    return None


def _money(cents: int | None) -> str:
    return "—" if cents is None else f"${cents / 100:,.2f}"


def _rows(
    db: Session, ctx: FinanceCtx, venue: Venue, first: dt.date, last: dt.date
) -> list[dict]:
    """The grid rows the forecast tool and the page share."""
    from app.services.finance import actuals as actuals_svc, holidays

    vintage = engine.latest_vintage(db, ctx.org_id, venue.id)
    days = engine.vintage_days(db, vintage.id, first, last) if vintage else {}
    actual = actuals_svc.series(db, ctx.org_id, venue.id, first, last)
    locked = budgets.locked_cents(db, ctx.org_id, venue.id, first, last)
    loaded = pushes.loaded_as_at(db, ctx.org_id, venue.id, first, last)
    overrides = drivers_svc.overrides_by_date(db, ctx.org_id, venue.id, first, last)
    by_date = drivers_svc.drivers_by_date(db, ctx.org_id, venue.id, first, last)
    hols = holidays.for_venue(db, ctx.org_id, venue.id, first, last)
    out = []
    cursor = first
    while cursor <= last:
        day = days.get(cursor)
        override = overrides.get(cursor)
        forecast = day.forecast_cents if day else None
        final = override.amount_cents if override else forecast
        ld = loaded.get(cursor)
        out.append(
            {
                "date": cursor.isoformat(),
                "weekday": cursor.isoweekday(),
                "actual_cents": actual.get(cursor),
                "forecast_cents": forecast,
                "baseline_cents": day.baseline_cents if day else None,
                "uplift_cents": day.uplift_cents if day else None,
                "drivers": [
                    {"kind": u["kind"], "label": u["label"], "cents": u["cents"]}
                    for u in ((day.components or {}).get("uplifts") if day else [])
                    or []
                ]
                + [
                    {
                        "kind": d.kind,
                        "label": d.label,
                        "cents": d.uplift_cents,
                        "bp": d.uplift_bp,
                        "id": d.id,
                    }
                    for d in by_date.get(cursor, [])
                    if not day
                ],
                "loaded_cents": ld,
                "locked_cents": locked.get(cursor),
                "override_cents": override.amount_cents if override else None,
                "final_cents": final,
                "delta_cents": (final - ld)
                if final is not None and ld is not None
                else None,
                "holiday": hols.get(cursor),
                "flags": list(
                    ((day.components or {}).get("flags") if day else []) or []
                ),
            }
        )
        cursor += dt.timedelta(days=1)
    return out


# ===========================================================================
# Reads
# ===========================================================================


@_tool("get_budget_plan")
def get_budget_plan(params: dict, db: Session, ctx: FinanceCtx, thread_id) -> dict:
    """The locked budget and prorated monthly lines for a week and its
    month-to-date — the Weekly Summary's budget side."""
    venue = _venue(db, ctx, params)
    week_ending = params.get("week_ending") or dt.date.today().isoformat()
    return summary.budget_plan(db, ctx, venue.id, week_ending)


@_tool("get_forecast")
def get_forecast(params: dict, db: Session, ctx: FinanceCtx, thread_id) -> dict:
    """The daily forecast for a range (default: the next four weeks) with
    the actual, Loaded's budget, the locked budget and any override; with
    ``explain`` one day's components and why."""
    venue = _venue(db, ctx, params)
    today = dt.date.today()
    explain = params.get("explain")
    if explain:
        day = _date(explain, "explain")
        first = last = day
    else:
        first = _date(params["start"], "start") if params.get("start") else today
        last = (
            _date(params["end"], "end")
            if params.get("end")
            else first + dt.timedelta(days=DEFAULT_AHEAD_DAYS - 1)
        )
    if last < first:
        first, last = last, first
    if (last - first).days >= MAX_RANGE_DAYS:
        raise Refused(f"Ask for {MAX_RANGE_DAYS} days or fewer at a time.")
    vintage = engine.latest_vintage(db, ctx.org_id, venue.id)
    rows = _rows(db, ctx, venue, first, last)
    ready, weeks = scoring.push_ready(db, ctx.org_id, venue.id)
    out = {
        "venue": venue.name,
        "venue_id": venue.id,
        "from": first.isoformat(),
        "to": last.isoformat(),
        "vintage": engine.vintage_dict(vintage),
        "forecast_push_ready": ready,
        "weeks_of_scores": weeks,
        "days": rows,
        "totals": {
            k: sum(r[k] for r in rows if r[k] is not None)
            for k in (
                "actual_cents",
                "forecast_cents",
                "final_cents",
                "loaded_cents",
                "locked_cents",
            )
        },
        "notes": [
            "cents, GST included (Loaded's basis); final = the override if any, else the forecast",
            "Loaded's budget is as last logged by the nightly job — read loadedhub.get_budgets for the live figure",
        ],
    }
    if vintage is None:
        out["notes"].append(
            "no forecast vintage yet for this venue — the nightly job has not run"
        )
    if explain:
        day = (
            engine.vintage_days(db, vintage.id, first, first).get(first)
            if vintage
            else None
        )
        if day is None:
            out["explain"] = {
                "date": first.isoformat(),
                "error": "no forecast stored for that day",
            }
        else:
            comps = day.components or {}
            out["explain"] = {
                "date": first.isoformat(),
                "forecast_cents": day.forecast_cents,
                "components": comps,
                "why": engine.why_text(comps, first.strftime("%A")),
            }
    return out


# ===========================================================================
# record_budget — lines / days / driver / override
# ===========================================================================


def _lines_change(params: dict, db, ctx, venue) -> tuple[dict, list[dict], str]:
    fiscal_year = _int(
        params.get("fiscal_year")
        or budgets.fiscal_year_for(db, ctx.org_id, dt.date.today()),
        "fiscal_year",
    )
    month = _int(params.get("month"), "month")
    lines = params.get("lines")
    if isinstance(lines, str):
        import json

        try:
            lines = json.loads(lines)
        except ValueError as exc:
            raise Refused("lines must be an object of line items.") from exc
    if not isinstance(lines, dict) or not lines:
        raise Refused(
            f"lines is an object keyed by line item ({', '.join(BUDGET_LINE_ITEMS)})."
        )
    current = {
        (r.month, r.line_item): r
        for r in budgets.lines_for_year(db, ctx, venue.id, fiscal_year)
    }
    changes = []
    for item, value in lines.items():
        before = current.get((month, item))
        before_text = (
            _money(before.value_cents)
            if before and before.value_cents is not None
            else f"{float(before.value_ratio) * 100:.2f}%"
            if before and before.value_ratio is not None
            else "—"
        )
        if value is None:
            after_text = "— (removed)"
        elif isinstance(value, dict) and value.get("value_cents") is not None:
            after_text = _money(_int(value["value_cents"], item))
        elif isinstance(value, dict) and value.get("value_ratio") is not None:
            after_text = f"{float(value['value_ratio']) * 100:.2f}%"
        else:
            raise Refused(f"{item}: give {{value_cents}} or {{value_ratio}}.")
        changes.append(
            {
                "field": f"{item} · month {month}",
                "before": before_text,
                "after": after_text,
            }
        )
    return (
        {"fiscal_year": fiscal_year, "month": month, "lines": lines},
        changes,
        f"{venue.name} — FY{fiscal_year}",
    )


def _days_change(params: dict, db, ctx, venue) -> tuple[dict, list[dict], str]:
    days = params.get("days")
    if isinstance(days, str):
        import json

        try:
            days = json.loads(days)
        except ValueError as exc:
            raise Refused("days must be a list of {date, amount_cents, note}.") from exc
    if not isinstance(days, list) or not days:
        raise Refused("days must be a non-empty list of {date, amount_cents, note}.")
    clean = []
    for d in days:
        if not isinstance(d, dict):
            raise Refused("Each day is {date, amount_cents, note}.")
        cents = _cents(d, "amount", "amount")
        if cents is None:
            raise Refused(f"{d.get('date')}: give amount_cents.")
        clean.append(
            {
                "date": _date(d.get("date"), "date").isoformat(),
                "amount_cents": cents,
                "note": d.get("note"),
            }
        )
    version_id = params.get("version_id")
    version = budgets.get_version(db, ctx, str(version_id)) if version_id else None
    if version is not None and version.venue_id != venue.id:
        raise Refused("That budget version belongs to another venue.")
    existing = (
        budgets.days_of_version(
            db,
            version.id,
            min(_date(d["date"], "date") for d in clean),
            max(_date(d["date"], "date") for d in clean),
        )
        if version
        else {}
    )
    changes = []
    for d in clean:
        before = existing.get(_date(d["date"], "date"))
        changes.append(
            {
                "field": d["date"],
                "before": _money(before.amount_cents) if before else "—",
                "after": _money(d["amount_cents"]),
            }
        )
    target = (
        f"{venue.name} — {version.label or 'version ' + version.id[:8]}"
        if version
        else f"{venue.name} — a new unlocked version"
    )
    return (
        {"version_id": version_id, "label": params.get("label"), "days": clean},
        changes,
        target,
    )


def _driver_change(params: dict, db, ctx, venue) -> tuple[dict, list[dict], str]:
    date = _date(params.get("date"), "date")
    kind = str(params.get("kind") or "manual")
    if kind not in DRIVER_KINDS:
        raise Refused(f"kind is one of {', '.join(DRIVER_KINDS)}.")
    label = str(params.get("label") or "").strip()
    if not label:
        raise Refused("A driver needs a label.")
    cents = _cents(params, "uplift", "uplift")
    bp = None
    if params.get("uplift_pct") not in (None, ""):
        bp = int(round(float(params["uplift_pct"]) * 100))
    elif params.get("uplift_bp") not in (None, ""):
        bp = _int(params["uplift_bp"], "uplift_bp")
    if (cents is None) == (bp is None):
        raise Refused(
            "Give exactly one of uplift_cents (an amount) or uplift_pct (a percentage)."
        )
    after = _money(cents) if cents is not None else f"{bp / 100:+.1f}%"
    return (
        {
            "date": date.isoformat(),
            "kind": kind,
            "label": label,
            "uplift_cents": cents,
            "uplift_bp": bp,
        },
        [{"field": f"{date.isoformat()} · {label}", "before": "—", "after": after}],
        f"{venue.name} — {date.strftime('%a %d %b %Y')}",
    )


def _override_change(params: dict, db, ctx, venue) -> tuple[dict, list[dict], str]:
    date = _date(params.get("date"), "date")
    current = drivers_svc.overrides_by_date(db, ctx.org_id, venue.id, date, date).get(
        date
    )
    before = _money(current.amount_cents) if current else "—"
    clear = str(params.get("clear") or "").lower() in ("true", "1", "yes")
    if clear:
        return (
            {"date": date.isoformat(), "clear": True},
            [{"field": date.isoformat(), "before": before, "after": "— (cleared)"}],
            f"{venue.name} — {date.strftime('%a %d %b %Y')}",
        )
    cents = _cents(params, "amount", "amount")
    if cents is None:
        raise Refused("Give amount_cents (or clear: true).")
    return (
        {
            "date": date.isoformat(),
            "amount_cents": cents,
            "reason": params.get("reason"),
        },
        [{"field": date.isoformat(), "before": before, "after": _money(cents)}],
        f"{venue.name} — {date.strftime('%a %d %b %Y')}",
    )


_OPS = {
    "lines": (_lines_change, "Set monthly budget lines"),
    "days": (_days_change, "Set daily budget"),
    "driver": (_driver_change, "Add a forecast driver"),
    "override": (_override_change, "Override the forecast"),
}


def _plan(params: dict, db: Session, ctx: FinanceCtx):
    op = str(params.get("op") or "").strip()
    if op not in _OPS:
        raise Refused(f"op is one of {', '.join(_OPS)}.")
    ctx.require("budgets:write")
    venue = _venue(db, ctx, params)
    build, title = _OPS[op]
    clean, changes, target = build(params, db, ctx, venue)
    return op, venue, clean, changes, title, target


@_preview("record_budget")
def preview_record_budget(params: dict, db: Session, ctx: FinanceCtx) -> dict:
    op, venue, clean, changes, title, target = _plan(params, db, ctx)
    return {
        "preview": {
            "title": title,
            "target": target,
            "venue": venue.name,
            "changes": changes,
        },
        "basis": {"op": op, "venue_id": venue.id, **clean},
    }


@_tool("record_budget", acts=True)
def record_budget(params: dict, db: Session, ctx: FinanceCtx, thread_id) -> dict:
    """Change the Norm budget or the forecast's inputs: ``op`` lines (a
    month's line items), days (a version's daily amounts), driver (a
    labelled uplift) or override (a day's number)."""
    op, venue, clean, changes, title, target = _plan(params, db, ctx)
    with db.begin_nested():
        if op == "lines":
            rows = budgets.set_lines(
                db, ctx, venue.id, clean["fiscal_year"], clean["month"], clean["lines"]
            )
            data = {
                "fiscal_year": clean["fiscal_year"],
                "month": clean["month"],
                "lines": {
                    r.line_item: {
                        "value_cents": r.value_cents,
                        "value_ratio": float(r.value_ratio)
                        if r.value_ratio is not None
                        else None,
                    }
                    for r in rows
                },
            }
        elif op == "days":
            version_id = clean.get("version_id")
            if not version_id:
                fy = budgets.fiscal_year_for(
                    db, ctx.org_id, _date(clean["days"][0]["date"], "date")
                )
                version = budgets.create_version(
                    db,
                    ctx,
                    venue.id,
                    fy,
                    source="manual",
                    label=clean.get("label") or f"Norm {dt.date.today().isoformat()}",
                )
                version_id = version.id
            n = budgets.set_days(db, ctx, str(version_id), clean["days"])
            data = {
                "version_id": version_id,
                "saved": n,
                "note": "an unlocked version — lock it under Versions on the Budgets page to make it the current budget",
            }
        elif op == "driver":
            row = drivers_svc.add_driver(
                db,
                ctx,
                venue.id,
                clean["date"],
                kind=clean["kind"],
                label=clean["label"],
                uplift_cents=clean["uplift_cents"],
                uplift_bp=clean["uplift_bp"],
            )
            data = drivers_svc.driver_dict(row)
        else:
            if clean.get("clear"):
                data = {
                    "date": clean["date"],
                    "cleared": drivers_svc.clear_override(
                        db, ctx, venue.id, clean["date"]
                    ),
                }
            else:
                row = drivers_svc.set_override(
                    db,
                    ctx,
                    venue.id,
                    clean["date"],
                    clean["amount_cents"],
                    reason=clean.get("reason"),
                )
                data = drivers_svc.override_dict(row)
    return {
        "op": op,
        "venue": venue.name,
        "venue_id": venue.id,
        "changes": len(changes),
        **data,
    }


# ===========================================================================
# Engine-only
# ===========================================================================


@register(CONNECTOR, "get_mode")
def _get_mode(params: dict, db: Session, thread_id: str | None) -> dict:  # noqa: ARG001
    """``{"mode": off|shadow|live}`` for the caller's organisation; a
    task-run thread gets ``"error": "unattended run"`` as well, so the budget
    push consolidators refuse (they refuse on any ``error``)."""
    from app.services import caller_scope

    user, org_id, _ = _person(db, thread_id)
    scope = caller_scope.current()
    org_id = (
        org_id
        or (scope.org_id if scope else None)
        or (org_id_for_user(user.id, db) if user else None)
    )
    if not org_id:
        return {
            "success": True,
            "data": {"mode": "off", "error": "no organisation is known for this call"},
        }
    data = {"mode": modes_svc.mode(db, org_id, "finance")}
    if unattended(db, thread_id):
        data["error"] = "unattended run"
    return {"success": True, "data": data}


@register(CONNECTOR, "record_budget_push")
def _record_budget_push(params: dict, db: Session, thread_id: str | None) -> dict:
    """The ledger write after a Loaded budget push (``fin_budget_pushes``)."""
    from app.services import caller_scope

    if unattended(db, thread_id):
        return {"success": False, "data": {}, "error": "unattended run"}
    scope = caller_scope.current()
    user, org_id, _ = _person(db, thread_id)
    org_id = (
        org_id
        or (scope.org_id if scope else None)
        or (org_id_for_user(user.id, db) if user else None)
    )
    venue_id = params.get("venue_id")
    name = params.get("venue")
    if scope is not None or thread_id:
        venue, err = caller_scope.find_venue(db, name=name, venue_id=venue_id)
        if venue is None:
            return {"success": False, "data": {}, "error": err or "venue not found"}
    else:
        venue = db.query(Venue).filter(Venue.id == str(venue_id or "")).first()
        if venue is None:
            return {"success": False, "data": {}, "error": "venue_id is required"}
    if org_id and venue.organization_id != org_id:
        # The door's organisation decides; a venue of another organisation is
        # never written, whatever the consolidator was handed.
        return {"success": False, "data": {}, "error": "venue not found"}
    org_id = venue.organization_id or org_id
    if not org_id:
        return {
            "success": False,
            "data": {},
            "error": "no organisation is known for this call",
        }
    tool = str(params.get("tool") or "set_budgets")
    source = pushes.source_for(tool, has_thread=bool(thread_id))
    days = params.get("days")
    if isinstance(days, str):
        import json

        try:
            days = json.loads(days)
        except ValueError:
            days = []
    try:
        rows = pushes.record(
            db,
            org_id,
            venue_id=venue.id,
            source=source,
            pushed_by=(scope.user_id if scope else None) or (user.id if user else None),
            days=days if isinstance(days, list) else [],
            tool=tool,
            vintage_id=params.get("vintage_id") or None,
        )
    except FinanceError as exc:
        return {"success": False, "data": {}, "error": exc.message}
    return {
        "success": True,
        "data": {"success": True, "recorded": len(rows), "source": source},
    }


# ===========================================================================
# The rows scripts/sync_norm_finance.py installs
# ===========================================================================

_VENUE_FIELDS = {
    "venue": {
        "type": "string",
        "description": "The venue's exact name (or give venue_id).",
    },
    "venue_id": {"type": "string", "description": "The venue's id, when known."},
}

TOOL_ROWS: list[dict] = [
    {
        "action": "get_budget_plan",
        "method": "GET",
        "description": (
            "Norm's locked sales budget for a week and its month-to-date at one venue, with the "
            "monthly lines (wages, COGS %, leave, opex, depreciation) prorated by day and the week's "
            "adjusted wages and leave. The Weekly Summary's budget side."
        ),
        "required_fields": [],
        "optional_fields": ["venue", "venue_id", "week_ending"],
        "field_schema": {
            **_VENUE_FIELDS,
            "week_ending": {
                "type": "string",
                "description": "YYYY-MM-DD; any date in the week (snapped to its Sunday). Default: this week.",
            },
        },
        "requires": ["finance:read"],
        "engine_only": False,
        "max_result_chars": 20_000,
    },
    {
        "action": "get_forecast",
        "method": "GET",
        "description": (
            "Norm's daily sales forecast for a venue over a range (default: the next four weeks) with "
            "the actual, Loaded's budget, the locked budget and any override per day — and, with "
            "explain, one day's samples, exclusions, uplifts and why. Cents, GST included."
        ),
        "required_fields": [],
        "optional_fields": ["venue", "venue_id", "start", "end", "explain"],
        "field_schema": {
            **_VENUE_FIELDS,
            "start": {
                "type": "string",
                "description": "YYYY-MM-DD, first day (default: today).",
            },
            "end": {
                "type": "string",
                "description": "YYYY-MM-DD, last day (default: start + 27 days; at most 120 days).",
            },
            "explain": {
                "type": "string",
                "description": "YYYY-MM-DD: explain this one day's forecast.",
            },
        },
        "requires": ["finance:read"],
        "engine_only": False,
        "max_result_chars": 40_000,
    },
    {
        "action": "record_budget",
        "method": "POST",
        "description": (
            "Change Norm's budget or the forecast's inputs at a venue. op=lines sets a month's line "
            "items (fiscal_year, month, lines {wage_pct|wage_cents|cogs_pct|food_cogs_pct|bev_cogs_pct|"
            "leave_cents|opex_cents|depreciation_cents|rebates_pct: {value_cents|value_ratio}}); "
            "op=days sets daily amounts on a budget version (version_id or a new unlocked one, days "
            "[{date, amount_cents, note}]); op=driver adds a labelled uplift to a day (date, kind, "
            "label, uplift_cents or uplift_pct); op=override replaces a day's forecast (date, "
            "amount_cents, reason; clear: true removes it). Never writes Loaded."
        ),
        "required_fields": ["op"],
        "optional_fields": [
            "venue",
            "venue_id",
            "fiscal_year",
            "month",
            "lines",
            "version_id",
            "label",
            "days",
            "date",
            "kind",
            "uplift_cents",
            "uplift_pct",
            "amount_cents",
            "reason",
            "clear",
        ],
        "field_schema": {
            **_VENUE_FIELDS,
            "op": {
                "type": "string",
                "enum": list(_OPS),
                "description": "What to change.",
            },
            "fiscal_year": {
                "type": "integer",
                "description": "lines: the fiscal year (named by the year it ends in).",
            },
            "month": {"type": "integer", "description": "lines: calendar month 1–12."},
            "lines": {
                "type": "object",
                "description": "lines: {line_item: {value_cents} | {value_ratio} | null}.",
            },
            "version_id": {
                "type": "string",
                "description": "days: the budget version to write into (unlocked).",
            },
            "label": {
                "type": "string",
                "description": "days: a label for a new version.",
            },
            "days": {
                "type": "array",
                "items": {"type": "object"},
                "description": "days: [{date, amount_cents, note}].",
            },
            "date": {"type": "string", "description": "driver/override: YYYY-MM-DD."},
            "kind": {
                "type": "string",
                "enum": list(DRIVER_KINDS),
                "description": "driver: what kind of driver.",
            },
            "uplift_cents": {
                "type": "integer",
                "description": "driver: a dollar uplift in cents.",
            },
            "uplift_pct": {
                "type": "number",
                "description": "driver: a percentage of the baseline (10 = +10%).",
            },
            "amount_cents": {
                "type": "integer",
                "description": "override: the day's number in cents.",
            },
            "reason": {"type": "string", "description": "override: why."},
            "clear": {
                "type": "boolean",
                "description": "override: remove the day's override.",
            },
        },
        "requires": ["budgets:write"],
        "engine_only": False,
    },
    {
        "action": "get_mode",
        "method": "GET",
        "description": "Engine-only: the organisation's finance mode; carries an error for an unattended run.",
        "required_fields": [],
        "optional_fields": [],
        "field_schema": {},
        "requires": [],
        "engine_only": True,
        "effect": "read",
    },
    {
        "action": "record_budget_push",
        "method": "POST",
        "description": "Engine-only: the ledger write the Loaded budget push consolidators make after writing.",
        "required_fields": ["tool", "days"],
        "optional_fields": [
            "venue",
            "venue_id",
            "from",
            "to",
            "allow_zero",
            "vintage_id",
        ],
        "field_schema": {
            "tool": {"type": "string"},
            "venue": {"type": "string"},
            "venue_id": {"type": "string"},
            "from": {"type": "string"},
            "to": {"type": "string"},
            "allow_zero": {"type": "boolean"},
            "vintage_id": {"type": "string"},
            "days": {"type": "array", "items": {"type": "object"}},
        },
        "requires": [],
        "engine_only": True,
        "effect": "write",
    },
]
