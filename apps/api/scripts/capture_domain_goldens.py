"""Capture golden reference numbers for the domain-tool conversions.

Phase 0 of the domain-tools arc (get_sales / get_labour): run the CURRENT
live config-DB consolidators over COMPLETED periods and record the numbers
they return, so each new domain tool is verified against reality — the
numbers the business already trusts — rather than against itself.

Writes tests/goldens/domain_tools_2026-08.json. Local-only: it needs venue
credentials (the local DB's LoadedHub tokens), which CI does not have. The
sandbox unit suites remain the CI gate; this file is the ship gate a human
runs per phase via scripts/verify_domain_goldens.py.

NOTE: this battery calls the PRE-conversion tools by design — once a
family's rollout deletes those rows (sales: sync_sales_domain_rollout.py)
its part of this script cannot re-run. That is fine: goldens are captured
once, before the conversion, and the committed JSON is the record.

Scope notes, 24 Aug 2026:
- Venues: La Zeppa and Bessie & Royals — the only two with live LOCAL
  token chains (Glass Goose / Dunedin Social Club / Freeman & Grey refresh
  tokens are expired locally, and re-authing locally is forbidden: the
  grant is a single chain, so it would steal production's tokens).
- Weekly periods use "week beginning N August 2026" — deterministic in the
  business calendar, stable forever. The monthly golden is captured with
  the phrase "last month" (deterministic; the month-name phrase needs the
  LLM resolver, and local has no API key). That phrase means July 2026
  only while it is still August 2026 — the stored window is the truth.
- Timeclock flags ride as STRINGS ("false"): the executor's required-field
  check is falsy-based, so a boolean False is rejected as missing. This is
  exactly what LLM traffic sends.

Usage:
    uv run python scripts/capture_domain_goldens.py
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

OUT = (
    pathlib.Path(__file__).resolve().parent.parent
    / "tests"
    / "goldens"
    / "domain_tools_2026-08.json"
)

VENUES = ["La Zeppa", "Bessie & Royals"]
WEEKS = ["week beginning 10 August 2026", "week beginning 3 August 2026"]
MONTH_PHRASE = "last month"
MONTH_LABEL = "July 2026"

DINNER = [{"start_hour": 17, "end_hour": 22, "label": "dinner"}]

# family tags let a re-run refresh ONE family's goldens (merging into the
# existing file) while another family's old tools are already retired.
BATTERY_WEEKLY = [
    ("loadedhub", "get_sales_for_period", {}),
    ("loadedhub", "get_pos_item_sales_for_period", {}),
    ("loadedhub", "get_staff_orders_for_period", {}),
    ("loadedhub", "get_pos_discounts_for_period", {}),
    ("loadedhub", "get_budgets", {}),
    ("loadedhub", "get_staff_attendance", {}),
    ("loadedhub", "get_roster_vs_actual_for_period", {"interval": "1.00:00:00"}),
    (
        "loadedhub",
        "get_timeclock_entries_for_period",
        {
            "include_inactive": "false",
            "include_only_clockins": "false",
            "should_truncate_shifts": "true",
        },
    ),
    (
        "norm_reports",
        "get_periodic_sales",
        {"time_windows": DINNER, "group_by": "each"},
    ),
]
BATTERY_MONTHLY = [
    ("loadedhub", "get_sales_for_period", {"interval": "31.00:00:00"}),
    ("loadedhub", "get_budgets", {}),
]


def _rows(payload):
    """The record array inside a wrapper payload (mirrors for_period.py)."""
    if isinstance(payload, list):
        return payload if payload and isinstance(payload[0], dict) else []
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return []


def _shape(results: dict) -> dict:
    """Compact goldens from one venue+period's raw battery results."""
    out: dict = {}

    sales = results.get("get_sales_for_period") or {}
    win = sales.get("window") or {}
    out["window"] = {"start": win.get("start"), "end": win.get("end")}
    daily = {}
    for row in _rows(sales.get("data")):
        amt = row.get("invoices")
        if not isinstance(amt, (int, float)):
            amt = row.get("amount")
        if isinstance(amt, (int, float)):
            daily[str(row.get("startTime", ""))[:10]] = round(float(amt), 2)
    out["sales"] = {"total": round(sum(daily.values()), 2), "daily": daily}

    items = results.get("get_pos_item_sales_for_period")
    if items:
        rows = _rows(items.get("data"))
        top = sorted(rows, key=lambda r: -(r.get("amount") or 0))[:10]
        out["item_sales"] = {
            "row_count": len(rows),
            "amount_total": round(sum(r.get("amount") or 0 for r in rows), 2),
            "quantity_total": round(sum(r.get("quantity") or 0 for r in rows), 2),
            "top10": [
                {
                    "name": r.get("itemName"),
                    "amount": round(r.get("amount") or 0, 2),
                    "quantity": r.get("quantity"),
                }
                for r in top
            ],
        }

    staff = results.get("get_staff_orders_for_period")
    if staff:
        rows = _rows(staff.get("data"))
        out["staff_orders"] = {
            "row_count": len(rows),
            "amount_total": round(sum(r.get("amount") or 0 for r in rows), 2),
            "quantity_total": round(sum(r.get("quantity") or 0 for r in rows), 2),
            "by_staff": {
                str(r.get("label")): round(r.get("amount") or 0, 2) for r in rows
            },
        }

    disc = results.get("get_pos_discounts_for_period")
    if disc:
        rows = _rows(disc.get("data"))
        out["discounts"] = {
            "row_count": len(rows),
            "amount_total": round(sum(r.get("discountsAmount") or 0 for r in rows), 2),
            "count_total": round(sum(r.get("discountsCount") or 0 for r in rows), 2),
        }

    bud = results.get("get_budgets")
    if bud:
        out["budget"] = {
            "total": bud.get("total"),
            "daily": {d.get("date"): d.get("amount") for d in bud.get("days") or []},
        }

    att = results.get("get_staff_attendance")
    if att:
        out["attendance"] = {
            "totals": att.get("totals"),
            "row_count": len(att.get("rows") or []),
        }

    rva = results.get("get_roster_vs_actual_for_period")
    if rva:
        # Sum the raw rows and round once — the old wrapper's summary
        # rounded per addition, drifting by pennies over a week.
        rows = _rows(rva.get("data"))
        out["roster_vs_actual"] = {
            "row_count": len(rows),
            "sums": {
                k: round(sum(r.get(k) or 0 for r in rows), 2)
                for k in (
                    "rosteredCost",
                    "actualCost",
                    "rosteredHours",
                    "actualHours",
                )
            },
        }

    tc = results.get("get_timeclock_entries_for_period")
    if tc:
        rows = _rows(tc.get("data"))
        out["timeclock"] = {
            "row_count": len(rows),
            "totalHours": round(sum(r.get("totalHours") or 0 for r in rows), 2),
            "totalCost": round(sum(r.get("totalCost") or 0 for r in rows), 2),
        }

    per = results.get("get_periodic_sales")
    if per:
        out["periodic_dinner"] = {
            "daily": {r.get("period"): r.get("dinner") for r in per.get("rows") or []},
            "total": (per.get("totals") or {}).get("dinner"),
        }

    return out


_FAMILY = {
    "get_sales_for_period": "sales",
    "get_pos_item_sales_for_period": "sales",
    "get_staff_orders_for_period": "sales",
    "get_pos_discounts_for_period": "sales",
    "get_budgets": "sales",
    "get_periodic_sales": "sales",
    "get_staff_attendance": "labour",
    "get_roster_vs_actual_for_period": "labour",
    "get_timeclock_entries_for_period": "labour",
}

_SHAPE_KEYS = {
    "sales": (
        "window",
        "sales",
        "item_sales",
        "staff_orders",
        "discounts",
        "budget",
        "periodic_dinner",
        "_period_phrase",
    ),
    "labour": ("attendance", "roster_vs_actual", "timeclock"),
}


def main(family: str | None = None) -> None:
    from app.agents.internal_tools import execute_consolidator
    from app.db.config_models import ConnectionSpec
    from app.db.engine import SessionLocal, _ConfigSessionLocal

    cfg = _ConfigSessionLocal()
    configs = {}
    try:
        for spec in (
            cfg.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name.in_(["loadedhub", "norm_reports"]))
            .all()
        ):
            for t in spec.tools or []:
                configs[(spec.connector_name, t.get("action"))] = t.get(
                    "consolidator_config"
                )
    finally:
        cfg.close()

    db = SessionLocal()
    goldens: dict = {}
    errors: list[str] = []
    try:
        for venue in VENUES:
            goldens[venue] = {}
            batches = [(p, BATTERY_WEEKLY) for p in WEEKS]
            batches.append((MONTH_PHRASE, BATTERY_MONTHLY))
            for period, battery in batches:
                raw = {}
                for connector, action, extra in battery:
                    if family and _FAMILY.get(action, "sales") != family:
                        continue
                    config = configs.get((connector, action))
                    if not config:
                        errors.append(f"no live row for {connector}.{action}")
                        continue
                    params = {"venue": venue, "period": period, **extra}
                    t0 = time.time()
                    result = execute_consolidator(config, params, db, None)
                    data = result.get("data") if isinstance(result, dict) else None
                    label = f"{venue} | {period} | {action}"
                    print(f"{label} ({time.time() - t0:.1f}s)", flush=True)
                    if not (isinstance(result, dict) and result.get("success")):
                        errors.append(f"{label}: {result.get('error')}")
                        continue
                    if isinstance(data, dict) and data.get("error"):
                        errors.append(f"{label}: {data['error']}")
                        continue
                    raw[action] = data
                label = MONTH_LABEL if period == MONTH_PHRASE else period
                goldens[venue][label] = _shape(raw)
                if period == MONTH_PHRASE:
                    goldens[venue][label]["_period_phrase"] = MONTH_PHRASE
    finally:
        db.close()

    if errors:
        print("\nCAPTURE ERRORS — goldens NOT written:")
        for e in errors:
            print(" ", e)
        sys.exit(1)

    if family:
        # Merge: refresh only this family's keys, keep the rest verbatim.
        existing = json.loads(OUT.read_text())
        keep = _SHAPE_KEYS[family]
        for venue, periods in goldens.items():
            for label, shaped in periods.items():
                slot = existing["goldens"].setdefault(venue, {}).setdefault(label, {})
                for k in keep:
                    if k in shaped:
                        slot[k] = shaped[k]
        existing["_meta"]["captured_at_" + family] = datetime.now(
            timezone.utc
        ).isoformat(timespec="seconds")
        OUT.write_text(json.dumps(existing, indent=1, sort_keys=True) + "\n")
        print(f"\nmerged {family} goldens into {OUT}")
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "_meta": {
                    "captured_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                    "venues": VENUES,
                    "weekly_periods": WEEKS,
                    "monthly": {
                        "label": MONTH_LABEL,
                        "phrase": MONTH_PHRASE,
                        "note": (
                            "'last month' is deterministic locally; it means "
                            "July 2026 only while it is August 2026 — the "
                            "stored window is the truth"
                        ),
                    },
                    "tolerance": (
                        "engine-computed sums must match exactly; the hourly "
                        "sales feed rounds to dollars, so daily/total sales "
                        "comparisons allow ±$3"
                    ),
                    "source": "scripts/capture_domain_goldens.py",
                },
                "goldens": goldens,
            },
            indent=1,
            sort_keys=True,
        )
        + "\n"
    )
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    main(family=arg.removeprefix("--family=") if arg else None)
