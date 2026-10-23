"""Norm Finance — the staff API (``/api/finance/...``), the four pages' data.

Every endpoint does the same things, so they're written once (the Functions
router's shape):

1. **Who and where** — ``finance_ctx``: the signed-in user's organisation
   (from membership), refused as 404 unless Norm Finance is on for it
   (``finance_mode`` ≠ off), then ``build_ctx`` (their venues, scopes, the
   modes). The router gates on ``require_permission("finance:read")``; the
   services gate again on the ctx, and every venue-scoped call runs
   ``require_venue`` — a person sees only their own venues.
2. **The rule** — a service in ``app/services/finance``. The router shapes
   what the services return and never queries a rule of its own.
3. **Errors** — domain errors become ``{"detail": {"code", "message"}}``
   with the service's status (404 / 403 / 400 / 409 ``conflict`` / 409
   ``not_live``). Bodies refuse unknown fields (422).
4. **One request, one transaction** — writes run in a savepoint and commit
   once.

Writes that reach Loaded never happen here: the push routes wrap
``page_writes.preview`` / ``confirm`` (F0) around the ``loadedhub.set_budgets``
twins, so the person sees the card, the consolidator refuses outside
``live``, and the ledger records who pressed.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permission
from app.db.engine import get_config_db, get_db
from app.db.finance_models import BUDGET_SOURCES, DRIVER_KINDS
from app.db.models import User, Venue
from app.services.entitlements import org_id_for_user
from app.services.finance import actuals as actuals_svc
from app.services.finance import budget_import, budgets
from app.services.finance import drivers as drivers_svc
from app.services.finance import forecast as engine
from app.services.finance import holidays, pushes, scoring, summary
from app.services.finance_core import modes as modes_svc
from app.services.finance_core.ctx import FinanceCtx, build_ctx, require_venue
from app.services.finance_core.errors import (
    FinanceError,
    Invalid,
    NotLive,
    http_exception,
)

router = APIRouter(prefix="/finance", tags=["finance"])


# ===========================================================================
# Plumbing
# ===========================================================================


def finance_ctx(
    user: User = Depends(require_permission("finance:read")),
    db: Session = Depends(get_db),
) -> FinanceCtx:
    """The caller's Finance context — or 404 when their organisation has
    Norm Finance off: the pages don't exist for them, so neither do these."""
    org_id = org_id_for_user(user.id, db)
    if not org_id or modes_svc.mode(db, org_id, "finance") == "off":
        raise HTTPException(
            status_code=404,
            detail={
                "code": "not_found",
                "message": "Norm Finance is not switched on for your organisation.",
            },
        )
    try:
        return build_ctx(db, user, org_id=org_id)
    except FinanceError as exc:
        raise http_exception(exc) from exc


def editor_ctx(
    ctx: FinanceCtx = Depends(finance_ctx),
    _: User = Depends(require_permission("budgets:write")),
) -> FinanceCtx:
    return ctx


@contextmanager
def _reading():
    try:
        yield
    except FinanceError as exc:
        raise http_exception(exc) from exc


@contextmanager
def _writing(db: Session):
    try:
        with db.begin_nested():
            yield
    except FinanceError as exc:
        raise http_exception(exc) from exc
    db.commit()


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _date(value: str, what: str = "date") -> dt.date:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        raise HTTPException(
            422, {"code": "invalid", "message": f"{what} must be YYYY-MM-DD."}
        )


def _venue(db: Session, ctx: FinanceCtx, venue_id: str) -> Venue:
    with _reading():
        require_venue(ctx, venue_id)
    venue = (
        db.query(Venue)
        .filter(Venue.id == venue_id, Venue.organization_id == ctx.org_id)
        .first()
    )
    if venue is None:
        raise HTTPException(404, {"code": "not_found", "message": "Venue not found."})
    return venue


def _loaded_month(
    db, config_db, ctx, venue: Venue, first: dt.date, last: dt.date, warnings: list
) -> dict:
    """Loaded's live budget for a range through ``get_budgets`` on the
    venue's own token — ``{}`` plus a warning when it cannot be read."""
    from app.connectors.tool_executor import execute_connector_tool

    try:
        res = execute_connector_tool(
            connector_name="loadedhub",
            action="get_budgets",
            params={
                "venue": venue.name,
                "venue_id": venue.id,
                "from_date": first.isoformat(),
                "to_date": last.isoformat(),
                "tax": "include",
            },
            db=db,
            config_db=config_db,
            venue_id=venue.id,
            strict_venue=True,
            user=ctx.user,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Loaded's budget could not be read: {exc}"[:300])
        return {}
    data = res.payload if res.success else None
    if not isinstance(data, dict) or data.get("error") or not res.success:
        warnings.append(
            f"Loaded's budget could not be read: {res.error or (data or {}).get('error')}"[
                :300
            ]
        )
        return {}
    out = {}
    for d in data.get("days") or []:
        try:
            out[dt.date.fromisoformat(str(d.get("date"))[:10])] = int(
                round(float(d.get("amount") or 0) * 100)
            )
        except (TypeError, ValueError):
            continue
    return out


def _version_dict(v, stats: dict | None = None) -> dict:
    days, total = (stats or {}).get(v.id, (0, 0))
    return {
        "id": v.id,
        "venue_id": v.venue_id,
        "fiscal_year": v.fiscal_year,
        "label": v.label,
        "source": v.source,
        "locked_at": v.locked_at.isoformat() if v.locked_at else None,
        "locked_by": v.locked_by,
        "created_by": v.created_by,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "notes": v.notes,
        "days": days,
        "total_cents": total,
    }


def _line_dict(r) -> dict:
    return {
        "value_cents": r.value_cents,
        "value_ratio": float(r.value_ratio) if r.value_ratio is not None else None,
        "version": r.version,
    }


# ===========================================================================
# Meta
# ===========================================================================


@router.get("/meta")
def meta(ctx: FinanceCtx = Depends(finance_ctx), db: Session = Depends(get_db)):
    from app.db.models import Organization
    from app.services.business_calendar import day_start_for, timezone_name

    org = db.query(Organization).filter(Organization.id == ctx.org_id).first()
    venues = summary.viewer_venues(db, ctx)
    sm = budgets.start_month(db, ctx.org_id)
    today = dt.date.today()
    fy = budgets.fiscal_year_of(today, sm)
    out_venues = []
    for v in venues:
        settings = budgets.settings_dict(
            budgets.venue_settings(db, ctx.org_id, v.id), v.id
        )
        ready, weeks = scoring.push_ready(db, ctx.org_id, v.id)
        out_venues.append(
            {
                "id": v.id,
                "name": v.name,
                "is_trading": v.is_trading,
                "timezone": timezone_name(v),
                "day_start": day_start_for(v),
                "closed_weekdays": settings["closed_weekdays"],
                "level_correction_on": settings["level_correction_on"],
                "forecast_push_ready": ready,
                "weeks_of_scores": weeks,
                "actuals": actuals_svc.coverage(db, ctx.org_id, v.id),
            }
        )
    return {
        "organization_id": ctx.org_id,
        "finance_mode": ctx.mode("finance"),
        "fiscal_year_start_month": sm,
        "currency": org.currency if org else "NZD",
        "gst_rate": float(summary.gst_rate(db, ctx.org_id)),
        "scopes": sorted(
            s for s in ctx.scopes if s.startswith(("finance:", "budgets:", "payroll:"))
        ),
        "can_see_group": ctx.holds("payroll:read"),
        "venues": out_venues,
        "fiscal_years": [fy - 1, fy, fy + 1],
        "today": today.isoformat(),
    }


# ===========================================================================
# Weekly Summary
# ===========================================================================


@router.get("/summary")
def weekly_summary(
    week_ending: str | None = None,
    venues: str | None = None,
    include_days: bool = True,
    ctx: FinanceCtx = Depends(finance_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    if week_ending:
        _date(week_ending, "week_ending")
    else:
        today = dt.date.today()
        week_ending = (today - dt.timedelta(days=today.weekday() + 1)).isoformat()
    ids = [v for v in (venues or "").split(",") if v.strip()] or None
    with _reading():
        return summary.weekly(
            db, ctx, config_db, week_ending, venue_ids=ids, include_days=include_days
        )


# ===========================================================================
# Budgets — versions, days, lines
# ===========================================================================


@router.get("/budgets/versions")
def list_versions(
    venue_id: str,
    fiscal_year: int | None = None,
    ctx: FinanceCtx = Depends(finance_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        rows = budgets.list_versions(db, ctx, venue_id, fiscal_year)
    stats = budgets.version_stats(db, [r.id for r in rows])
    current = None
    if fiscal_year is not None:
        cur = budgets.current_version(db, ctx.org_id, venue_id, int(fiscal_year))
        current = cur.id if cur else None
    return {
        "venue_id": venue_id,
        "fiscal_year": fiscal_year,
        "current_version_id": current,
        "versions": [_version_dict(r, stats) for r in rows],
    }


class VersionCreate(_Body):
    venue_id: str
    fiscal_year: int
    label: str | None = None
    source: Literal["import", "manual", "forecast"] = "manual"
    notes: str | None = None
    copy_from_version_id: str | None = None


@router.post("/budgets/versions", status_code=201)
def create_version(
    body: VersionCreate,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
):
    if body.source not in BUDGET_SOURCES:
        raise HTTPException(422, {"code": "invalid", "message": "bad source"})
    with _writing(db):
        row = budgets.create_version(
            db,
            ctx,
            body.venue_id,
            body.fiscal_year,
            source=body.source,
            label=body.label,
            notes=body.notes,
            copy_from_version_id=body.copy_from_version_id,
        )
    return _version_dict(row, budgets.version_stats(db, [row.id]))


class VersionPatch(_Body):
    label: str | None = None
    notes: str | None = None


@router.patch("/budgets/versions/{version_id}")
def patch_version(
    version_id: str,
    body: VersionPatch,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        row = budgets.update_version(
            db, ctx, version_id, **body.model_dump(exclude_unset=True)
        )
    return _version_dict(row, budgets.version_stats(db, [row.id]))


@router.post("/budgets/versions/{version_id}/lock")
def lock_version(
    version_id: str,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        row = budgets.lock_version(db, ctx, version_id)
    return _version_dict(row, budgets.version_stats(db, [row.id]))


@router.post("/budgets/versions/{version_id}/unlock")
def unlock_version(
    version_id: str,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        row = budgets.unlock_version(db, ctx, version_id)
    return _version_dict(row, budgets.version_stats(db, [row.id]))


class DaysBody(_Body):
    days: list[dict[str, Any]]


@router.put("/budgets/versions/{version_id}/days")
def put_days(
    version_id: str,
    body: DaysBody,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        n = budgets.set_days(db, ctx, version_id, body.days)
    return {"version_id": version_id, "saved": n}


@router.get("/budgets/days")
def month_days(
    venue_id: str,
    year: int,
    month: int,
    version_id: str | None = None,
    ctx: FinanceCtx = Depends(finance_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    venue = _venue(db, ctx, venue_id)
    with _reading():
        first, last = budgets.month_bounds(year, month)
        sm = budgets.start_month(db, ctx.org_id)
        fy = budgets.fiscal_year_of(first, sm)
        if version_id:
            version = budgets.get_version(db, ctx, version_id)
            if version.venue_id != venue_id:
                raise Invalid("That version belongs to another venue.")
            days = budgets.days_of_version(db, version.id, first, last)
        else:
            version = budgets.current_version(db, ctx.org_id, venue_id, fy)
            days = (
                budgets.days_of_version(db, version.id, first, last) if version else {}
            )
    warnings: list[str] = []
    loaded = _loaded_month(db, config_db, ctx, venue, first, last, warnings)
    actual = actuals_svc.series(db, ctx.org_id, venue_id, first, last)
    hols = holidays.for_venue(db, ctx.org_id, venue_id, first, last)
    out, totals = [], {"amount_cents": 0, "loaded_cents": 0, "actual_cents": 0}
    cursor = first
    while cursor <= last:
        d = days.get(cursor)
        row = {
            "date": cursor.isoformat(),
            "weekday": cursor.isoweekday(),
            "amount_cents": d.amount_cents if d else None,
            "note": d.note if d else None,
            "loaded_cents": loaded.get(cursor),
            "actual_cents": actual.get(cursor),
            "holiday": hols.get(cursor),
        }
        for key, value in (
            ("amount_cents", row["amount_cents"]),
            ("loaded_cents", row["loaded_cents"]),
            ("actual_cents", row["actual_cents"]),
        ):
            if value is not None:
                totals[key] += value
        out.append(row)
        cursor += dt.timedelta(days=1)
    return {
        "venue_id": venue_id,
        "year": year,
        "month": month,
        "fiscal_year": fy,
        "version_id": version.id if version else None,
        "locked": bool(version and version.locked_at),
        "days": out,
        "totals": totals,
        "warnings": warnings,
    }


@router.get("/budgets/lines")
def get_lines(
    venue_id: str,
    fiscal_year: int,
    ctx: FinanceCtx = Depends(finance_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        rows = budgets.lines_for_year(db, ctx, venue_id, fiscal_year)
    months: dict[str, dict] = {}
    for r in rows:
        months.setdefault(str(r.month), {})[r.line_item] = _line_dict(r)
    sm = budgets.start_month(db, ctx.org_id)
    return {
        "venue_id": venue_id,
        "fiscal_year": fiscal_year,
        "month_order": [m for _, m in budgets.fiscal_months(fiscal_year, sm)],
        "months": months,
    }


class LinesBody(_Body):
    venue_id: str
    fiscal_year: int
    month: int
    lines: dict[str, dict[str, Any] | None]


@router.put("/budgets/lines")
def put_lines(
    body: LinesBody,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        rows = budgets.set_lines(
            db, ctx, body.venue_id, body.fiscal_year, body.month, body.lines
        )
    return {
        "venue_id": body.venue_id,
        "fiscal_year": body.fiscal_year,
        "month": body.month,
        "lines": {r.line_item: _line_dict(r) for r in rows},
    }


# ---------------------------------------------------------------- import --


class ImportBody(_Body):
    venue_id: str
    text: str
    layout: Literal["generic", "orbit"] = "generic"
    fiscal_year: int | None = None


class ImportApply(ImportBody):
    label: str | None = None
    lock: bool = False


@router.post("/budgets/import/preview")
def import_preview(
    body: ImportBody,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        return budget_import.preview(
            db,
            ctx,
            body.venue_id,
            body.text,
            layout=body.layout,
            fiscal_year=body.fiscal_year,
        )


@router.post("/budgets/import/apply", status_code=201)
def import_apply(
    body: ImportApply,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        version, shown = budget_import.apply(
            db,
            ctx,
            body.venue_id,
            body.text,
            layout=body.layout,
            fiscal_year=body.fiscal_year,
            label=body.label,
            lock=body.lock,
        )
    return {
        "version": _version_dict(version, budgets.version_stats(db, [version.id])),
        "days": shown["days"],
        "total_cents": shown["total_cents"],
    }


# ---------------------------------------------------------- vs Loaded ----


@router.get("/budgets/vs-loaded")
def vs_loaded(
    venue_id: str,
    year: int | None = None,
    month: int | None = None,
    fiscal_year: int | None = None,
    ctx: FinanceCtx = Depends(finance_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    venue = _venue(db, ctx, venue_id)
    warnings: list[str] = []
    sm = budgets.start_month(db, ctx.org_id)
    if fiscal_year is not None and month is None:
        months_out = []
        totals = {"locked_cents": 0, "loaded_cents": 0, "actual_cents": 0}
        lo, hi = budgets.fiscal_bounds(fiscal_year, sm)
        loaded = _loaded_month(db, config_db, ctx, venue, lo, hi, warnings)
        locked = budgets.locked_cents(db, ctx.org_id, venue_id, lo, hi)
        actual = actuals_svc.series(db, ctx.org_id, venue_id, lo, hi)
        for y, m in budgets.fiscal_months(fiscal_year, sm):
            first, last = budgets.month_bounds(y, m)
            _, t = budgets.vs_rows(
                first, last, locked=locked, loaded=loaded, actual=actual
            )
            months_out.append({"year": y, "month": m, **t})
            for k in totals:
                totals[k] += t[k]
        lk, ld = totals["locked_cents"], totals["loaded_cents"]
        totals["variance_cents"] = ld - lk
        totals["variance_pct"] = round((ld - lk) / lk * 100, 1) if lk else None
        return {
            "venue_id": venue_id,
            "fiscal_year": fiscal_year,
            "months": months_out,
            "totals": totals,
            "warnings": warnings,
        }
    if year is None or month is None:
        raise HTTPException(
            422, {"code": "invalid", "message": "give year and month, or fiscal_year"}
        )
    with _reading():
        first, last = budgets.month_bounds(year, month)
    loaded = _loaded_month(db, config_db, ctx, venue, first, last, warnings)
    locked_days = budgets.locked_days(db, ctx.org_id, venue_id, first, last)
    rows, totals = budgets.vs_rows(
        first,
        last,
        locked={d: r.amount_cents for d, r in locked_days.items()},
        loaded=loaded,
        actual=actuals_svc.series(db, ctx.org_id, venue_id, first, last),
        notes={d: r.note for d, r in locked_days.items()},
    )
    return {
        "venue_id": venue_id,
        "year": year,
        "month": month,
        "days": rows,
        "totals": totals,
        "warnings": warnings,
    }


@router.get("/budgets/sync-month")
def sync_month(
    venue_id: str,
    year: int,
    month: int,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
):
    """The payload "Sync month to Loaded" previews through the page helper
    (``loadedhub.set_budgets_typed``): the locked budget's days as dollars."""
    venue = _venue(db, ctx, venue_id)
    with _reading():
        first, last = budgets.month_bounds(year, month)
    locked = budgets.locked_cents(db, ctx.org_id, venue_id, first, last)
    days = [
        {"date": d.isoformat(), "amount": round(c / 100, 2)}
        for d, c in sorted(locked.items())
    ]
    missing = []
    cursor = first
    while cursor <= last:
        if cursor not in locked:
            missing.append(cursor.isoformat())
        cursor += dt.timedelta(days=1)
    return {
        "tool": "loadedhub.set_budgets_typed",
        "venue_id": venue_id,
        "params": {
            "venue": venue.name,
            "venue_id": venue_id,
            "days": days,
            "allow_zero": False,
        },
        "zero_days": [d["date"] for d in days if d["amount"] == 0],
        "days_without_budget": missing,
    }


# ===========================================================================
# The Loaded push — wrappers over page_writes
# ===========================================================================


class PushBody(_Body):
    venue_id: str
    kind: Literal["typed", "forecast", "restore"] = "typed"
    days: list[dict[str, Any]]
    allow_zero: bool = False
    vintage_id: str | None = None
    fingerprint: str | None = None


def _push_target(db: Session, ctx: FinanceCtx, body: PushBody) -> tuple[str, dict]:
    """Which consolidator row, and its params — after the gates: live mode,
    the venue, and for a forecast push the four weeks of scores."""
    venue = _venue(db, ctx, body.venue_id)
    with _reading():
        ctx.require("budgets:write")
        ctx.require_live("finance")
        if body.kind == "forecast":
            ready, weeks = scoring.push_ready(db, ctx.org_id, venue.id)
            if not ready:
                raise NotLive(
                    f"Setting Loaded's budget from the forecast opens after four weeks of nightly "
                    f"scores at this venue ({weeks} so far)."
                )
    days = []
    for d in body.days:
        if not isinstance(d, dict) or not d.get("date"):
            raise HTTPException(
                422, {"code": "invalid", "message": "days are {date, amount_cents}"}
            )
        cents = d.get("amount_cents")
        if cents is None and d.get("amount") is not None:
            cents = int(round(float(d["amount"]) * 100))
        if cents is None:
            raise HTTPException(
                422,
                {"code": "invalid", "message": f"{d['date']}: amount_cents missing"},
            )
        days.append(
            {"date": _date(d["date"]).isoformat(), "amount": round(int(cents) / 100, 2)}
        )
    tool = (
        "loadedhub.set_budgets_typed"
        if body.kind == "typed"
        else "loadedhub.set_budgets"
    )
    params = {
        "venue": venue.name,
        "venue_id": venue.id,
        "days": days,
        "allow_zero": body.allow_zero,
    }
    if body.vintage_id and body.kind == "forecast":
        params["vintage_id"] = body.vintage_id
    return tool, params


@router.post("/push/preview")
def push_preview(
    body: PushBody,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    from app.services import page_writes

    tool, params = _push_target(db, ctx, body)
    return page_writes.preview(
        db,
        tool=tool,
        params=params,
        user=ctx.user,
        venue_id=body.venue_id,
        config_db=config_db,
    )


@router.post("/push/confirm")
def push_confirm(
    body: PushBody,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    from app.services import page_writes

    tool, params = _push_target(db, ctx, body)
    marker = pushes.restoring() if body.kind == "restore" else _noop()
    with marker:
        result = page_writes.confirm(
            db,
            tool=tool,
            params=params,
            user=ctx.user,
            venue_id=body.venue_id,
            fingerprint=body.fingerprint,
            config_db=config_db,
        )
    db.commit()
    return result


@contextmanager
def _noop():
    yield


# ===========================================================================
# Settings
# ===========================================================================


@router.get("/settings")
def get_settings(
    venue_id: str, ctx: FinanceCtx = Depends(finance_ctx), db: Session = Depends(get_db)
):
    _venue(db, ctx, venue_id)
    return budgets.settings_dict(
        budgets.venue_settings(db, ctx.org_id, venue_id), venue_id
    )


class SettingsBody(_Body):
    venue_id: str
    closed_weekdays: list[int] | None = None
    level_correction_on: bool | None = None
    version: int | None = None


@router.put("/settings")
def put_settings(
    body: SettingsBody,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
):
    given = body.model_dump(exclude_unset=True)
    kwargs = {
        k: given[k] for k in ("closed_weekdays", "level_correction_on") if k in given
    }
    with _writing(db):
        row = budgets.set_venue_settings(
            db, ctx, body.venue_id, version=body.version, **kwargs
        )
    return budgets.settings_dict(row, body.venue_id)


# ===========================================================================
# Forecasting
# ===========================================================================


def _grid_rows(db, ctx, venue: Venue, first: dt.date, last: dt.date) -> list[dict]:
    from app.agents.finance_tools import _rows

    return _rows(db, ctx, venue, first, last)


@router.get("/forecast")
def forecast_month(
    venue_id: str,
    year: int,
    month: int,
    ctx: FinanceCtx = Depends(finance_ctx),
    db: Session = Depends(get_db),
):
    venue = _venue(db, ctx, venue_id)
    with _reading():
        first, last = budgets.month_bounds(year, month)
    rows = _grid_rows(db, ctx, venue, first, last)
    vintage = engine.latest_vintage(db, ctx.org_id, venue.id)
    ready, weeks = scoring.push_ready(db, ctx.org_id, venue.id)
    keys = (
        "actual_cents",
        "forecast_cents",
        "final_cents",
        "loaded_cents",
        "locked_cents",
    )
    weeks_out: dict[str, dict] = {}
    for r in rows:
        d = dt.date.fromisoformat(r["date"])
        ws = (d - dt.timedelta(days=d.weekday())).isoformat()
        w = weeks_out.setdefault(ws, {"week_start": ws, **{k: 0 for k in keys}})
        for k in keys:
            if r[k] is not None:
                w[k] += r[k]
    prev_first = (first - dt.timedelta(days=1)).replace(day=1)
    return {
        "venue_id": venue_id,
        "year": year,
        "month": month,
        "vintage": engine.vintage_dict(vintage),
        "forecast_push_ready": ready,
        "weeks_of_scores": weeks,
        "days": rows,
        "weeks": [weeks_out[k] for k in sorted(weeks_out)],
        "totals": {k: sum(r[k] for r in rows if r[k] is not None) for k in keys},
        "accuracy": scoring.month_strip(
            db, ctx.org_id, venue.id, prev_first.year, prev_first.month
        ),
        "warnings": []
        if vintage
        else ["no forecast vintage yet — the nightly job has not run for this venue"],
    }


@router.get("/forecast/day")
def forecast_day(
    venue_id: str,
    date: str,
    vintage_id: str | None = None,
    ctx: FinanceCtx = Depends(finance_ctx),
    db: Session = Depends(get_db),
):
    venue = _venue(db, ctx, venue_id)
    day = _date(date)
    with _reading():
        vintage = (
            engine.get_vintage(db, ctx.org_id, vintage_id)
            if vintage_id
            else engine.latest_vintage(db, ctx.org_id, venue.id)
        )
    row = engine.vintage_days(db, vintage.id, day, day).get(day) if vintage else None
    grid = _grid_rows(db, ctx, venue, day, day)[0]
    comps = (row.components or {}) if row else {}
    return {
        **grid,
        "vintage": engine.vintage_dict(vintage),
        "components": comps,
        "why": engine.why_text(comps, day.strftime("%A")) if row else None,
        "driver_rows": [
            drivers_svc.driver_dict(d)
            for d in drivers_svc.list_drivers(db, ctx, venue.id, day, day)
        ],
        "override": drivers_svc.override_dict(
            drivers_svc.overrides_by_date(db, ctx.org_id, venue.id, day, day).get(day)
        ),
    }


class DriverBody(_Body):
    venue_id: str
    date: str
    kind: Literal["holiday", "event", "function", "manual"] = "manual"
    label: str
    uplift_cents: int | None = None
    uplift_bp: int | None = None


@router.post("/forecast/drivers", status_code=201)
def add_driver(
    body: DriverBody,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
):
    if body.kind not in DRIVER_KINDS:
        raise HTTPException(422, {"code": "invalid", "message": "bad kind"})
    with _writing(db):
        row = drivers_svc.add_driver(
            db,
            ctx,
            body.venue_id,
            _date(body.date),
            kind=body.kind,
            label=body.label,
            uplift_cents=body.uplift_cents,
            uplift_bp=body.uplift_bp,
        )
    return drivers_svc.driver_dict(row)


@router.get("/forecast/drivers")
def list_drivers(
    venue_id: str,
    start: str,
    end: str,
    include_archived: bool = False,
    ctx: FinanceCtx = Depends(finance_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        rows = drivers_svc.list_drivers(
            db,
            ctx,
            venue_id,
            _date(start),
            _date(end),
            include_archived=include_archived,
        )
    return [drivers_svc.driver_dict(r) for r in rows]


@router.delete("/forecast/drivers/{driver_id}")
def archive_driver(
    driver_id: str, ctx: FinanceCtx = Depends(editor_ctx), db: Session = Depends(get_db)
):
    with _writing(db):
        row = drivers_svc.archive_driver(db, ctx, driver_id)
    return drivers_svc.driver_dict(row)


class OverrideBody(_Body):
    venue_id: str
    date: str
    amount_cents: int
    reason: str | None = None


@router.put("/forecast/override")
def put_override(
    body: OverrideBody,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        row = drivers_svc.set_override(
            db,
            ctx,
            body.venue_id,
            _date(body.date),
            body.amount_cents,
            reason=body.reason,
        )
    return drivers_svc.override_dict(row)


@router.delete("/forecast/override")
def delete_override(
    venue_id: str,
    date: str,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
):
    with _writing(db):
        n = drivers_svc.clear_override(db, ctx, venue_id, _date(date))
    return {"venue_id": venue_id, "date": date, "cleared": n}


class RunBody(_Body):
    venue_id: str
    as_of: str | None = None


@router.post("/forecast/run")
def run_forecast(
    body: RunBody, ctx: FinanceCtx = Depends(editor_ctx), db: Session = Depends(get_db)
):
    venue = _venue(db, ctx, body.venue_id)
    as_of = _date(body.as_of, "as_of") if body.as_of else dt.date.today()
    with _writing(db):
        vintage, results = engine.build_vintage(db, ctx.org_id, venue.id, as_of)
    return {"vintage": engine.vintage_dict(vintage), "days": len(results)}


class BacktestBody(_Body):
    venue_id: str
    months: int = 6
    store: bool = False
    level_correction: bool | None = None


@router.post("/forecast/backtest")
def run_backtest(
    body: BacktestBody,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
):
    venue = _venue(db, ctx, body.venue_id)
    with _writing(db):
        return scoring.backtest(
            db,
            ctx.org_id,
            venue.id,
            months=body.months,
            store=body.store,
            level_correction=body.level_correction,
        )


@router.get("/forecast/scores")
def forecast_scores(
    venue_id: str,
    year: int,
    month: int,
    ctx: FinanceCtx = Depends(finance_ctx),
    db: Session = Depends(get_db),
):
    _venue(db, ctx, venue_id)
    with _reading():
        return scoring.month_strip(db, ctx.org_id, venue_id, year, month)


class HistoryBody(_Body):
    venue_id: str
    months: int = 24


@router.post("/forecast/load-history")
def load_history(
    body: HistoryBody,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    """Fill the actuals cache back ``months`` months from Loaded (reads only)."""
    venue = _venue(db, ctx, body.venue_id)
    with _reading():
        out = actuals_svc.load_history(
            db, config_db, venue, months=body.months, user=ctx.user
        )
    db.commit()
    return out


# ===========================================================================
# Holidays
# ===========================================================================


class SyncHolidaysBody(_Body):
    venue_id: str


@router.post("/holidays/sync")
def sync_holidays(
    body: SyncHolidaysBody,
    ctx: FinanceCtx = Depends(editor_ctx),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    """The venue's public holidays from Loaded's pay adjustments — the
    history and the next twelve months (the nightly job keeps the next twelve
    fresh). Reads Loaded on the venue's token; never writes it."""
    with _writing(db):
        return holidays.sync_from_page(
            db, ctx, body.venue_id, today=dt.date.today(), config_db=config_db
        )


@router.get("/holidays")
def list_holidays(
    venue_id: str,
    year: int | None = None,
    ctx: FinanceCtx = Depends(finance_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        rows = holidays.list_holidays(db, ctx, venue_id, year=year)
    return [holidays.holiday_dict(r) for r in rows]


# ===========================================================================
# Pushes
# ===========================================================================


@router.get("/pushes")
def list_pushes(
    venue_id: str | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    limit: int = 200,
    ctx: FinanceCtx = Depends(finance_ctx),
    db: Session = Depends(get_db),
):
    with _reading():
        return pushes.ledger(
            db,
            ctx,
            venue_id=venue_id,
            first=_date(from_, "from") if from_ else None,
            last=_date(to, "to") if to else None,
            limit=limit,
        )


@router.post("/pushes/restore/plan")
def restore_plan(
    body: dict, ctx: FinanceCtx = Depends(editor_ctx), db: Session = Depends(get_db)
):
    """Body: ``{venue_id, from, to, scope}``."""
    unknown = set(body) - {"venue_id", "from", "to", "scope"}
    if (
        unknown
        or not body.get("venue_id")
        or not body.get("from")
        or not body.get("to")
    ):
        raise HTTPException(
            422, {"code": "invalid", "message": "body is {venue_id, from, to, scope}"}
        )
    with _reading():
        return pushes.restore_plan(
            db,
            ctx,
            str(body["venue_id"]),
            _date(body["from"], "from"),
            _date(body["to"], "to"),
            scope=str(body.get("scope") or "latest"),
        )
