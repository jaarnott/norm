"""Component-API rows for the menu editor.

The menu editor reaches LoadedHub through ``callComponentApi('menu_editor',
<action>)`` (web) / ``norm__component_api`` (MCP), each resolving one of these
ComponentApiConfig rows:

- ``list_menus``   — the menu picker (full menus with sections + lines).
- ``list_recipes`` — recipe autocomplete for a menu line (a line links a recipe).
- ``create_menu``  — submit a NEW menu (POST); body is the MenuModel the editor sends.
- ``update_menu``  — submit an EDITED menu (PUT /{id}); body is the MenuModel.

Menus are on the core-api host ``loadedhub.com/api`` (the one Norm already writes
to); recipes are on the stock-api host ``api.loadedhub.com/1.0``. Both accept the
loadedhub OAuth connector token + ``x-loaded-company-id`` header.

create_menu/update_menu leave request_body_template empty on purpose: the
component-API executor falls back to sending the raw params as the JSON body, so
the editor passes the whole MenuModel as params. update_menu's path pulls the id
from that same object (``{{ id }}``).

Reads use a pass-through response mapping (empty) — the raw Loaded field names
(id/name/groups/lines/workingPrice/recipeId/stockItemId/lineOrder) are exactly
what the editor reads; confirmed live against La Zeppa.

Idempotent; config DB is shared, so committing reaches production. Dry-run first.

Usage:
    .venv/bin/python scripts/sync_menu_component_apis.py --dry-run
    .venv/bin/python scripts/sync_menu_component_apis.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

COMPONENT = "menu_editor"
CONNECTOR = "loadedhub"

HEADERS = {
    "Content-Type": "application/json",
    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
}


def _row(action_name, label, method, path, *, required=None, body="", mapping=None):
    required = required or []
    return {
        "action_name": action_name,
        "display_label": label,
        "method": method,
        "path_template": path,
        "request_body_template": body,
        "headers": dict(HEADERS),
        "required_fields": required,
        "field_mapping": {k: k for k in required},
        "field_descriptions": {},
        "ref_fields": {},
        "id_field": None,
        "response_field_mapping": mapping or {},
        "enabled": True,
    }


ROWS = [
    _row(
        "list_menus",
        "Menu list",
        "GET",
        "//loadedhub.com/api/stock/menus?includeLines=true",
    ),
    _row(
        # Same path the working get_all_recipes tool uses — the /1.0 host 403s on
        # anything else (wrong path/audience). Returns id + name + currentVersion,
        # which is all a menu-line recipe autocomplete needs.
        "list_recipes",
        "Recipe list",
        "GET",
        "//api.loadedhub.com/1.0//stock/internal/recipes",
    ),
    _row(
        # Stock item prices — the menu editor costs each dish (recipe cost vs sell
        # price) and needs currentPrice + counting-unit ratios. Same host/path the
        # recipe editor uses.
        "list_stock_items",
        "Stock item list",
        "GET",
        "//loadedhub.com/api/StockItems",
    ),
    _row(
        # Unit ratios + types, for the cost engine (see recipeCost.ts).
        "list_units",
        "Unit list",
        "GET",
        "//api.loadedhub.com/1.0/stock/internal/units?includeDeleted=true",
    ),
    _row(
        "create_menu",
        "Create menu",
        "POST",
        "//loadedhub.com/api/stock/menus",
    ),
    _row(
        "update_menu",
        "Update menu",
        "PUT",
        "//loadedhub.com/api/stock/menus/{{ id }}",
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
