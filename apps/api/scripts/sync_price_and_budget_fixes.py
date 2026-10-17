"""Install the two tool fixes from the insights research (17 Oct 2026).

  loadedhub.get_received_items_for_period   code: unit_cost_change_pct is a
                                            PERCENT within one invoiced unit
                                            (was a fraction, across units),
                                            plus last_price_change; and the
                                            one phrase of its description
                                            that says so
  loadedhub.get_sales                       code: an ex-GST budget loses the
                                            share of tax the sales carried, so
                                            "vs budget" reads the same on both
                                            bases
  dashboard template procurement-cogs       the price-movers column shows the
                                            percent as it comes (it multiplied
                                            the old fraction by 100)

Deliberately surgical. The full installers (sync_sales_config.py,
sync_purchase_cogs_config.py) rewrite whole rows from the repo, and on 17 Oct
that would have reverted newer live config: get_purchase_cogs' chunked sales
read, get_sales' live description and its ``effect``, and a re-created
procurement-dashboard template. Here only the code, one phrase and one column
format change; the dry run (the default) prints exactly that.

Usage:
    uv run python scripts/sync_price_and_budget_fixes.py            # dry run
    uv run python scripts/sync_price_and_budget_fixes.py --apply
"""

from __future__ import annotations

import difflib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "loadedhub"
CODE = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
CODE_FILES = {
    "get_received_items_for_period": CODE / "received_items_for_period.py",
    "get_sales": CODE / "get_sales.py",
}
OLD_PHRASE = (
    "'item_supplier' (each supplier's unit cost avg/min/max/last — 'are we paying "
    "different prices')"
)
NEW_PHRASE = (
    "'item_supplier' (each supplier's unit cost avg/min/max/last; "
    "unit_cost_change_pct is the move first to last in PERCENT (25.0 = up 25%); "
    "last_price_change is the latest step, with its date — 'are we paying "
    "different prices', 'what's gone up')"
)
TEMPLATE = "procurement-cogs"
FIELD = "unit_cost_change_pct"
OLD_FORMAT = {"type": "percent"}
NEW_FORMAT = {"type": "number", "decimals": 1, "suffix": "%"}


def _diff(a: str, b: str, name: str) -> str:
    lines = difflib.unified_diff(
        a.splitlines(), b.splitlines(), f"live {name}", f"repo {name}", lineterm="", n=1
    )
    return "\n".join(lines)


def main(apply: bool) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectorSpec, DashboardTemplate
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    changes: list[str] = []
    try:
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == CONNECTOR)
            .one()
        )
        tools = [dict(t) for t in spec.tools or []]
        for i, tool in enumerate(tools):
            action = tool.get("action")
            if action not in CODE_FILES:
                continue
            cc = dict(tool.get("consolidator_config") or {})
            live, repo = cc.get("function_code") or "", CODE_FILES[action].read_text()
            if live != repo:
                print(_diff(live, repo, action))
                cc["function_code"] = repo
                tool["consolidator_config"] = cc
                changes.append(f"{action}: code")
            if action == "get_received_items_for_period":
                desc = tool.get("description") or ""
                if OLD_PHRASE in desc:
                    tool["description"] = desc.replace(OLD_PHRASE, NEW_PHRASE)
                    changes.append(f"{action}: description phrase")
                elif NEW_PHRASE not in desc:
                    changes.append(
                        f"{action}: description phrase NOT found — left as it is"
                    )
            tools[i] = tool
        spec.tools = tools
        flag_modified(spec, "tools")

        row = db.query(DashboardTemplate).filter_by(slug=TEMPLATE).first()
        if row is None:
            changes.append(f"template {TEMPLATE} not found — skipped")
        else:
            charts = [dict(c) for c in row.charts or []]
            for c in charts:
                spec_ = dict(c.get("chart_spec") or {})
                formats = dict(spec_.get("field_formats") or {})
                if formats.get(FIELD) == OLD_FORMAT:
                    formats[FIELD] = NEW_FORMAT
                    spec_["field_formats"] = formats
                    c["chart_spec"] = spec_
                    changes.append(f"template {TEMPLATE}: '{c.get('title')}' format")
            row.charts = charts
            flag_modified(row, "charts")

        for c in changes:
            print(("Would apply: " if not apply else "Applied: ") + c)
        if not changes:
            print("Nothing to do — already installed.")
        if apply and changes:
            db.commit()
        else:
            db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
