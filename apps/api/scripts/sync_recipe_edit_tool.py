"""Add the `edit_recipe` tool to the loadedhub spec and enable it for executive_chef.

`edit_recipe` lets the agent change the recipe the user has OPEN in the editor.
It is an INTERNAL tool (handler in ``app/agents/internal_tools.py``) that never
calls LoadedHub — it merges the change into the SAME working document the Recipes
page is showing (``doc_type="recipe"``, keyed by ``recipe_id`` + venue), and the
page's poll reflects it. The user presses Save on the card to write to Loaded.

Why ``method: "GET"``: tool_loop routes GET tools through the read path, where the
handler's RESULT payload becomes the working-document data. A write method would
store the raw LLM params instead. There is no HTTP request — the registered
handler wins over the spec's (empty) HTTP config.

Idempotent; the config DB is shared across every environment, so committing
reaches production. Dry-run first.

Usage:
    .venv/bin/python scripts/sync_recipe_edit_tool.py --dry-run
    .venv/bin/python scripts/sync_recipe_edit_tool.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "loadedhub"
AGENT = "executive_chef"
ACTION = "edit_recipe"
CAP_LABEL = "Edit the recipe the user has open in the editor."

EDIT_RECIPE = {
    "action": ACTION,
    "method": "GET",  # read path -> the handler's result becomes the working doc
    "description": (
        "Open a recipe as an editable Recipe card and apply changes to the draft. "
        "Works from ANY page — if the recipe isn't already open it loads it, so "
        "never tell the user to open a recipe first; call this and the card "
        "appears. If the user has a recipe open ('Open Recipe' context), use that "
        "recipe_id; otherwise use the id from get_all_recipes. It does NOT save "
        "to LoadedHub (the user presses Save on the card for that). Put only the "
        "fields you are changing in `changes`; they merge into the draft. Address "
        "a line by its id or by a `match` on its name (e.g. 'salt'). You CAN "
        "change a line's unit: set unit_id + unit_name (from the units read) and "
        "give quantity in the NEW unit's display units."
    ),
    "required_fields": ["recipe_id", "venue_id", "changes"],
    "field_schema": {
        "recipe_id": {
            "type": "string",
            "description": "The open recipe's id (from the Open Recipe context).",
        },
        "venue_id": {
            "type": "string",
            "description": "The open recipe's venue id (from the Open Recipe context).",
        },
        "changes": {
            "type": "object",
            "description": (
                "The edit to apply, merged into the open draft. Scalar fields: "
                "name, notes (the method), yield_quantity. Line edits under `lines`."
            ),
            "properties": {
                "name": {"type": "string", "description": "Rename the recipe."},
                "notes": {"type": "string", "description": "Replace the method/notes."},
                "yield_quantity": {"type": "number", "description": "Change the yield amount."},
                "yield_unit_id": {"type": "string", "description": "Change the yield unit (id from the units read)."},
                "lines": {
                    "type": "object",
                    "properties": {
                        "update": {
                            "type": "array",
                            "description": (
                                "Change existing lines. Each item identifies the line "
                                "by `id` OR `match` (case-insensitive name substring) "
                                "and puts the new values in `set`."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "match": {
                                        "type": "string",
                                        "description": "Ingredient-name match, e.g. 'salt'.",
                                    },
                                    "set": {
                                        "type": "object",
                                        "description": (
                                            'New values, e.g. {"quantity": 5} or a unit change '
                                            '{"unit_id": "...", "unit_name": "Each", "quantity": 8}.'
                                        ),
                                        "properties": {
                                            "quantity": {
                                                "type": "number",
                                                "description": "In the line's unit (the NEW unit if changing it).",
                                            },
                                            "name": {"type": "string"},
                                            "unit_id": {
                                                "type": "string",
                                                "description": "New unit id (from the units read).",
                                            },
                                            "unit_name": {
                                                "type": "string",
                                                "description": "New unit's name — set together with unit_id so the card shows it.",
                                            },
                                        },
                                    },
                                },
                            },
                        },
                        "remove": {
                            "type": "array",
                            "description": "Remove lines. Each item: {id} or {match}.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "match": {"type": "string"},
                                },
                            },
                        },
                        "add": {
                            "type": "array",
                            "description": (
                                "Add lines. A real Save needs the item's ref_id and "
                                "unit_id, so only add when you have them."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "kind": {"type": "string", "enum": ["item", "recipe"]},
                                    "name": {"type": "string", "description": "Item/sub-recipe name."},
                                    "ref_id": {"type": "string", "description": "Stock item or sub-recipe id."},
                                    "unit_id": {"type": "string", "description": "Unit id (from the units read)."},
                                    "unit_name": {"type": "string", "description": "Unit name, so the card shows it."},
                                    "quantity": {"type": "number", "description": "In the unit's display units."},
                                },
                            },
                        },
                    },
                },
            },
        },
    },
    "working_document": {
        "doc_type": "recipe",  # SAME doc_type the Recipes page creates
        "sync_mode": "submit",
        "ref_fields": ["recipe_id"],  # venue_id auto-stamped from the tool result
    },
    "display_component": "recipe_editor",
    "display_props": {"title": "Recipe"},
    "read_only": True,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import AgentConnectionBinding, ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec:
            sys.exit(f"No connector spec named {CONNECTOR}")

        tools = list(spec.tools or [])
        idx = next((i for i, t in enumerate(tools) if t.get("action") == ACTION), None)
        tool_changed = idx is None or tools[idx] != EDIT_RECIPE
        if tool_changed:
            if idx is not None:
                tools[idx] = EDIT_RECIPE
            else:
                tools.append(EDIT_RECIPE)
            print(f"spec {CONNECTOR}: {'update' if idx is not None else 'add'} {ACTION}")
        else:
            print(f"spec {CONNECTOR}: {ACTION} already up to date")

        binding = (
            db.query(AgentConnectionBinding)
            .filter(
                AgentConnectionBinding.agent_slug == AGENT,
                AgentConnectionBinding.connector_name == CONNECTOR,
            )
            .first()
        )
        cap_changed = False
        caps = list(binding.capabilities or []) if binding else []
        if binding is None:
            print(f"WARNING: no {AGENT} binding for {CONNECTOR} — run sync_executive_chef_agent first")
        elif ACTION not in {c.get("action") for c in caps}:
            caps.append({"action": ACTION, "label": CAP_LABEL, "enabled": True})
            cap_changed = True
            print(f"binding {AGENT}/{CONNECTOR}: + {ACTION}")
        else:
            print(f"binding {AGENT}/{CONNECTOR}: already enables {ACTION}")

        if not tool_changed and not cap_changed:
            print("nothing to do")
            return
        if args.dry_run:
            print("(dry run — nothing written)")
            return

        if tool_changed:
            spec.tools = tools
            spec.version = (spec.version or 0) + 1
            flag_modified(spec, "tools")
        if cap_changed and binding is not None:
            binding.capabilities = caps
            flag_modified(binding, "capabilities")
        db.commit()
        print("committed")
    finally:
        db.close()


if __name__ == "__main__":
    main()
