"""The Weekly Summary's Norm side (plan §4, §13 Phase 6; 04 §7, §12 D).

Two things live here:

* ``budget_plan`` — the locked budget for a week and its month-to-date,
  with the monthly lines prorated **as Orbit's Financial Summary did**
  (``financialSummary.ts``): a dollar line is spread by ``month value ÷
  days in that month`` over the window's days; a percentage line is the
  plain average of the month ratio over those days; wages per day are
  ``wage_cents ÷ days in month`` when the month budgets a dollar wage, else
  ``wage_pct × that day's locked budget ex GST``. Plus the week's adjusted
  wages and leave from the payroll reconciliation (F4's
  ``week_adjustments`` — zeros and a note until it lands). Small on
  purpose: ``loadedhub.get_weekly_financials`` carries it inside a
  60k-character result, one per venue.

* ``weekly`` — the Weekly Summary page's data: ``get_weekly_financials``
  per venue through ``execute_connector_tool`` on that venue's own token,
  the viewer's venues only, group and non-trading rows only for
  ``payroll:read`` holders, nothing cached. Figures are the consolidator's
  own (dollars, F2's contract) — this module adds nothing to them.
"""

from __future__ import annotations

import calendar
import datetime as dt
import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models import Organization, Venue
from app.services.finance import budgets
from app.services.finance_core.ctx import FinanceCtx, require_venue
from app.services.finance_core.errors import Invalid

logger = logging.getLogger(__name__)

#: GST by organisation currency — Loaded's budgets include it.
GST_RATES = {"NZD": Decimal("0.15"), "AUD": Decimal("0.10")}
_DOLLAR_LINES = ("leave_cents", "opex_cents", "depreciation_cents")
_RATIO_LINES = ("cogs_pct", "food_cogs_pct", "bev_cogs_pct", "rebates_pct")
#: Keys the group row sums; ratios are recomputed from them.
_SUMMED = (
    "sales",
    "sales_ex_gst",
    "gst",
    "budget",
    "vs_budget",
    "orders",
    "order_value",
    "bills",
    "discounts",
    "voids",
    "void_count",
    "waste",
    "food_sales",
    "beverage_sales",
    "other_sales",
    "split_mismatch",
    "wages",
    "food_wages",
    "beverage_wages",
    "other_wages",
    "rostered_cost",
    "rostered_hours",
    "actual_hours",
    "kitchen_hours",
    "hours_variance",
    "cost_variance",
    "cash_variance",
    "total_variance",
    "cashup_buckets",
)


def gst_rate(db: Session, org_id: str) -> Decimal:
    currency = (
        db.query(Organization.currency).filter(Organization.id == org_id).scalar()
    )
    return GST_RATES.get(str(currency or "NZD").upper(), Decimal("0.15"))


def week_of(week_ending) -> tuple[dt.date, dt.date]:
    """Monday..Sunday for a week-ending date; any date snaps to its week's
    Sunday, as ``get_weekly_financials`` does."""
    if isinstance(week_ending, dt.date):
        day = week_ending
    else:
        try:
            day = dt.date.fromisoformat(str(week_ending)[:10])
        except (TypeError, ValueError) as exc:
            raise Invalid("week_ending must be YYYY-MM-DD.") from exc
    sunday = day + dt.timedelta(days=(6 - day.weekday()) % 7)
    return sunday - dt.timedelta(days=6), sunday


def _dollars(cents: int) -> float:
    return round(cents / 100, 2)


def _window(db, org_id, venue_id, first, last, lines, sm, rate, adjustments) -> dict:
    locked = budgets.locked_cents(db, org_id, venue_id, first, last)
    budget_cents = sum(locked.values())
    budget_ex = Decimal(budget_cents) / (1 + rate)
    totals = {k: Decimal(0) for k in _DOLLAR_LINES}
    ratio_sums = {k: Decimal(0) for k in _RATIO_LINES}
    ratio_counts = {k: 0 for k in _RATIO_LINES}
    wages = Decimal(0)
    wage_ratio_sum, wage_ratio_count = Decimal(0), 0
    cursor = first
    while cursor <= last:
        fy = budgets.fiscal_year_of(cursor, sm)
        days_in_month = calendar.monthrange(cursor.year, cursor.month)[1]
        for item in _DOLLAR_LINES:
            row = lines.get((fy, cursor.month, item))
            if row is not None and row.value_cents is not None:
                totals[item] += Decimal(row.value_cents) / days_in_month
        for item in _RATIO_LINES:
            row = lines.get((fy, cursor.month, item))
            if row is not None and row.value_ratio is not None:
                ratio_sums[item] += Decimal(row.value_ratio)
                ratio_counts[item] += 1
        wage_amount = lines.get((fy, cursor.month, "wage_cents"))
        wage_pct = lines.get((fy, cursor.month, "wage_pct"))
        if wage_amount is not None and wage_amount.value_cents:
            wages += Decimal(wage_amount.value_cents) / days_in_month
        elif wage_pct is not None and wage_pct.value_ratio is not None:
            day_ex = Decimal(locked.get(cursor, 0)) / (1 + rate)
            wages += day_ex * Decimal(wage_pct.value_ratio)
            wage_ratio_sum += Decimal(wage_pct.value_ratio)
            wage_ratio_count += 1
        cursor += dt.timedelta(days=1)
    ratios = {
        item[:-4] + "_pct": (
            float(round(ratio_sums[item] / ratio_counts[item], 4))
            if ratio_counts[item]
            else None
        )
        for item in _RATIO_LINES
    }
    cogs = (
        budget_ex * Decimal(str(ratios["cogs_pct"]))
        if ratios["cogs_pct"] is not None
        else None
    )
    out_lines = {
        "wages": float(round(wages / 100, 2)),
        "wage_pct": float(round(wages / budget_ex, 4))
        if budget_ex
        else (
            float(round(wage_ratio_sum / wage_ratio_count, 4))
            if wage_ratio_count
            else None
        ),
        "cogs": float(round(cogs / 100, 2)) if cogs is not None else None,
        **ratios,
        "leave": float(round(totals["leave_cents"] / 100, 2)),
        "opex": float(round(totals["opex_cents"] / 100, 2)),
        "depreciation": float(round(totals["depreciation_cents"] / 100, 2)),
    }
    gp = budget_ex - wages - totals["leave_cents"] - (cogs or 0)
    out_lines["gross_profit"] = float(round(gp / 100, 2))
    out_lines["operating_profit"] = float(round((gp - totals["opex_cents"]) / 100, 2))
    out_lines["net_profit"] = float(
        round((gp - totals["opex_cents"] - totals["depreciation_cents"]) / 100, 2)
    )
    return {
        "start": first.isoformat(),
        "end": last.isoformat(),
        "days_with_budget": len(locked),
        "budget": _dollars(budget_cents),
        "budget_cents": budget_cents,
        "budget_ex_gst": float(round(budget_ex / 100, 2)),
        "lines": out_lines,
        "adjustments": adjustments,
    }


def _adjustments(db, org_id, venue_id, week_start: dt.date) -> tuple[dict, str | None]:
    """F4's ``payroll.reconciliation.week_adjustments`` — zeros plus a note
    while it is not built or fails."""
    import importlib

    try:
        reconciliation = importlib.import_module("app.services.payroll.reconciliation")
    except ImportError:
        return {"wages": 0.0, "leave": 0.0}, (
            "adjusted wages/leave unavailable: the payroll reconciliation is not built yet"
        )
    fn = getattr(reconciliation, "week_adjustments", None)
    if fn is None:
        return {"wages": 0.0, "leave": 0.0}, (
            "adjusted wages/leave unavailable: the payroll reconciliation has no week_adjustments yet"
        )
    try:
        got = fn(db, org_id, venue_id, week_start) or {}
    except Exception as exc:  # noqa: BLE001 — the summary still answers
        logger.warning("budget_plan: week_adjustments failed: %s", exc)
        return {"wages": 0.0, "leave": 0.0}, f"adjusted wages/leave unavailable: {exc}"[
            :200
        ]
    return {
        "wages": _dollars(int(got.get("wages_cents") or 0)),
        "leave": _dollars(int(got.get("leave_cents") or 0)),
    }, None


def budget_plan(db: Session, ctx: FinanceCtx, venue_id: str, week_ending) -> dict:
    """What ``norm_finance.get_budget_plan`` returns: the locked budget and
    prorated lines for the week and its month-to-date (the month the week
    starts in, up to the week's end — Orbit's anchor)."""
    ctx.require("finance:read")
    require_venue(ctx, venue_id)
    first, last = week_of(week_ending)
    mtd_first = first.replace(day=1)
    sm = budgets.start_month(db, ctx.org_id)
    rate = gst_rate(db, ctx.org_id)
    fys = {budgets.fiscal_year_of(d, sm) for d in (mtd_first, first, last)}
    lines = budgets.lines_map(db, ctx.org_id, venue_id, fys)
    fy = budgets.fiscal_year_of(last, sm)
    version = budgets.current_version(db, ctx.org_id, venue_id, fy)
    adjustments, adj_note = _adjustments(db, ctx.org_id, venue_id, first)
    week = _window(db, ctx.org_id, venue_id, first, last, lines, sm, rate, adjustments)
    mtd = _window(db, ctx.org_id, venue_id, mtd_first, last, lines, sm, rate, None)
    notes = [
        "monthly lines prorated by day as Orbit's Financial Summary does; budget includes GST"
    ]
    if version is None:
        notes.append(f"no locked budget for FY{fy} at this venue")
    if adj_note:
        notes.append(adj_note)
    return {
        "venue_id": venue_id,
        "fiscal_year": fy,
        "week_ending": last.isoformat(),
        "version": (
            {
                "id": version.id,
                "label": version.label,
                "locked_at": version.locked_at.isoformat()
                if version.locked_at
                else None,
            }
            if version
            else None
        ),
        "week": week,
        "mtd": mtd,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# The Weekly Summary page
# ---------------------------------------------------------------------------


def viewer_venues(db: Session, ctx: FinanceCtx, venue_ids=None) -> list[Venue]:
    """The venues a Summary call may cover: the viewer's, trading ones only
    unless they hold payroll:read (CBI trades nothing and is a payroll fact)."""
    q = db.query(Venue).filter(
        Venue.organization_id == ctx.org_id, Venue.id.in_(sorted(ctx.venue_ids))
    )
    if not ctx.holds("payroll:read"):
        q = q.filter(Venue.is_trading.is_(True))
    rows = q.order_by(Venue.name).all()
    if venue_ids:
        wanted = {str(v) for v in venue_ids}
        for v in wanted:
            require_venue(ctx, v)
        rows = [v for v in rows if v.id in wanted]
    return rows


def _sum_group(blocks: list[dict]) -> dict:
    out: dict = {}
    for key in _SUMMED:
        values = [b.get(key) for b in blocks if isinstance(b.get(key), (int, float))]
        if values:
            out[key] = round(sum(values), 2)
    sales, ex = out.get("sales"), out.get("sales_ex_gst")
    budget, wages = out.get("budget"), out.get("wages")
    if sales is not None and budget:
        out["budget_pct"] = round(sales / budget, 4)
    if ex and wages is not None:
        out["wage_pct"] = round(wages / ex, 4)
    if out.get("orders") and out.get("order_value") is not None:
        out["average_order"] = round(out["order_value"] / out["orders"], 2)
    if sales:
        for group in ("food", "beverage"):
            part = out.get(f"{group}_sales")
            if part is not None:
                out[f"{group}_share"] = round(part / sales, 4)
    return out


def weekly(
    db: Session,
    ctx: FinanceCtx,
    config_db,
    week_ending,
    *,
    venue_ids=None,
    include_days: bool = True,
) -> dict:
    """One ``get_weekly_financials`` per venue on its own token; the
    group row only for payroll:read holders."""
    from app.connectors.tool_executor import execute_connector_tool

    ctx.require("finance:read")
    first, last = week_of(week_ending)
    venues_out, warnings = [], []
    week_block = mtd_block = None
    for venue in viewer_venues(db, ctx, venue_ids):
        try:
            res = execute_connector_tool(
                connector_name="loadedhub",
                action="get_weekly_financials",
                params={
                    "venue": venue.name,
                    "venue_id": venue.id,
                    "week_ending": last.isoformat(),
                    "include_days": include_days,
                },
                db=db,
                config_db=config_db,
                venue_id=venue.id,
                strict_venue=True,
                user=ctx.user,
            )
        except Exception as exc:  # noqa: BLE001 — one venue's failure is its own row
            venues_out.append(
                {"venue": venue.name, "venue_id": venue.id, "error": str(exc)[:300]}
            )
            continue
        data = res.payload if res.success else None
        error = (
            res.error
            if not res.success
            else (data.get("error") if isinstance(data, dict) else "no data")
        )
        if error or not isinstance(data, dict):
            venues_out.append(
                {"venue": venue.name, "venue_id": venue.id, "error": str(error)[:300]}
            )
            continue
        week_block = week_block or data.get("week")
        mtd_block = mtd_block or data.get("mtd")
        warnings += [str(w) for w in data.get("warnings") or []]
        mine = next(
            (
                v
                for v in data.get("venues") or []
                if v.get("venue_id") == venue.id or v.get("venue") == venue.name
            ),
            None,
        )
        if mine is None:
            venues_out.append(
                {
                    "venue": venue.name,
                    "venue_id": venue.id,
                    "error": "venue missing from the result",
                }
            )
            continue
        venues_out.append(
            {
                **mine,
                "venue": venue.name,
                "venue_id": venue.id,
                "is_trading": venue.is_trading,
            }
        )
    good = [v for v in venues_out if not v.get("error")]
    group = None
    if ctx.holds("payroll:read") and good:
        group = {
            "week": _sum_group([v.get("week") or {} for v in good]),
            "mtd": _sum_group([v.get("mtd") or {} for v in good]),
        }
    return {
        "week_ending": last.isoformat(),
        "week_start": first.isoformat(),
        "week": week_block,
        "mtd": mtd_block,
        "venues": venues_out,
        "group": group,
        "can_see_group": ctx.holds("payroll:read"),
        "warnings": warnings,
        "notes": [
            "figures are loadedhub.get_weekly_financials' own, per venue on its own login",
            "budget_plan inside each week is norm_finance.get_budget_plan",
        ],
    }
