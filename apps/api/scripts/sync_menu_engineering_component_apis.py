"""Component-API rows for the Menu Engineering page.

The Menu Engineering view (executive_chef agent) plots each sold product on a
popularity (units) x profitability (margin %) grid — Stars / Plow Horses /
Puzzles / Dogs — and lists them in a table that links through to the recipe.

Its data is Loaded's Cost-of-Goods PRODUCTS report: per POS item, the units sold,
revenue (ex tax), cost and discounts, already computed by Loaded and priced Live.
That's a DIRECT loadedhub read; the recipe-name -> recipeId map it needs for the
click-through comes from the existing ``menu_editor/list_menus`` rows, so nothing
else is added here.

    get_cogs_detail — //loadedhub.com/api/cogs/products/detail (per-product COGS).

Idempotent; config DB is shared, so committing reaches production. Dry-run first.

Usage:
    .venv/bin/python scripts/sync_menu_engineering_component_apis.py --dry-run
    .venv/bin/python scripts/sync_menu_engineering_component_apis.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

COMPONENT = "menu_engineering"
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
    # Loaded's Cost-of-Goods per-product report — one row per POS item with
    # quantitySold, salesExcludeTax (revenue), cost and discounts, priced Live.
    _row(
        "get_cogs_detail",
        "COGS by product",
        "//loadedhub.com/api//cogs/products/detail"
        "?start={{ start }}&end={{ end }}&priceType={{ price_type | default('Live') }}",
        required=["start", "end"],
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
