"""Install `cook_brothers_app.record_recipe` — the tool that replaces Orbit's raw
recipe function on the agent's menu.

Sep 2026 rule (docs/tool-architecture-strategy.md): an Orbit MCP function is an
API endpoint and an LLM only ever sees tools. `kitchen_record_recipe` was the
last endpoint any App claimed (Loaded Kitchen). This installs the consolidator
wrapper (config/consolidators/record_recipe.py) and moves every reference over:

1. the tool row on cook_brothers_app's ``tools`` list (the connector must be
   split first — on an unsplit MCP connector the admin "sync tools" button
   replaces the whole list and would wipe the wrapper);
2. the Loaded Kitchen App's claim (composition.tools);
3. the executive_chef binding capability (unattended-task scope);
4. the create_recipe_from_ingredients playbook's instructions.

The endpoint stays — the recipe editor (app/services/recipe_save.py) calls it.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_orbit_recipe_wrapper.py --dry-run
    uv run python scripts/sync_orbit_recipe_wrapper.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

CONNECTOR = "cook_brothers_app"
ENDPOINT = "kitchen_record_recipe"
TOOL = "record_recipe"
APP = "loaded-kitchen"
BINDING_AGENT = "executive_chef"
PLAYBOOK = "create_recipe_from_ingredients"
CODE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "config/consolidators/record_recipe.py"
)


#: What the model is told (consolidator review, 1 Oct 2026). Orbit's own
#: description reads as "edit recipes here", but an edit WITH the user is
#: edit_recipe's job (a card the user saves) — this is the direct write.
DESCRIPTION = (
    "Create a NEW recipe in LoadedHub, or write a change to an existing one "
    "directly. To edit a recipe with the user, use edit_recipe instead — it "
    "opens the recipe card and the user saves. Ingredients may be given by "
    "name — stock items, sub-recipes and units are resolved for you. When "
    "editing an existing recipe's ingredients you MUST pass mode: 'merge' "
    "changes or adds the lines you send and keeps the rest; 'replace' makes "
    "your list the whole recipe. Allergen statuses upsert only what you send. "
    "This is a write — human-approved."
)

#: Orbit's own input schema for kitchen_record_recipe (from its MCP listing,
#: 1 Oct 2026). Without it every field reached the model as a string, and
#: Orbit (additionalProperties false, typed lines) rejected the JSON text.
FIELD_SCHEMA = {
    "yield_quantity": {"type": "number"},
    "is_counted_in_stocktake": {"type": "boolean"},
    "mode": {"type": "string", "enum": ["replace", "merge"]},
    "ingredients": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["item", "recipe"],
                    "description": "'item' for a stock item, 'recipe' for a sub-recipe",
                },
                "name": {
                    "type": "string",
                    "description": "Stock item or sub-recipe name (resolved for you when ref_id is omitted)",
                },
                "ref_id": {
                    "type": "string",
                    "description": "Stock item id or sub-recipe id, if you already know it",
                },
                "line_id": {
                    "type": "string",
                    "description": "Existing line id, when editing a specific line",
                },
                "quantity": {
                    "type": "number",
                    "description": "Quantity in display units of `unit`",
                },
                "unit": {
                    "type": "string",
                    "description": "Unit name or unit id for the quantity",
                },
                "remove": {
                    "type": "boolean",
                    "description": "In merge mode, drop this line instead of keeping it",
                },
            },
            "required": ["kind", "unit", "quantity"],
        },
    },
    "allergens": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["contains", "no", "cmw", "mc"],
                    "description": "contains | no | cmw (can be made without) | mc (may contain)",
                },
                "notes": {"type": "string"},
            },
            "required": ["code", "status"],
        },
    },
}

MODE_DESCRIPTION = (
    "How to apply `ingredients`: 'merge' (change/add the lines you send, keep "
    "the rest) or 'replace' (your list becomes the whole recipe). Required "
    "when editing an existing recipe's ingredients."
)


def tool_row(endpoint: dict) -> dict:
    drop = {"venue_id"}
    return {
        "action": TOOL,
        "method": "POST",  # a write: the tool loop asks for approval first
        "read_only": False,
        "description": DESCRIPTION,
        "required_fields": [
            f for f in endpoint.get("required_fields") or [] if f not in drop
        ],
        "optional_fields": [
            f for f in endpoint.get("optional_fields") or [] if f not in drop
        ],
        "field_descriptions": {
            **{
                k: v
                for k, v in (endpoint.get("field_descriptions") or {}).items()
                if k not in drop
            },
            "mode": MODE_DESCRIPTION,
        },
        "field_schema": FIELD_SCHEMA,
        "consolidator_config": {
            "function_code": CODE.read_text(),
            # An edit reads the recipe for its approval preview, then writes.
            "max_api_calls": 2,
            "allowed_write_actions": [f"{CONNECTOR}.{ENDPOINT}"],
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import (
        AgentConnectionBinding,
        ConnectionSpec,
        MarketplaceApp,
        Playbook,
    )
    from app.db.engine import _ConfigSessionLocal
    from app.services.config_validator import validate_config

    import app.agents.internal_tools  # noqa: F401 — registers built-ins

    db = _ConfigSessionLocal()
    changes: list[str] = []
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter_by(connector_name=CONNECTOR)
            .with_for_update()
            .one()
        )
        if not spec_rows.is_split(spec):
            raise SystemExit(
                f"{CONNECTOR} is not split yet — run "
                f"scripts/sync_split_connector_endpoints.py --connector {CONNECTOR} first"
            )
        endpoint = spec_rows.find_endpoint(spec, ENDPOINT)
        if endpoint is None:
            raise SystemExit(f"{CONNECTOR}.{ENDPOINT} endpoint not found")

        # 1. the tool
        row = tool_row(endpoint)
        existing = spec_rows.find_tool(spec, TOOL)
        if (
            existing is None
            or {k: v for k, v in existing.items() if k != "added_at"} != row
        ):
            changes.append(
                f"{'update' if existing else 'install'} tool {CONNECTOR}.{TOOL}"
            )
            if not args.dry_run:
                spec.tools = spec_rows.upsert(spec.tools, [row])
                flag_modified(spec, "tools")
                spec.version = (spec.version or 0) + 1

        # 2. the App claim
        app_row = db.query(MarketplaceApp).filter_by(slug=APP).one()
        comp = dict(app_row.composition or {})
        claimed = list(comp.get("tools") or [])
        old_key, new_key = f"{CONNECTOR}.{ENDPOINT}", f"{CONNECTOR}.{TOOL}"
        if old_key in claimed or new_key not in claimed:
            claimed = [new_key if k == old_key else k for k in claimed]
            if new_key not in claimed:
                claimed.append(new_key)
            changes.append(f"{APP} claims {new_key} instead of {old_key}")
            if not args.dry_run:
                comp["tools"] = claimed
                app_row.composition = comp
                flag_modified(app_row, "composition")

        # 3. the legacy binding (unattended-task scope unions binding actions)
        for b in db.query(AgentConnectionBinding).filter_by(
            agent_slug=BINDING_AGENT, connector_name=CONNECTOR
        ):
            caps = [dict(c) if isinstance(c, dict) else c for c in b.capabilities or []]
            touched = False
            for c in caps:
                if isinstance(c, dict) and c.get("action") == ENDPOINT:
                    c["action"] = TOOL
                    touched = True
            if touched:
                changes.append(
                    f"binding {BINDING_AGENT}/{CONNECTOR}: {ENDPOINT} -> {TOOL}"
                )
                if not args.dry_run:
                    b.capabilities = caps
                    flag_modified(b, "capabilities")

        # 4. the playbook that names it
        pb = db.query(Playbook).filter_by(slug=PLAYBOOK).first()
        if pb and ENDPOINT in (pb.instructions or ""):
            changes.append(f"playbook {PLAYBOOK}: names {TOOL}")
            if not args.dry_run:
                pb.instructions = pb.instructions.replace(ENDPOINT, TOOL)

        for c in changes or ["nothing to do"]:
            print(("[dry-run] " if args.dry_run else "") + c)
        if args.dry_run:
            db.rollback()
            return
        db.commit()
        summary = validate_config(config_db=db)
        issues = summary.get("issues") or []
        print(
            f"validate_config: {'ok' if summary.get('ok') else str(len(issues)) + ' issue(s)'}"
        )
        for i in issues[:10]:
            print(f"   {i.get('severity')}: {i.get('where')} — {i.get('problem')}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
