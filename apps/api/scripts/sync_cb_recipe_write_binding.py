"""Bind the Cook Brothers App recipe-write tool to executive_chef (config only).

executive_chef could read recipes but not WRITE them. The only recipe writer is the
CB App MCP tool ``kitchen_loadedhub_update_recipe`` (Loaded's own recipe-write API
is on the legacy /wapi host Norm's OAuth can't reach — confirmed: a direct OAuth
POST to /1.0/stock/internal/recipes returns 403). The CB App was updated to resolve
the venue from the authenticated connection, so Norm can call it even though the
tool loop strips venue/venue_name/venue_id from arguments before every connector
call (tool_loop.py) — verified live end to end (a no-venue_id create reaches
Loaded's own content validation).

This script (config DB only):
  1. On the cook_brothers_app spec's ``kitchen_loadedhub_update_recipe``: clear
     ``required_fields`` (so a CREATE can omit recipe_id/version_id — the CB tool
     makes the recipe + its first version) and keep them documented as optional;
     append a create-vs-update note to the description; mark it a write.
  2. Upserts an executive_chef -> cook_brothers_app binding enabling the tool.

Durability caveat: ``POST /connector-specs/cook_brothers_app/sync-mcp-tools`` does
NOT preserve required_fields (connector_specs.py), so a re-discovery reverts the
override — re-run this script afterwards. (method IS preserved.)

Idempotent. The config DB is shared across every environment, so committing reaches
production. Dry-run first.

Usage:
    .venv/bin/python scripts/sync_cb_recipe_write_binding.py --dry-run
    .venv/bin/python scripts/sync_cb_recipe_write_binding.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "cook_brothers_app"
AGENT = "executive_chef"
ACTION = "kitchen_loadedhub_update_recipe"
CAP_LABEL = "Create or edit a recipe and save it to LoadedHub."

_MARKER = "To CREATE a new recipe:"
_CREATE_NOTE = (
    " To CREATE a new recipe: OMIT recipe_id and version_id and provide name, "
    "yield_quantity, yield_unit_id and at least one line. To UPDATE an existing "
    "recipe: recipe_id AND version_id are BOTH required — get version_id from "
    "get_recipe_details first; without it the tool takes the CREATE branch and "
    "fails. yield_unit_id must be a canonical base unit (e.g. Kilo, Litre, Each) "
    "— gram-fraction units like 'grams' are rejected by Loaded, so express a "
    "weight yield in Kilo (0.3 = 300g). This is a write — describe what you'll "
    "save and let the user approve; it applies on approval."
)

# The CB tool's own `lines` description is bare ("Full replacement list of
# ingredient lines"), so the model guesses the line shape wrong (e.g. kind
# 'stock_item', no name) and the CB App rejects it. Spell out the real schema —
# verified live: kind is 'item'|'recipe' and every line needs a name.
_LINES_FD = (
    "The full ingredient list — REPLACES the version's lines, so send every line "
    "each time. Each line is an object {kind, name, ref_id, unit_id, quantity}: "
    "kind is 'item' for a stock item or 'recipe' for a sub-recipe (NOT "
    "'stock_item'); name is the item/recipe name (required); ref_id is its id; "
    "unit_id is the line's unit id; quantity is in that unit's DISPLAY units. Get "
    "item ids + units from the stock reads, recipe ids from get_all_recipes."
)

# Without an explicit field_schema, build_input_schema types EVERY field as
# string (prompt_builder.py — "the only way to express nested objects or
# arrays"), so the model sent yield_quantity as "300" and lines as a JSON
# string, and the CB server's own validation rejected both (expected number /
# expected array, received string). This schema is the fix: real types for the
# non-string fields, and the verified line shape as array items.
_FIELD_SCHEMA = {
    "recipe_id": {"type": "string", "description": "LoadedHub recipe id (omit to create)."},
    "version_id": {"type": "string", "description": "Version to update (omit to create)."},
    "create": {"type": "boolean", "description": "true to create a new recipe (with recipe_id/version_id omitted)."},
    "name": {"type": "string", "description": "Recipe name (required for a create)."},
    "notes": {"type": "string", "description": "Recipe method / notes (HTML)."},
    "is_counted_in_stocktake": {"type": "boolean", "description": "Whether the recipe is counted in stocktakes."},
    "yield_quantity": {"type": "number", "description": "Yield amount, in display units of yield_unit_id."},
    "yield_unit_id": {"type": "string", "description": "Yield unit id (from the units read)."},
    "lines": {
        "type": "array",
        "description": _LINES_FD,
        "items": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["item", "recipe"], "description": "'item' for a stock item, 'recipe' for a sub-recipe."},
                "name": {"type": "string", "description": "The item/recipe name — required."},
                "ref_id": {"type": "string", "description": "The stock item or sub-recipe id."},
                "unit_id": {"type": "string", "description": "The line's unit id."},
                "quantity": {"type": "number", "description": "Quantity in the unit's display units."},
            },
            "required": ["kind", "name", "ref_id", "unit_id", "quantity"],
        },
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import AgentConnectorBinding, ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        # --- 1. spec tool -----------------------------------------------------
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec:
            sys.exit(f"No connector spec named {CONNECTOR}")
        tools = list(spec.tools or [])
        idx = next((i for i, t in enumerate(tools) if t.get("action") == ACTION), None)
        if idx is None:
            sys.exit(
                f"spec {CONNECTOR} has no {ACTION} tool — run sync-mcp-tools first"
            )
        tool = dict(tools[idx])

        spec_changed = False
        if tool.get("required_fields"):
            optional = list(tool.get("optional_fields") or [])
            for f in tool["required_fields"]:
                if f not in optional:
                    optional.append(f)
            tool["optional_fields"] = optional
            tool["required_fields"] = []
            spec_changed = True
        # Replace from the marker so a revised note supersedes the old one
        # instead of being skipped because the marker already exists.
        desc = tool.get("description") or ""
        cut = desc.find(_MARKER)
        base = desc[:cut].rstrip() if cut >= 0 else desc.rstrip()
        want_desc = base + _CREATE_NOTE
        if desc != want_desc:
            tool["description"] = want_desc
            spec_changed = True
        if tool.get("read_only") is not False:
            tool["read_only"] = False
            spec_changed = True
        fd = dict(tool.get("field_descriptions") or {})
        if fd.get("lines") != _LINES_FD:
            fd["lines"] = _LINES_FD
            tool["field_descriptions"] = fd
            spec_changed = True
        if tool.get("field_schema") != _FIELD_SCHEMA:
            tool["field_schema"] = _FIELD_SCHEMA
            spec_changed = True
        # build_input_schema only emits fields listed in required+optional, so
        # `create` must be declared to be sendable at all.
        optional = list(tool.get("optional_fields") or [])
        if "create" not in optional:
            optional.append("create")
            tool["optional_fields"] = optional
            spec_changed = True

        print(
            f"spec {CONNECTOR}: {'update' if spec_changed else 'no change to'} {ACTION}"
        )

        # --- 2. binding -------------------------------------------------------
        cap = {"action": ACTION, "label": CAP_LABEL, "enabled": True}
        binding = (
            db.query(AgentConnectorBinding)
            .filter(
                AgentConnectorBinding.agent_slug == AGENT,
                AgentConnectorBinding.connector_name == CONNECTOR,
            )
            .first()
        )
        if binding is None:
            bind_changed = True
            caps = [cap]
            print(f"binding {AGENT}/{CONNECTOR}: create + enable {ACTION}")
        else:
            caps = list(binding.capabilities or [])
            if ACTION in {c.get("action") for c in caps}:
                bind_changed = False
                print(f"binding {AGENT}/{CONNECTOR}: already enables {ACTION}")
            else:
                caps.append(cap)
                bind_changed = True
                print(f"binding {AGENT}/{CONNECTOR}: + {ACTION}")

        if not spec_changed and not bind_changed:
            print("nothing to do")
            return
        if args.dry_run:
            print("(dry run — nothing written)")
            return

        if spec_changed:
            tools[idx] = tool
            spec.tools = tools
            spec.version = (spec.version or 0) + 1
            flag_modified(spec, "tools")
        if binding is None:
            db.add(
                AgentConnectorBinding(
                    agent_slug=AGENT,
                    connector_name=CONNECTOR,
                    capabilities=caps,
                    enabled=True,
                )
            )
        elif bind_changed:
            binding.capabilities = caps
            binding.enabled = True
            flag_modified(binding, "capabilities")
        db.commit()
        print("committed")
    finally:
        db.close()


if __name__ == "__main__":
    main()
