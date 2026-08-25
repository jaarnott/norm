"""Verify a new domain tool against the Phase-0 goldens.

Runs the WORKING-TREE consolidator code (config/consolidators/get_sales.py
— nothing needs to be synced) through execute_consolidator against the
live local venues, and checks every number against
tests/goldens/domain_tools_2026-08.json — the values the CURRENT tools
returned for completed periods. A conversion that changes an answer the
business already trusts fails here before it ships.

Local-only (needs venue credentials). Tolerances: engine-computed sums
must match exactly; values summed from the hourly sales feed allow ±$3
(the feed rounds to dollars).

Usage:
    uv run python scripts/verify_domain_goldens.py sales
    uv run python scripts/verify_domain_goldens.py labour
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

BASE = pathlib.Path(__file__).resolve().parent.parent
GOLDENS = BASE / "tests" / "goldens" / "domain_tools_2026-08.json"

DINNER = [{"start_hour": 17, "end_hour": 22, "label": "dinner"}]

PASS = 0
FAIL = 0


def check(label: str, got, want, tol: float = 0.0) -> None:
    global PASS, FAIL
    ok = False
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        ok = abs(float(got) - float(want)) <= tol
    else:
        ok = got == want
    if ok:
        PASS += 1
        print(f"  PASS {label}: {got}")
    else:
        FAIL += 1
        print(f"  FAIL {label}: got {got!r}, want {want!r} (tol {tol})")


def verify_sales(execute, venues: dict) -> None:
    code = (BASE / "config" / "consolidators" / "get_sales.py").read_text()
    config = {
        "function_code": code,
        "max_api_calls": 40,
        "allowed_write_actions": [],
    }

    for venue, periods in venues.items():
        for period_label, g in periods.items():
            phrase = g.get("_period_phrase") or period_label
            monthly = "_period_phrase" in g
            print(f"\n== {venue} | {period_label} (get_sales) ==")

            # total + budget compare
            out = execute(
                config,
                {
                    "venue": venue,
                    "period": phrase,
                    "compare": "budget",
                },
            )
            check(
                "window.start",
                (out.get("window") or {}).get("start"),
                g["window"]["start"],
            )
            row = (out.get("rows") or [{}])[0]
            check("total actual", row.get("actual"), g["sales"]["total"], 3.0)
            if g.get("budget"):
                check("budget total", row.get("budget"), g["budget"]["total"])

            if monthly:
                # The monthly golden holds one 31-day bucket + the budget
                # total — both checked above; there are no daily rows.
                continue

            # daily
            out = execute(
                config,
                {"venue": venue, "period": phrase, "breakdown": "daily"},
            )
            daily = {r["date"]: r["actual"] for r in out.get("rows") or []}
            for date, amt in g["sales"]["daily"].items():
                check(f"daily {date}", daily.get(date), amt, 3.0)

            # items
            out = execute(
                config,
                {
                    "venue": venue,
                    "period": phrase,
                    "breakdown": "items",
                    "top": 10,
                },
            )
            t = out.get("totals") or {}
            gi = g["item_sales"]
            check("items amount_total", t.get("sales"), gi["amount_total"], 0.01)
            check("items quantity_total", t.get("quantity"), gi["quantity_total"], 0.01)
            # get_sales merges POS-duplicate rows (same item name + group +
            # category under different POS identifiers), so its row count
            # can only be at or below the raw feed's — the money totals
            # above are the equality that matters.
            rc = t.get("row_count") or 0
            check(
                "items row_count <= raw",
                rc if rc <= gi["row_count"] else f"{rc} > {gi['row_count']}",
                rc,
            )
            got_top = [
                r["item"] for r in out.get("rows") or [] if r.get("item") != "(others)"
            ]
            want_top = [i["name"] for i in gi["top10"]]
            check("items top10", got_top, want_top)

            # staff
            out = execute(
                config,
                {"venue": venue, "period": phrase, "breakdown": "staff"},
            )
            t = out.get("totals") or {}
            gs = g["staff_orders"]
            check("staff sales_total", t.get("sales"), gs["amount_total"], 0.01)
            by_staff = {r["staff"]: r["sales"] for r in out.get("rows") or []}
            for name, amt in gs["by_staff"].items():
                if amt > 0:
                    check(f"staff {name}", by_staff.get(name), amt, 0.01)

            # discounts
            out = execute(
                config,
                {"venue": venue, "period": phrase, "breakdown": "discounts"},
            )
            t = out.get("totals") or {}
            gd = g["discounts"]
            check(
                "discounts amount_total",
                t.get("discounts_amount"),
                gd["amount_total"],
                0.01,
            )
            check(
                "discounts count_total",
                t.get("discounts_count"),
                gd["count_total"],
                0.01,
            )

            # dinner time-window cut (day-start attribution)
            out = execute(
                config,
                {
                    "venue": venue,
                    "period": phrase,
                    "time_windows": DINNER,
                    "group_by": "each",
                },
            )
            gp = g["periodic_dinner"]
            got_daily = {r["period"]: r.get("dinner") for r in out.get("rows") or []}
            for label, amt in gp["daily"].items():
                check(f"dinner {label}", got_daily.get(label), amt, 0.01)
            check(
                "dinner total",
                (out.get("totals") or {}).get(venue, {}).get("dinner"),
                gp["total"],
                0.01,
            )


def verify_labour(execute, venues: dict) -> None:
    code = (BASE / "config" / "consolidators" / "get_labour.py").read_text()
    config = {
        "function_code": code,
        "max_api_calls": 12,
        "allowed_write_actions": [],
    }

    for venue, periods in venues.items():
        for period_label, g in periods.items():
            if "_period_phrase" in g or "attendance" not in g:
                continue  # monthly goldens carry no labour battery
            print(f"\n== {venue} | {period_label} (get_labour) ==")

            # attendance (the default view) — engine identical to the
            # retired get_staff_attendance, so totals must match exactly.
            out = execute(config, {"venue": venue, "period": period_label})
            ga = g["attendance"]
            for key, want in (ga.get("totals") or {}).items():
                check(f"attendance {key}", (out.get("totals") or {}).get(key), want)
            check("attendance row_count", len(out.get("rows") or []), ga["row_count"])

            # vs_actual
            out = execute(
                config,
                {"venue": venue, "period": period_label, "view": "vs_actual"},
            )
            gr = g["roster_vs_actual"]
            sums = (out.get("summary") or {}).get("column_sums") or {}
            for key, want in (gr.get("sums") or {}).items():
                if want is not None:
                    check(f"vs_actual {key}", sums.get(key), want)
            check(
                "vs_actual row_count",
                (out.get("summary") or {}).get("row_count"),
                gr["row_count"],
            )

            # timeclock
            out = execute(
                config,
                {"venue": venue, "period": period_label, "view": "timeclock"},
            )
            gt = g["timeclock"]
            summary = out.get("summary") or {}
            sums = summary.get("column_sums") or {}
            check("timeclock row_count", summary.get("row_count"), gt["row_count"])
            check("timeclock totalHours", sums.get("totalHours"), gt["totalHours"])
            check("timeclock totalCost", sums.get("totalCost"), gt["totalCost"])


def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else "sales"
    from app.agents.internal_tools import execute_consolidator
    from app.db.engine import SessionLocal

    goldens = json.loads(GOLDENS.read_text())["goldens"]
    db = SessionLocal()
    try:

        def execute(config, params):
            result = execute_consolidator(config, params, db, None)
            if not (isinstance(result, dict) and result.get("success")):
                return {"error": str(result)}
            return result.get("data") or {}

        if what == "sales":
            verify_sales(execute, goldens)
        elif what == "labour":
            verify_labour(execute, goldens)
        else:
            raise SystemExit(f"unknown target {what!r} — expected 'sales' or 'labour'")
    finally:
        db.close()

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
