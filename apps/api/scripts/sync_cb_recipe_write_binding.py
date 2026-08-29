"""Point executive_chef's recipe write at the CB App's CURRENT tool.

History: the original binding targeted ``kitchen_loadedhub_update_recipe``.
On 29 Aug 2026 the Cook Brothers App consolidated its tool surface (114 → 45
tools) and the recipe write became ``kitchen_record_recipe`` — create-or-edit
by presence of ``recipe_id`` (+ ``version_id``, which despite its description
is REQUIRED for updates), ingredients as ``{kind, name, quantity, unit}`` with
name-based resolution (``unit`` accepts a unit name or GUID). Re-discovery for
the tenders work replaced the spec's tool list, silently orphaning the old
binding cap — the chef lost recipe writes and the editor's Save broke until
``recipe_save.SAVE_ACTION`` and this binding were updated together.

This script enforces the end state: the chef's cook_brothers_app binding
carries ``kitchen_record_recipe`` (and no dead caps), and the recipe-creation
playbook's tool_filter + instructions match the new tool.

Idempotent — safe to re-run. The config DB is shared across every environment,
so committing reaches production. Dry-run first.

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
ACTION = "kitchen_record_recipe"
DEAD_ACTIONS = {"kitchen_loadedhub_update_recipe"}
CAP_LABEL = "Create or edit a recipe and save it to LoadedHub (approve-gated)."

PLAYBOOK_SLUG = "create_recipe_from_ingredients"
PLAYBOOK_TOOL_FILTER = [
    "get_stock_items",
    "get_stock_units",
    "get_recipes",
    "kitchen_record_recipe",
    "edit_recipe",
]
PLAYBOOK_INSTRUCTIONS = """Goal: turn the recipe the user just gave you (ingredients + instructions) into a saved LoadedHub recipe, then open it for review.

1. **Parse what they gave you.** Extract: recipe name, yield (how much it makes), each ingredient with quantity and unit, and the method. If the name is missing, look at get_recipes for the venue's naming convention (e.g. "COMPONENT - X", "ENTREE - X") and propose one — ask, don't invent silently. If the yield is missing, ask for it.

2. **Sanity-check the ingredients.** kitchen_record_recipe resolves ingredients BY NAME (stock items, sub-recipes and units are matched for you), so you don't need ids — but check get_stock_items / get_recipes for anything ambiguous ("flour" when the venue stocks three flours, or a prepped component that should be a sub-recipe). **If a match is genuinely unclear, show your best 2–3 candidates and ASK the user to confirm before saving.** Confident matches don't need questions.

3. **Show the recipe you're about to create** — name, yield, ingredient table (name, quantity, unit, item vs sub-recipe), method — then save with kitchen_record_recipe: name, yield_quantity, yield_unit (unit name is fine), notes (the method as simple HTML, e.g. <ol><li>…), and ingredients as objects {kind: 'item'|'recipe', name, quantity, unit}. The save is a write — the user approves it before it executes. To EDIT an existing recipe instead: pass recipe_id AND version_id (from the recipe read) — both are required.

4. **Open the saved recipe.** The create's response may return null ids — find the new recipe by name via get_recipes to get its id, then call edit_recipe with that recipe_id, the venue_id and changes {} — this brings the recipe up in the recipe editor card. Tell the user it's open below for review.

Never claim the recipe is saved before the approved write has returned success, and never skip the confirmation step for unclear matches."""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import AgentConnectionBinding, ConnectionSpec, Playbook
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        changes = []

        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec or not any(t.get("action") == ACTION for t in (spec.tools or [])):
            sys.exit(f"{CONNECTOR} has no {ACTION} tool — run sync-mcp-tools first")

        # --- binding: swap dead cap for the live tool -----------------------
        binding = (
            db.query(AgentConnectionBinding)
            .filter(
                AgentConnectionBinding.agent_slug == AGENT,
                AgentConnectionBinding.connector_name == CONNECTOR,
            )
            .first()
        )
        caps = [
            c for c in (binding.capabilities or []) if c.get("action") not in DEAD_ACTIONS
        ] if binding else []
        removed = binding is not None and len(caps) != len(binding.capabilities or [])
        if removed:
            changes.append(f"binding: remove dead cap(s) {sorted(DEAD_ACTIONS)}")
        if ACTION not in {c.get("action") for c in caps}:
            caps.append({"action": ACTION, "label": CAP_LABEL, "enabled": True})
            changes.append(f"binding: + {ACTION}")
        if changes and not args.dry_run:
            if binding is None:
                db.add(
                    AgentConnectionBinding(
                        agent_slug=AGENT,
                        connector_name=CONNECTOR,
                        capabilities=caps,
                        enabled=True,
                    )
                )
            else:
                binding.capabilities = caps
                flag_modified(binding, "capabilities")

        # --- playbook: tool filter + instructions ---------------------------
        pb = db.query(Playbook).filter(Playbook.slug == PLAYBOOK_SLUG).first()
        if pb:
            if pb.tool_filter != PLAYBOOK_TOOL_FILTER:
                changes.append("playbook: tool_filter -> kitchen_record_recipe set")
                if not args.dry_run:
                    pb.tool_filter = PLAYBOOK_TOOL_FILTER
            if pb.instructions != PLAYBOOK_INSTRUCTIONS:
                changes.append("playbook: instructions rewritten for the new tool")
                if not args.dry_run:
                    pb.instructions = PLAYBOOK_INSTRUCTIONS

        if not changes:
            print("everything up to date")
            return
        for c in changes:
            print(f"  {c}")
        if args.dry_run:
            print("(dry run — nothing written)")
            return
        db.commit()
        print("committed")
    finally:
        db.close()


if __name__ == "__main__":
    main()
