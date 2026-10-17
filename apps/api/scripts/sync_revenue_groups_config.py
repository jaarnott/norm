"""Install POS revenue groups: the one tool, set_revenue_group (Oct 2026).

Loaded's Settings → Company → Revenue Groups page puts each POS item in Food,
Beverage or Other; an item in a group reports UNDER it, which is what makes food
vs beverage sales and cost of goods right at a venue whose POS categories are
thin. Plan, and what was checked against Loaded's source and test environment:
docs/plans/pos-revenue-groups.md.

What this puts in the shared config DB, idempotently, on the loadedhub spec:

  endpoints  get_pos_items_raw                GET  /api/pos/items
             set_pos_items_revenue_group_raw  PUT  /api/pos/items/revenue-group
  tool       set_revenue_group  write, asks first; each person may switch it
             to "always allow", after which it just runs
             (config/consolidators/set_revenue_group.py)

ONE tool (owner, 17 Oct 2026): the read is get_sales' items breakdown, which
already shows each item's group and category by sales — no second tool.

The App claim (Loaded Reports → set_revenue_group) lives in sync_marketplace_catalog.py —
run it AFTER this, since its validator wants the tool rows to exist.

Usage:
    .venv/bin/python scripts/sync_revenue_groups_config.py --dry-run
    .venv/bin/python scripts/sync_revenue_groups_config.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "loadedhub"
_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"

_HEADERS = {
    "Content-Type": "application/json",
    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
}

ENDPOINTS = [
    {
        "action": "get_pos_items_raw",
        "method": "GET",
        "path_template": "//loadedhub.com/api/pos/items",
        "headers": _HEADERS,
        "required_fields": [],
        "field_mapping": {},
        "field_descriptions": {},
        "request_body_template": "",
        "success_status_codes": [200],
        "timeout_seconds": 60,
        "engine_only": True,
        "read_only": True,
        "description": (
            "[consolidator-only] Every POS item at the venue (deleted ones carry "
            "whenDeleted): posIdentifier, name, price, priceEx, itemGroupName, "
            "itemCategoryName, revenueGroup. ~270 KB at 781 items. Read by "
            "set_revenue_group; never bind to an agent."
        ),
    },
    {
        "action": "set_pos_items_revenue_group_raw",
        "method": "PUT",
        "path_template": "//loadedhub.com/api/pos/items/revenue-group",
        "headers": _HEADERS,
        "required_fields": ["body"],
        "field_mapping": {"body": "body"},
        "field_descriptions": {
            "body": '{"posItemIds": [...], "revenueGroupName": "Food" | "Beverage" | "Other" | null}'
        },
        "request_body_template": "{{ body | tojson }}",
        "success_status_codes": [200, 204],
        "timeout_seconds": 60,
        "engine_only": True,
        "read_only": False,
        "description": (
            "[consolidator-only] Put POS items in a revenue group (null clears). "
            "Loaded saves the ids one at a time and 500s on an id it doesn't know, "
            "so send only ids just read. A write — called by set_revenue_group; "
            "never bind to an agent."
        ),
    },
]

WRITE_TOOL = {
    "action": "set_revenue_group",
    # PUT: the method the human-approval gate used to key on; effect decides now.
    "method": "PUT",
    "effect": "write",
    "read_only": False,
    "approval": {
        "default": "ask",
        "allow_auto": True,
        "label": "set revenue groups on POS items",
    },
    "description": (
        "Put POS items in a revenue group — Food, Beverage or Other — or back "
        "to Unassigned. Loaded then reports them under that group, which is "
        "what makes food vs beverage sales and cost of goods right. Find them "
        "with get_sales breakdown 'items' (each row's group and category; "
        "category 'Uncategorised' lists the ones to sort, biggest sellers first) "
        "and pass the item names as shown — every live item with that name "
        "moves. Or give a pos_category (only its unassigned items unless "
        "only_unassigned=false). Several groups in one call: assignments "
        "[{revenue_group, items}] — one approval for a whole pass. Don't ask the "
        "person first: the approval asks when it needs to, and runs straight "
        "away for someone who has switched it to always allow. A name that "
        "matches nothing stops the call and returns the closest names."
    ),
    "required_fields": ["venue"],
    "optional_fields": [
        "revenue_group",
        "items",
        "pos_category",
        "only_unassigned",
        "assignments",
    ],
    "field_descriptions": {
        "venue": "The venue name.",
        "revenue_group": "Food, Beverage, Other, or Unassigned to clear.",
        "items": "Item names exactly as get_sales shows them (or posIdentifiers).",
        "pos_category": "Every item in this POS category.",
        "only_unassigned": "With pos_category, default true: items already in a group stay put.",
        "assignments": "Several groups at once: [{revenue_group, items | pos_category}].",
    },
    "field_schema": {
        "revenue_group": {
            "type": "string",
            "enum": ["Food", "Beverage", "Other", "Unassigned"],
        },
        "items": {"type": "array", "items": {"type": "string"}},
        "only_unassigned": {"type": "boolean"},
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "revenue_group": {
                        "type": "string",
                        "enum": ["Food", "Beverage", "Other", "Unassigned"],
                    },
                    "items": {"type": "array", "items": {"type": "string"}},
                    "pos_category": {"type": "string"},
                    "only_unassigned": {"type": "boolean"},
                },
                "required": ["revenue_group"],
            },
        },
    },
    "consolidator_config": {
        # read + one PUT per group per 200 items + read back
        "max_api_calls": 20,
        "allowed_write_actions": ["set_pos_items_revenue_group_raw"],
    },
}

TOOLS = [(WRITE_TOOL, "set_revenue_group.py")]


def _tool_row(tool: dict, filename: str) -> dict:
    row = dict(tool)
    row["consolidator_config"] = {
        **tool["consolidator_config"],
        "function_code": (_DIR / filename).read_text(encoding="utf-8"),
    }
    return row


def plan(spec) -> tuple[list[dict], list[dict], list[str]]:
    """The spec's (tools, endpoints) with this install applied, and what changed."""
    from app.connectors import spec_rows

    changes: list[str] = []
    endpoints = list(spec_rows.endpoints(spec))
    have = {e.get("action"): e for e in endpoints}
    for ep in ENDPOINTS:
        live = have.get(ep["action"])
        merged = {**(live or {}), **ep}
        if live != merged:
            changes.append(f"{'updated' if live else 'added'} endpoint {ep['action']}")
    endpoints = spec_rows.upsert(
        endpoints, [{**(have.get(ep["action"]) or {}), **ep} for ep in ENDPOINTS]
    )

    tools = list(spec_rows.tools(spec))
    have_t = {t.get("action"): t for t in tools}
    rows = []
    for tool, filename in TOOLS:
        live = have_t.get(tool["action"])
        merged = {**(live or {}), **_tool_row(tool, filename)}
        if live != merged:
            changes.append(f"{'updated' if live else 'added'} tool {tool['action']}")
        rows.append(merged)
    tools = spec_rows.upsert(tools, rows)
    return tools, endpoints, changes


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec:
            raise SystemExit(f"{CONNECTOR} ConnectionSpec not found in config DB")
        if not spec_rows.is_split(spec):
            raise SystemExit(f"{CONNECTOR} is not split into tools and endpoints")

        tools, endpoints, changes = plan(spec)
        if not changes:
            print("Already in sync.")
            db.rollback()
            return
        verb = "Would apply" if dry_run else "Applied"
        if dry_run:
            db.rollback()
        else:
            # Endpoints first: the tools listener routes an endpoint row it is
            # handed into `endpoints`, so the explicit list must be in place.
            spec.endpoints = endpoints
            flag_modified(spec, "endpoints")
            spec.tools = tools
            flag_modified(spec, "tools")
            db.commit()
        for line in changes:
            print(f"{verb}: {line}")
        if not dry_run:
            from app.services.config_validator import validate_config

            summary = validate_config(config_db=db)
            print(f"validate_config: {summary}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    main(dry_run=parser.parse_args().dry_run)
