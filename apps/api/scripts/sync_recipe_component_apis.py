"""Component-API rows for the recipe editor.

The recipe editor reaches LoadedHub through ``callComponentApi('recipe_editor',
<action>)`` (web) / ``norm__component_api`` (MCP). All of these are DIRECT
loadedhub reads — per the design, only the recipe *write* goes through the Cook
Brothers App (``kitchen_loadedhub_update_recipe``); everything else is direct:

- ``list_recipes``     — the recipe picker (id + name + currentVersion).
- ``get_recipe``       — one recipe with its versions and ingredient lines.
- ``list_stock_items`` — ingredient (stock item) autocomplete.
- ``list_units``       — unit options for a line / the yield.

Paths are the same ones the working loadedhub tools use (recipes/units on the
stock-api host ``api.loadedhub.com/1.0``, stock items on the core-api host
``loadedhub.com/api``). Reads pass through raw — the editor consumes Loaded's
own field names (currentVersion.id, lines[].itemId|recipeId|unitId|quantity|
unitRatio), which it maps to the CB update payload on save.

Idempotent; config DB is shared, so committing reaches production. Dry-run first.

Usage:
    .venv/bin/python scripts/sync_recipe_component_apis.py --dry-run
    .venv/bin/python scripts/sync_recipe_component_apis.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

COMPONENT = "recipe_editor"
CONNECTOR = "loadedhub"

HEADERS = {
    "Content-Type": "application/json",
    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
}


def _row(action_name, label, path, *, required=None):
    required = required or []
    return {
        "action_name": action_name,
        "display_label": label,
        "method": "GET",
        "path_template": path,
        "request_body_template": "",
        "headers": dict(HEADERS),
        "required_fields": required,
        "field_mapping": {k: k for k in required},
        "field_descriptions": {},
        "ref_fields": {},
        "id_field": None,
        "response_field_mapping": {},
        "enabled": True,
    }


ROWS = [
    _row(
        "list_recipes",
        "Recipe list",
        "//api.loadedhub.com/1.0//stock/internal/recipes",
    ),
    _row(
        "get_recipe",
        "Recipe detail",
        "//api.loadedhub.com/1.0/stock/internal/recipes/{{ recipe_id }}",
        required=["recipe_id"],
    ),
    _row(
        "list_stock_items",
        "Stock items",
        "//loadedhub.com/api/StockItems",
    ),
    _row(
        "list_units",
        "Units",
        "//api.loadedhub.com/1.0/stock/internal/units?includeDeleted=true",
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.db.config_models import ComponentApiConfig
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        changed = []
        for row in ROWS:
            existing = (
                db.query(ComponentApiConfig)
                .filter(
                    ComponentApiConfig.component_key == COMPONENT,
                    ComponentApiConfig.connector_name == CONNECTOR,
                    ComponentApiConfig.action_name == row["action_name"],
                )
                .first()
            )
            if existing:
                dirty = [k for k, v in row.items() if getattr(existing, k, None) != v]
                if not dirty:
                    print(f"  = {row['action_name']}: already up to date")
                    continue
                changed.append(f"update {row['action_name']} ({', '.join(dirty)})")
                if not args.dry_run:
                    for k, v in row.items():
                        setattr(existing, k, v)
            else:
                changed.append(f"add {row['action_name']}")
                if not args.dry_run:
                    db.add(
                        ComponentApiConfig(
                            component_key=COMPONENT,
                            connector_name=CONNECTOR,
                            **row,
                        )
                    )

        if not changed:
            print("nothing to do")
            return
        if args.dry_run:
            print("WOULD: " + "; ".join(changed))
            return
        db.commit()
        print("applied: " + "; ".join(changed))
    finally:
        db.close()


if __name__ == "__main__":
    main()
