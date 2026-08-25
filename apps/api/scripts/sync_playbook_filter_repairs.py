"""Repair the reports/roster/stocktake playbooks the demotions crippled.

A playbook ``tool_filter`` entry naming an engine_only action is SILENTLY
dropped (the filter runs after the engine_only skip; the config validator
only checks the action exists on some spec, which engine_only satisfies).
The for_period demotion arcs swept agent prompts and the invoice playbooks
but missed these seven — leaving e.g. sales_comparison with just
get_periodic_sales, which is how prod thread b9bda2c1 answered "budget vs
actual for this week" with "I don't have a budget data source" (23 Aug
2026).

Fixes, per playbook: every demoted raw in tool_filter and instructions
moves to its ``_for_period`` twin, and the two sales playbooks that answer
budget-vs-actual questions gain ``get_budgets``.

Usage:
    uv run python scripts/sync_playbook_filter_repairs.py [--dry-run]
"""

from __future__ import annotations

import re
import sys

sys.path.insert(0, ".")

#: raw → date-safe replacement (order irrelevant; regex guards handle
#: substrings). Kept at CURRENT doctrine so a replay can never write a
#: retired name into a filter: the whole sales family now maps to
#: get_sales (24 Aug 2026 — see sync_sales_config.py).
SWAPS = {
    "get_sales_data": "get_sales",
    "get_pos_item_sales": "get_sales",
    "get_staff_orders": "get_sales",
    "get_staff_item_orders": "get_sales",
    "get_cogs_detail": "get_cogs_detail_for_period",
    "get_completed_stocktakes": "get_completed_stocktakes_for_period",
    "get_roster": "get_roster_for_period",
    "get_timeclock_entries": "get_timeclock_entries_for_period",
}

SLUGS = [
    "weekly_sales_report",
    "sales_comparison",
    "product_sales_analysis",
    "staff_sales_performance",
    "cogs_analysis",
    "stocktake_variance",
    "roster_viewer",
]

#: Budget-vs-actual is these playbooks' bread and butter; the consolidated
#: budget tool belongs in their reach.
ADD_BUDGETS = {"sales_comparison", "weekly_sales_report"}

BUDGET_NOTE = (
    "\n\nFor budget vs actual questions, call `get_budgets` (takes a period "
    "in plain English or from_date/to_date; dates are corrected to the day "
    "each budget is FOR) alongside the sales figures."
)

DAILY_NOTE = (
    "\n\nFor daily figures always pass `period` in plain English to the "
    "sales tools — they align each day to the venue's trading day (a "
    "Saturday's 1am trade counts as Saturday). Never pass explicit "
    "period_start/period_end for day-by-day comparisons: those are civil "
    "calendar days and misattribute late-night trade."
)


def _swap_text(text: str) -> str:
    for old, new in SWAPS.items():
        # Guarded: never inside a longer identifier (get_roster_vs_actual,
        # get_staff_item_orders) and never a name already _for_period.
        text = re.sub(rf"(?<![A-Za-z_]){old}(?![A-Za-z_])", new, text)
    return text


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import Playbook
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    changed: list[str] = []
    try:
        for slug in SLUGS:
            pb = db.query(Playbook).filter(Playbook.slug == slug).first()
            if not pb:
                changed.append(f"{slug}: NOT FOUND — skipped")
                continue
            tf = list(pb.tool_filter or [])
            new_tf: list[str] = []
            for a in tf:
                repl = SWAPS.get(a, a)
                if repl not in new_tf:
                    new_tf.append(repl)
            if slug in ADD_BUDGETS and "get_budgets" not in new_tf:
                new_tf.append("get_budgets")
            if new_tf != tf:
                if not dry_run:
                    pb.tool_filter = new_tf
                    flag_modified(pb, "tool_filter")
                changed.append(f"{slug} filter: {new_tf}")
            ins = pb.instructions or ""
            new_ins = _swap_text(ins)
            if slug in ADD_BUDGETS and "get_budgets" not in new_ins:
                new_ins = new_ins.rstrip() + BUDGET_NOTE
            if slug in ADD_BUDGETS and "trading day (a" not in new_ins:
                new_ins = new_ins.rstrip() + DAILY_NOTE
            if new_ins != ins:
                if not dry_run:
                    pb.instructions = new_ins
                changed.append(
                    f"{slug} instructions: raw names → _for_period twins"
                    + (" + budget note" if slug in ADD_BUDGETS else "")
                )
        if not dry_run:
            db.commit()
        print("DRY RUN — would apply:" if dry_run else "Applied:")
        for line in changed or ["  (nothing to do)"]:
            print(f"  {line}")
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
