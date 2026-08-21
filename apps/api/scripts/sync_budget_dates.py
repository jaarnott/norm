"""One budget surface: get_budgets becomes a consolidator; the raw endpoint
is demoted to [consolidator-only].

Loaded's ``/api/budgets`` returns each budget dated one day AFTER the day it
belongs to (confirmed 21 Aug 2026: the venue's Thursday $22k rode Friday's
date, and the whole curve only aligns with the venue's sales once shifted
back a day — the Saturday budget peak had been landing on Sunday). The
serialization looks innocent (clean venue-local midnights), which is why it
survived: every value was well-formed, just filed under tomorrow.

This sync makes ``loadedhub.get_budgets`` a consolidator
(config/consolidators/get_budgets.py): corrected dates with the weekday
alongside, exact range coverage (the API filters to-exclusively over the
shifted instants, so [F, T] needs the query [F, T+1]), plus totals, weekly
subtotals and explicit gaps — one budget tool instead of a raw endpoint every
report re-interprets. The raw HTTP action survives as ``get_budgets_raw``,
[consolidator-only], never bound to an agent; the
``calculate_template_stock_requirements`` consolidator calls it directly
(sandboxed consolidators cannot nest) and is re-synced by its own script.

Config-only: no API deploy required. Bindings keep working — the action name
``get_budgets`` is unchanged.

Usage:
    uv run python scripts/sync_budget_dates.py [--dry-run]
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, ".")

FUNCTION_CODE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "get_budgets.py"
)

RAW_TOOL = {
    "action": "get_budgets_raw",
    "method": "GET",
    "description": (
        "[consolidator-only] Loaded's raw budgets endpoint — dates each "
        "budget one day AFTER the day it belongs to, and the from/to filter "
        "works on those shifted instants. Call get_budgets instead; never "
        "bind this to an agent."
    ),
    "path_template": (
        "//loadedhub.com/api/budgets?from={{ from_date }}&to={{ to_date }}"
    ),
    "headers": {
        "Content-Type": "application/json",
        "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
    },
    "required_fields": ["from_date", "to_date"],
    "field_mapping": {"from_date": "from", "to_date": "to"},
    "field_descriptions": {
        "from_date": "Query start YYYY-MM-DD (raw Loaded semantics)",
        "to_date": "Query end YYYY-MM-DD, exclusive over shifted instants",
    },
    "request_body_template": "",
    "success_status_codes": [200],
    "response_ref_path": "",
    "timeout_seconds": 30,
    "read_only": True,
}


def budgets_tool() -> dict:
    return {
        "action": "get_budgets",
        "method": "GET",  # deliberate: consolidator dispatch auto-executes
        "description": (
            "Daily budgets for a date range, corrected to the day each "
            "budget is FOR (the source dates them one day late) with the "
            "weekday alongside, plus range total, Mon-Sun weekly subtotals "
            "and any days with no budget set. THE one budget tool — use this "
            "for every budget question."
        ),
        "required_fields": ["from_date"],
        "optional_fields": ["to_date"],
        "field_descriptions": {
            "from_date": "Start date YYYY-MM-DD",
            "to_date": (
                "End date YYYY-MM-DD, inclusive (default: from_date + 6 — one week)"
            ),
        },
        "read_only": True,
        "consolidator_config": {
            "function_code": FUNCTION_CODE_PATH.read_text(),
            "max_api_calls": 2,
        },
    }


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == "loadedhub")
            .first()
        )
        if not spec:
            raise SystemExit("loadedhub ConnectorSpec not found in config DB")
        tools = list(spec.tools or [])
        changed = []
        for tool in (RAW_TOOL, budgets_tool()):
            idx = next(
                (i for i, t in enumerate(tools) if t.get("action") == tool["action"]),
                None,
            )
            if idx is None:
                tools.append(tool)
                changed.append(f"added {tool['action']}")
            elif tools[idx] != tool:
                tools[idx] = tool
                changed.append(f"updated {tool['action']}")
        if not changed:
            print("Already in sync.")
            return
        if dry_run:
            print("Would apply:", "; ".join(changed))
            return
        spec.tools = tools
        flag_modified(spec, "tools")
        db.commit()
        print("Applied:", "; ".join(changed))
        print(
            "Remember: scripts/sync_stock_requirements_config.py must also "
            "run (calculate_template_stock_requirements now calls "
            "get_budgets_raw)."
        )
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
