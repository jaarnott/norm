"""Install recipe drafting: the draft_recipe tool, the rewritten skill, the
Kitchen App claim, and the record_recipe fix (11 Oct 2026).

Why (production thread aad94383, 2–4 Oct 2026): a chef and the agent spent
$30 and 64 model-minutes putting 14 recipes into Loaded. Half the agent's
``record_recipe`` saves failed on names and units it resolved itself, every
failure cost an approval round trip, and the recipe cards it "opened for
review" showed nothing (the card ignored its working document). The chef's
rules — component or dish, prefixes and capitals, methods from the source,
check what exists first — were taught mid-job and partly lost.

What this installs:

1. ``loadedhub.draft_recipe`` — a built-in (app/agents/internal_tools.py)
   that takes recipes as written and opens one recipe-editor card per
   recipe, lines matched by Norm (app/services/recipe_draft.py). The row's
   ``working_document.items_path`` fans ``data.recipes`` out into one
   document and one card each. The chef's Save on the card is the approval.
2. The skill, renamed ``create_recipes`` (was ``create_recipe_from_ingredients``)
   and rewritten around the card, with the direct ``record_recipe`` write kept
   for "no card, just save it".
3. The Loaded Kitchen App claims the tool (the App Map is exposure).
4. ``cook_brothers_app.record_recipe``: an edit without ``version_id`` now
   reads the current version instead of being refused as a create.

**Run AFTER the matching code is deployed** — the tool's handler and the
card's working-document loading ship with it. The config DB is shared with
production, so a row installed early would name a handler production
doesn't have yet.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_recipe_drafting.py --dry-run
    uv run python scripts/sync_recipe_drafting.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.tool_row_updates import RowUpdate, apply_row_updates, code_of  # noqa: E402

CONNECTOR = "loadedhub"
ACTION = "draft_recipe"
APP_SLUG = "loaded-kitchen"
PLAYBOOK_SLUG = "create_recipes"
#: The skill's name until 11 Oct 2026 — it described the old flow (one recipe,
#: typed in). The row is renamed in place, so its id and history stay.
OLD_PLAYBOOK_SLUG = "create_recipe_from_ingredients"

DRAFT_RECIPE = {
    "action": ACTION,
    "method": "GET",  # read path: the handler's result becomes the working docs
    "read_only": True,
    "effect": "draft",  # the card's Save is the approval (spec_rows.EFFECTS)
    "description": (
        "Open NEW recipes as recipe-editor cards for the chef to finish and "
        "save — THE way to create recipes. Send each recipe as written (name, "
        "component or dish, yield, method, ingredient lines with quantity and "
        "unit as printed); Norm matches every line to the venue's stock items, "
        "sub-recipes and units, and leaves anything unsure for the chef on the "
        "card. Nothing is written to Loaded until the chef presses Save, so this "
        "needs no approval. Several recipes in one call open one card each; "
        "draft and have components saved before the dishes that use them. Use "
        "record_recipe only when the user asks to save without a card."
    ),
    "required_fields": ["venue", "recipes"],
    "optional_fields": [],
    "field_descriptions": {
        "venue": "The venue name.",
        "recipes": "The recipes, as written in the source.",
    },
    "field_schema": {
        "venue": {"type": "string"},
        "recipes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "In the venue's convention, e.g. 'COMPONENT - LEMON BUTTER'.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["component", "dish"],
                        "description": "component: a prep recipe used in others, counted in stocktake. dish: a plated menu item.",
                    },
                    "yield_quantity": {"type": "number"},
                    "yield_unit": {
                        "type": "string",
                        "description": "As written (kg, g, L, ml, each, serve).",
                    },
                    "method": {
                        "type": "string",
                        "description": "The method exactly as the source has it, as simple HTML (<ol><li>…).",
                    },
                    "lines": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "As written.",
                                },
                                "quantity": {
                                    "type": "string",
                                    "description": "As written ('500', '1/2', 'to taste').",
                                },
                                "unit": {
                                    "type": "string",
                                    "description": "As written.",
                                },
                                "kind": {
                                    "type": "string",
                                    "enum": ["item", "recipe"],
                                    "description": "Only when you know it is a stock item or a sub-recipe.",
                                },
                                "ref_id": {
                                    "type": "string",
                                    "description": "Only when you already have the item or sub-recipe id.",
                                },
                            },
                            "required": ["name"],
                        },
                    },
                },
                "required": ["name", "lines"],
            },
        },
    },
    "working_document": {
        "doc_type": "recipe",  # the same doc_type the Recipes page uses
        "sync_mode": "submit",
        "ref_fields": ["draft_id"],  # one document per draft
        "items_path": "recipes",  # one card per recipe
    },
    "display_component": "recipe_editor",
    "display_props": {"title": "New recipe"},
}

PLAYBOOK = {
    "display_name": "Create recipes in Loaded",
    "description": (
        "Turn a document, a message or a dictated recipe into new Loaded "
        "recipes: components first, every line matched by Norm, each recipe "
        "opened as a card the chef finishes and saves."
    ),
    "instructions": """Goal: the recipes the user gave you (a document, a message, dictation) become saved Loaded recipes, built the way this kitchen builds them, with as little back-and-forth as possible.

**1. Read the whole source and take out every recipe.** A page can hold several; a dish often carries its components inline ("for the sauce: …"). Each is one of two things: a COMPONENT (a prep recipe used inside others — sauces, rubs, doughs, purées; it yields a weight or volume and is counted in stocktake) or a DISH (a plated menu item; it yields 1 serve and is not counted). Never create anything that looks like a stock item.

**2. Learn the venue's conventions before naming anything.** Call get_recipes once and look at how names are built: prefixes ("COMPONENT - ", "DISH - "), capitals, how a dish names its components. Follow what you see; if the venue has no pattern, use "COMPONENT - NAME" and "DISH - NAME" in capitals. Check the list for a recipe with the same name — if one exists, say so and ask whether to edit it (edit_recipe) rather than draft a second one. Never change a recipe the user didn't ask about.

**3. Draft with draft_recipe — components first.** Send each recipe exactly as written: name, kind, yield, method as simple HTML (<ol><li>…), and every line with its quantity and unit as printed. Do not resolve ids or rename ingredients yourself; Norm matches each line to the venue's stock items, sub-recipes and units and leaves anything unsure for the chef. A dish line that names a component from the same batch stays unresolved until that component is saved — so draft the components, let the chef save them, then draft the dishes (or re-draft a dish once its components exist).

**4. Methods are copied, never written.** Put the source's method on every recipe, step for step. If the source has no method, leave it blank and say so — don't invent one. "To taste" and "as needed" are not quantities; leave them as written and the card asks the chef for a number.

**5. Then say what needs the chef, briefly.** Each card is open below. Report only the lines Norm flagged (a candidate to pick, a stock item that doesn't exist, a missing quantity) and any recipe that already exists. Don't repeat the drafts in text. The chef can create a missing stock item from the card; if the user asks YOU to create items, use manage_stock_item (names in CAPITALS, no supplier) and then re-draft.

**6. Saving.** The chef's Save on a card is the approval — nothing reaches Loaded until then, so never say a recipe is saved until you've seen it: the card's draft document carries `status: saved` and the new recipe_id once the chef saves, and get_recipes shows it. When the user explicitly asks you to save without a card ("just create it"), use record_recipe instead: it needs Loaded's own unit names (get_stock view 'reference' kind 'units') and exact item names, it asks for approval, and it returns the new recipe's id.""",
}

APP_NOTE = (
    "Recipes, menus and stock items live in Loaded. New recipes: draft_recipe "
    "opens each one as a card the chef completes and saves (no approval card); "
    "record_recipe writes one directly when asked to skip the card. An edit "
    "needs the recipe id from get_recipes."
)


def install_tool_and_claims(db, dry_run: bool) -> list[str]:
    """The tool row, the skill text and the App claim — same transaction."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec, MarketplaceApp, Playbook

    lines = []
    spec = (
        db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == CONNECTOR)
        .with_for_update()
        .one()
    )
    live = spec_rows.find_tool(spec, ACTION)
    if live != DRAFT_RECIPE:
        lines.append(f"{CONNECTOR}.{ACTION}: {'update' if live else 'add'} tool row")
        if not dry_run:
            spec.tools = spec_rows.upsert(spec.tools, [DRAFT_RECIPE])
            spec.version = (spec.version or 0) + 1
            flag_modified(spec, "tools")

    pb = (
        db.query(Playbook)
        .filter(Playbook.slug.in_([PLAYBOOK_SLUG, OLD_PLAYBOOK_SLUG]))
        .with_for_update()
        .all()
    )
    if not pb:
        raise SystemExit(f"playbook {PLAYBOOK_SLUG} not found")
    pb = sorted(pb, key=lambda p: p.slug != PLAYBOOK_SLUG)[0]  # the renamed one first
    if pb.slug != PLAYBOOK_SLUG:
        lines.append(f"playbook {pb.slug}: rename to {PLAYBOOK_SLUG}")
        if not dry_run:
            pb.slug = PLAYBOOK_SLUG
    changed = [k for k, v in PLAYBOOK.items() if getattr(pb, k) != v]
    if changed:
        lines.append(f"playbook {PLAYBOOK_SLUG}: update {', '.join(changed)}")
        if not dry_run:
            for k, v in PLAYBOOK.items():
                setattr(pb, k, v)

    app = (
        db.query(MarketplaceApp)
        .filter(MarketplaceApp.slug == APP_SLUG)
        .with_for_update()
        .first()
    )
    if app is None:
        raise SystemExit(f"marketplace app {APP_SLUG} not found")
    comp = dict(app.composition or {})
    tools = list(comp.get("tools") or [])
    key = f"{CONNECTOR}.{ACTION}"
    want = (
        tools
        if key in tools
        else tools[: tools.index("loadedhub.edit_recipe")]
        + [key]
        + tools[tools.index("loadedhub.edit_recipe") :]
        if "loadedhub.edit_recipe" in tools
        else tools + [key]
    )
    skills = list(comp.get("skills") or [])
    want_skills = [PLAYBOOK_SLUG if s == OLD_PLAYBOOK_SLUG else s for s in skills]
    if PLAYBOOK_SLUG not in want_skills:
        want_skills.append(PLAYBOOK_SLUG)
    if want != tools or want_skills != skills or comp.get("note") != APP_NOTE:
        lines.append(f"app {APP_SLUG}: claim {key} and {PLAYBOOK_SLUG}, note")
        if not dry_run:
            comp["tools"] = want
            comp["skills"] = want_skills
            comp["note"] = APP_NOTE
            app.composition = comp
            flag_modified(app, "composition")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    apply_row_updates(
        [
            RowUpdate(
                "cook_brothers_app", "record_recipe", code=code_of("record_recipe.py")
            )
        ],
        dry_run=args.dry_run,
        label="recipe-drafting",
        also=install_tool_and_claims,
    )


if __name__ == "__main__":
    main()
