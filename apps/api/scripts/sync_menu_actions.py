"""Add menu read + write actions to the loadedhub connector spec.

Menus live on Loaded's core-api host — ``loadedhub.com/api/stock/menus`` — the
same ``loadedhub.com/api`` base the roster tools already use, so Norm's existing
OAuth connector token reaches them (unlike recipe writes, which are stuck on the
legacy ``/wapi`` host). Endpoints and the ``MenuModel`` shape are taken from
Loaded's core-api (``StockMenusController`` / ``MenuModel`` / ``MenuLineModel``):

    GET    /stock/menus?includeLines=true      -> MenuModel[]
    GET    /stock/menus/{id}                    -> MenuModel
    POST   /stock/menus        body MenuModel   -> 201 + created MenuModel
    PUT    /stock/menus/{id}   body MenuModel   -> 200 updated MenuModel
    DELETE /stock/menus/{id}                    -> 204 (soft delete)

A MenuModel is ``{id, name, groups:[{id, name, lines:[{id, workingPrice, menuId,
recipeId?|stockItemId?, name, lineOrder, stockUnitRatio, salesTaxRateId?}]}]}``;
each line references a recipe XOR a stock item.

Read actions are additive and safe — nothing calls them until a binding or the
menu editor does. The reads ship with a PASS-THROUGH transform on purpose: the
real field names must be confirmed live (``/connector-specs/loadedhub/test``)
before a response_transform is trusted. The writes are declared POST/PUT/DELETE
with read_only=false; the create/update body is rendered from a ``menu`` object
the caller passes.

Idempotent — safe to re-run. The config DB is shared across every environment,
so committing reaches production immediately. Dry-run first.

Usage:
    .venv/bin/python scripts/sync_menu_actions.py --dry-run
    .venv/bin/python scripts/sync_menu_actions.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "loadedhub"

_HEADERS = {
    "Content-Type": "application/json",
    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
}


def _read(action, path, description, params=None, transform=None):
    params = params or {}
    return {
        "action": action,
        "description": description,
        "method": "GET",
        "path_template": path,
        "headers": dict(_HEADERS),
        "required_fields": list(params),
        "field_mapping": {k: k for k in params},
        "field_descriptions": dict(params),
        "request_body_template": "",
        "success_status_codes": [200],
        "response_ref_path": "",
        "timeout_seconds": 30,
        "response_transform": transform,
        "read_only": True,
    }


TOOLS = [
    _read(
        "list_menus",
        "//loadedhub.com/api/stock/menus?includeLines=true",
        "Every menu for the venue with its sections (groups) and lines. Each "
        "line references a recipe or a stock item and carries a sell price "
        "(workingPrice). PASS-THROUGH until field names are confirmed live.",
    ),
    _read(
        "get_menu",
        "//loadedhub.com/api/stock/menus/{{ menu_id }}",
        "One menu with its sections and lines. PASS-THROUGH until confirmed live.",
        {"menu_id": "The menu's id (a GUID, from list_menus)."},
    ),
    {
        "action": "create_menu",
        "description": (
            "Create a new menu. `menu` is the full MenuModel object "
            "({name, groups:[{name, lines:[{name, workingPrice, recipeId|"
            "stockItemId, lineOrder, salesTaxRateId}]}]}). This is a write — "
            "describe it and let the user approve before executing."
        ),
        "method": "POST",
        "path_template": "//loadedhub.com/api/stock/menus",
        "headers": dict(_HEADERS),
        "required_fields": ["menu"],
        "field_mapping": {"menu": "menu"},
        "field_descriptions": {"menu": "The full MenuModel to create."},
        "request_body_template": "{{ menu | tojson }}",
        "success_status_codes": [200, 201],
        "response_ref_path": "",
        "timeout_seconds": 30,
        "response_transform": None,
        "read_only": False,
    },
    {
        "action": "update_menu",
        "description": (
            "Update an existing menu (rename, add/remove/reorder sections and "
            "lines, change prices). `menu` is the full MenuModel with its id and "
            "current groups/lines. This is a write — human-approved."
        ),
        "method": "PUT",
        "path_template": "//loadedhub.com/api/stock/menus/{{ menu_id }}",
        "headers": dict(_HEADERS),
        "required_fields": ["menu_id", "menu"],
        "field_mapping": {"menu_id": "menu_id", "menu": "menu"},
        "field_descriptions": {
            "menu_id": "The menu's id (a GUID).",
            "menu": "The full MenuModel to save.",
        },
        "request_body_template": "{{ menu | tojson }}",
        "success_status_codes": [200],
        "response_ref_path": "",
        "timeout_seconds": 30,
        "response_transform": None,
        "read_only": False,
    },
    {
        "action": "delete_menu",
        "description": (
            "Soft-delete a menu by id. This is a write — human-approved."
        ),
        "method": "DELETE",
        "path_template": "//loadedhub.com/api/stock/menus/{{ menu_id }}",
        "headers": dict(_HEADERS),
        "required_fields": ["menu_id"],
        "field_mapping": {"menu_id": "menu_id"},
        "field_descriptions": {"menu_id": "The menu's id (a GUID)."},
        "request_body_template": "",
        "success_status_codes": [200, 204],
        "response_ref_path": "",
        "timeout_seconds": 30,
        "response_transform": None,
        "read_only": False,
    },
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

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
            sys.exit(f"No connector spec named {CONNECTOR}")

        tools = list(spec.tools or [])
        by_action = {t.get("action"): i for i, t in enumerate(tools)}
        changed = []

        for tool in TOOLS:
            action = tool["action"]
            idx = by_action.get(action)
            if idx is not None and tools[idx] == tool:
                print(f"  = {action}: already up to date")
                continue
            verb = "update" if idx is not None else "add"
            changed.append(f"{verb} {action}")
            if args.dry_run:
                print(f"  ~ {action}: WOULD {verb}")
                continue
            if idx is not None:
                tools[idx] = tool
            else:
                tools.append(tool)

        if not changed:
            print("nothing to do")
            return
        if args.dry_run:
            print(f"\n{len(changed)} change(s): {', '.join(changed)}")
            return

        spec.tools = tools
        spec.version = (spec.version or 0) + 1
        db.commit()
        print(f"\n{len(changed)} change(s) applied; spec version -> {spec.version}")
        for c in changed:
            print(f"  {c}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
