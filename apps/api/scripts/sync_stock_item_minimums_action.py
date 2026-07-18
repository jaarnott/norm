"""Ensure the loadedhub `get_stock_item_minimums` connector action exists.

`calculate_template_stock_requirements` needs each item's par level (minimum
stock on hand) plus the unit ratios to convert it into counting units. The
existing `get_stock_items` action hits the legacy /StockItems endpoint and its
transform carries no minimum fields, so this adds a dedicated action against
api.loadedhub.com/1.0/stock/internal/items exposing only what the consolidator
needs. Like the connector spec generally, the action lives in the config DB —
the "config blind spot" (docs/architecture.md §13) — so this script is the
reviewed, version-controlled source for it.

Idempotent — safe to re-run; reports whether anything changed.

Usage:
    .venv/bin/python scripts/sync_stock_item_minimums_action.py --dry-run
    .venv/bin/python scripts/sync_stock_item_minimums_action.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "loadedhub"
ACTION = "get_stock_item_minimums"

TOOL = {
    "action": ACTION,
    "description": (
        "List stock items with their configured minimum stock-on-hand (par) "
        "level and the unit ratios needed to convert that minimum into counting "
        "units. Used by calculate_template_stock_requirements to top items up to "
        "par. Read-only."
    ),
    "method": "GET",
    "path_template": "//api.loadedhub.com/1.0/stock/internal/items",
    "headers": {
        "Content-Type": "application/json",
        "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
    },
    "required_fields": [],
    "field_mapping": {},
    "request_body_template": "",
    "success_status_codes": [200],
    "response_ref_path": "",
    "timeout_seconds": 30,
    "response_transform": {
        "enabled": True,
        "fields": {
            "id": "id",
            "name": "itemName",
            "countingUnitRatio": "countingUnitRatio",
            "minimumStockOnHandQuantity": "minQty",
            # Nested: the min is stored in its own unit; grab that unit's ratio
            # so the consolidator can convert it to counting units.
            "minimumStockOnHandUnit.ratio": "minUnitRatio",
        },
        "flatten": [],
        # Drop soft-deleted items.
        "filters": [{"field": "datestampRemoved", "operator": "is_empty", "value": ""}],
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec:
            sys.exit(f"No connector spec named {CONNECTOR}")

        tools = list(spec.tools or [])
        idx = next((i for i, t in enumerate(tools) if t.get("action") == ACTION), None)

        if idx is not None and tools[idx] == TOOL:
            print(f"{ACTION}: already up to date")
            return

        verb = "update" if idx is not None else "add"
        if args.dry_run:
            print(f"{ACTION}: WOULD {verb} the action")
            return

        if idx is not None:
            tools[idx] = TOOL
        else:
            tools.append(TOOL)
        spec.tools = tools
        spec.version = (spec.version or 0) + 1
        db.commit()
        print(f"{ACTION}: {verb}ed, spec version -> {spec.version}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
