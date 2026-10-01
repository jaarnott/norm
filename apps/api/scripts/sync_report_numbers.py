"""Stage 3 of the 1 Oct 2026 consolidator review: numbers that read wrong.

- loadedhub.get_budgets — a failed Loaded read is an error, not a $0 budget
  (get_sales' compare=budget reported $0 from it); amounts are labelled as
  including GST, and tax 'exclude' divides by 1 + each day's rate; part-weeks
  are marked; $0 days are listed.
- loadedhub.get_labour — the timeclock view sums worked and leave hours
  separately (leave was inside "hours"); group totals carry leave; staff_name
  works on a venues='all' answer and vs_actual refuses it instead of ignoring
  it; an unknown group_by is an error, not empty rows.
- loadedhub.get_cogs_detail_for_period — the description no longer tells the
  model to pass venue='all' (all 11 such calls failed) or to split by month,
  and says revenue is EXCLUDING GST; quantities are rounded.

Only the keys named below change (scripts/tool_row_updates.py); a JSON backup
of the rows is written first.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_report_numbers.py --dry-run
    uv run python scripts/sync_report_numbers.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.tool_row_updates import (  # noqa: E402 — path set above
    RowUpdate,
    apply_row_updates,
    code_of,
    load_script,
    shapes_of,
)


def updates() -> list[RowUpdate]:
    budgets = load_script("sync_budget_dates").budgets_tool()
    labour = load_script("sync_labour_config").build_tool()
    fp = load_script("sync_for_period_config")
    cogs_returns = next(
        r for a, *_, r in fp.WRAPPED if a == "get_cogs_detail_for_period"
    )
    keys = ("description", "optional_fields", "field_descriptions", "field_schema")
    return [
        RowUpdate(
            "loadedhub",
            "get_budgets",
            {k: budgets[k] for k in keys if k in budgets},
            code=code_of("get_budgets.py"),
        ),
        RowUpdate(
            "loadedhub",
            "get_labour",
            {k: labour[k] for k in keys if k in labour},
            code=code_of("get_labour.py"),
            shapes=shapes_of("loadedhub.get_labour"),
        ),
        RowUpdate(
            "loadedhub",
            "get_cogs_detail_for_period",
            {"description": fp.description_for(cogs_returns)},
            shapes=shapes_of("loadedhub.get_cogs_detail_for_period"),
        ),
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    apply_row_updates(updates(), dry_run=args.dry_run, label="report-numbers")


if __name__ == "__main__":
    main()
