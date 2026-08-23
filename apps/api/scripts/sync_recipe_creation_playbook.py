"""Create the executive_chef "Create a Recipe in Loaded" playbook.

Triggered when the user hands Norm a recipe — a list of ingredients and
instructions — to be built in LoadedHub. The flow the instructions encode:
parse the recipe, match every ingredient to a stock item (or sub-recipe) and
every unit to a Loaded unit, ask the user to confirm anything unclear, save via
the Cook Brothers App write (one-tap approval), then bring the saved recipe up
in the recipe editor card.

Hard-won facts baked into the instructions (all verified live on Bessie &
Engineers, 20 Aug 2026):
- lines are ``{kind: 'item'|'recipe', name, ref_id, unit_id, quantity}`` — kind
  is NOT 'stock_item', and name is required;
- yield_unit_id must be a canonical base unit (Kilo/Litre/Each) — gram-fraction
  units like "grams" are rejected, so a weight yield is expressed in Kilo;
- a successful create can return ``created: true`` with NULL ids, so the new
  recipe must be re-found by name before it can be opened.

Idempotent — safe to re-run. The config DB is shared across every environment,
so committing reaches production. Dry-run first.

Usage:
    .venv/bin/python scripts/sync_recipe_creation_playbook.py --dry-run
    .venv/bin/python scripts/sync_recipe_creation_playbook.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

PLAYBOOK = {
    "slug": "create_recipe_from_ingredients",
    "agent_slug": "executive_chef",
    "display_name": "Create a Recipe in Loaded",
    "description": (
        "Build a new LoadedHub recipe from a list of ingredients and "
        "instructions the user provides: match every ingredient to a stock "
        "item and every unit to a Loaded unit (asking about anything unclear), "
        "save it, and open it in the recipe editor."
    ),
    "instructions": """Goal: turn the recipe the user just gave you (ingredients + instructions) into a saved LoadedHub recipe, then open it for review.

1. **Parse what they gave you.** Extract: recipe name, yield (how much it makes), each ingredient with quantity and unit, and the method. If the name is missing, look at get_all_recipes for the venue's naming convention (e.g. "COMPONENT - X", "ENTREE - X") and propose one — ask, don't invent silently. If the yield is missing, ask for it.

2. **Fetch the venue's catalogue.** Call get_stock_items and get_stock_units, and get_all_recipes — an ingredient may be a prepped component (e.g. "charred leeks") that should link to a SUB-RECIPE rather than a raw stock item.

3. **Match every ingredient and unit.**
   - Ingredient → a stock item id (kind "item") or a recipe id (kind "recipe"). Match case-insensitively and fuzzily ("flour" → "FLOUR PLAIN").
   - Unit → a unit id from get_stock_units; the line quantity is in that unit's DISPLAY units.
   - **If ANY ingredient or unit has no confident match** (nothing close, or several plausible candidates): show your best 2–3 suggestions for each unclear one and ASK the user to confirm before saving. Never guess silently and never fabricate an id. Confident matches don't need questions — present them in the summary table instead.

4. **Show the recipe you're about to create** — name, yield, a table of lines (ingredient → matched stock item/sub-recipe, quantity, unit), method — then save it with kitchen_loadedhub_update_recipe: create=true, name, yield_quantity, yield_unit_id, notes (the method as simple HTML, e.g. <ol><li>…), and lines as objects {kind, name, ref_id, unit_id, quantity}. Rules that matter: kind is 'item' or 'recipe' (never 'stock_item'); every line needs its name; yield_quantity and quantities are JSON numbers; yield_unit_id must be a canonical base unit (Kilo, Litre or Each — gram-fraction units are rejected, so a 300 g yield is 0.3 Kilo). The save is a write — the user approves it before it executes.

5. **Open the saved recipe.** The create's response may return null ids — call get_all_recipes (or search the result) to find the new recipe by name and get its id. Then call edit_recipe with that recipe_id, the venue_id and changes {} — this brings the recipe up in the recipe editor card. Tell the user it's open below for review, and that any further tweaks can be made on the card or by asking you.

Never claim the recipe is saved before the approved write has returned success, and never skip the confirmation step for unclear matches.""",
    "tool_filter": [
        "get_stock_items",
        "get_stock_units",
        "get_all_recipes",
        "get_recipe_details",
        "kitchen_loadedhub_update_recipe",
        "edit_recipe",
    ],
    "enabled": True,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.db.config_models import Playbook
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        row = db.query(Playbook).filter(Playbook.slug == PLAYBOOK["slug"]).first()
        if row:
            changed = any(
                getattr(row, k) != v for k, v in PLAYBOOK.items() if k != "slug"
            )
            if not changed:
                print(f"{PLAYBOOK['slug']}: already up to date")
                return
            if args.dry_run:
                print(f"{PLAYBOOK['slug']}: WOULD update")
                return
            for k, v in PLAYBOOK.items():
                setattr(row, k, v)
            print(f"{PLAYBOOK['slug']}: updated")
        else:
            if args.dry_run:
                print(f"{PLAYBOOK['slug']}: WOULD create")
                return
            db.add(Playbook(**PLAYBOOK))
            print(f"{PLAYBOOK['slug']}: created")
        db.commit()
        print("committed")
    finally:
        db.close()


if __name__ == "__main__":
    main()
