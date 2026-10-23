"""Move the Apps' standing rules out of Norm's prompt and into their tools.

Norm's prompt carried a "## Your Apps" section: one short note per App that is
on. It was capped at 1,500 characters, the notes came to about 1,680 and
nothing ordered them, so for Cook Brothers the Functions rule ("never quote a
minimum spend, price or date you haven't read") was cut off (23 Oct 2026). A
tool's description reaches Norm on every turn while the tool is available, so
each rule now sits on the tool it governs, uncapped, and the section is
removed (agent_config_service.build_unified_prompt). Owner's decision 23 Oct,
docs/plans/agents-and-apps-restructure.md §6.

The other notes were already said by their tools: get_stock ("Never guess an
item id"), get_purchase_cogs, draft_recipe / record_recipe / edit_recipe, and
open_app (Norm Hiring, Training and Performance with their inputs).

The same sentences are in each tool's installer (RULES names it), so a re-run
keeps them; tests/test_app_builder_style.py checks the two agree. Idempotent.
The config DB is shared by every environment: dry-run first.

Usage (from apps/api, .env loaded, DATABASE_URL at a migrated database —
validate_config reads the app database):
    uv run python scripts/sync_app_rules_into_tools.py --dry-run
    uv run python scripts/sync_app_rules_into_tools.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.tool_row_updates import RowUpdate, apply_row_updates  # noqa: E402

#: (connector, action) -> (the rule, the installer that defines the tool)
RULES = {
    ("norm_functions", "get_venue_profile"): (
        "Never quote a minimum spend, price or date you haven't read from "
        "Functions; drafts and proposals are free, and a customer email or "
        "proposal goes only from an approval card. Functions is worked with "
        "these norm_functions tools, not open_app.",
        "sync_norm_functions",
    ),
    ("xero", "get_bills"): (
        "Never quote an amount, due date or account you haven't read from the "
        "xero tools. If a tool says a venue isn't connected to Xero, show the "
        "connect card (norm.show_connect, connector_name 'xero').",
        "sync_xero_connector",
    ),
    ("loadedhub", "get_labour"): (
        "Answer roster questions grouped by day and sorted by start time: "
        "staff, role, start–end, hours.",
        "sync_labour_config",
    ),
    ("norm", "save_app"): (
        "Before building or changing one, open its skill: build_a_dashboard "
        "for a dashboard, build_an_app for an app.",
        "sync_dashboard_tools",
    ),
}


def updates(specs: dict) -> list[RowUpdate]:
    from app.connectors import spec_rows

    out = []
    for (connector, action), (rule, _) in RULES.items():
        live = spec_rows.find_tool(specs.get(connector), action)
        if live is None:
            raise SystemExit(f"{connector}.{action} is not a tool row")
        desc = (live.get("description") or "").rstrip()
        if rule not in desc:
            desc = f"{desc} {rule}"
        out.append(RowUpdate(connector, action, fields={"description": desc}))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    apply_row_updates(updates, dry_run=args.dry_run, label="app-rules-into-tools")


if __name__ == "__main__":
    main()
