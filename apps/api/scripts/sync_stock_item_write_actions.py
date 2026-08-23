"""Add stock-item WRITE actions to the loadedhub connector spec.

Lets an agent create/update stock items, change a variant's unit, and set a
supplier's default variant — direct on Loaded's internal host
(``//api.loadedhub.com/1.0/stock/internal/...``), the same host + OAuth the
stock-receive flow already writes to (``received_invoice.py``). Reads there are
already config (``sync_stock_item_minimums_action.py``); these are the matching
writes. Confirmed live that OAuth is authorised to write there (a PUT to
``/items/{id}`` reaches processing rather than 403, unlike recipes which are 403).

The writes are POST/PUT/PATCH with ``read_only=false``; the body is the full object
the agent passes (a ``{{ obj | tojson }}`` passthrough), exactly like the menu
write actions (``sync_menu_actions.py``). They ride Norm's existing
describe->approve->execute write flow — no app code. Bind them to an agent with
``sync_executive_chef_agent.py``.

Idempotent — safe to re-run. The config DB is shared across every environment, so
committing reaches production. Dry-run first.

Usage:
    .venv/bin/python scripts/sync_stock_item_write_actions.py --dry-run
    .venv/bin/python scripts/sync_stock_item_write_actions.py
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

TOOLS = [
    {
        "action": "get_stock_item_full",
        "description": (
            "The COMPLETE stock item as Loaded stores it — every field plus its "
            "suppliers[] variants (each variant's id, supplierId, stockCode, "
            "unitId, unitCost, brandId, defaultForSupplier, description). Call this "
            "BEFORE update_stock_item so you can send the whole item back unchanged "
            "except your one edit. Read-only."
        ),
        "method": "GET",
        "path_template": "//api.loadedhub.com/1.0/stock/internal/items/{{ item_id }}",
        "headers": dict(_HEADERS),
        "required_fields": ["item_id"],
        "field_mapping": {"item_id": "item_id"},
        "field_descriptions": {
            "item_id": "The stock item's id (GUID, from get_stock_items)."
        },
        "request_body_template": "",
        "success_status_codes": [200],
        "response_ref_path": "",
        "timeout_seconds": 30,
        "response_transform": None,  # pass-through: keep every field for the round-trip
        "read_only": True,
    },
    {
        "action": "update_stock_item",
        "description": (
            "Update a stock item by PUTting the WHOLE item back. First call "
            "get_stock_item_full, change ONLY the field(s) you need in the returned "
            "object, then pass that COMPLETE object as `item` (keep every other "
            "field, and keep every entry in suppliers[] with its "
            "stockCode/description/unitCost/brandId). To change the counting unit "
            "set countingUnitId AND countingUnitRatio (the unit's ratio from "
            "get_stock_units); for the ordering unit set orderingUnitId AND "
            "orderingUnitRatio. To change which variant is the supplier's default, "
            "set defaultForSupplier:true on that one suppliers[] entry and false on "
            "EVERY other entry with the same supplierId (exactly one true per "
            "supplier). This is a write — describe it and let the user approve."
        ),
        "method": "PUT",
        "path_template": "//api.loadedhub.com/1.0/stock/internal/items/{{ item_id }}",
        "headers": dict(_HEADERS),
        "required_fields": ["item_id", "item"],
        "field_mapping": {"item_id": "item_id", "item": "item"},
        "field_descriptions": {
            "item_id": "The stock item's id (GUID).",
            "item": (
                "The COMPLETE stock item object from get_stock_item_full with your "
                "edit applied — include id, name, groupId, unitType, countingUnitId, "
                "countingUnitRatio, orderingUnitId, orderingUnitRatio, "
                "defaultSupplierId, itemType, and the full suppliers[] array "
                "unchanged except the field you are editing."
            ),
        },
        "request_body_template": "{{ item | tojson }}",
        "success_status_codes": [200],
        "response_ref_path": "",
        "timeout_seconds": 30,
        "response_transform": None,
        "read_only": False,
    },
    {
        "action": "create_stock_item",
        "description": (
            "Create a NEW stock item. Pass the full `item` object: name, groupId "
            "(from get_stock_item_groups), unitType (0=Weight, 1=Volume, 2=Count), "
            "countingUnitId+countingUnitRatio (the base unit for the dimension — "
            "kilo/litre/each, ratio usually 1.0), orderingUnitId+orderingUnitRatio "
            "(the purchase unit + its ratio from get_stock_units), defaultSupplierId, "
            "itemType:'Default', and suppliers:[{supplierId, stockCode, unitId, "
            "unitCost, brandId, defaultForSupplier:true, description}]. Units and ids "
            "come from the read tools. This is a write — human-approved."
        ),
        "method": "POST",
        "path_template": "//api.loadedhub.com/1.0/stock/internal/items",
        "headers": dict(_HEADERS),
        "required_fields": ["item"],
        "field_mapping": {"item": "item"},
        "field_descriptions": {
            "item": "The full stock item to create (see the description for the fields)."
        },
        "request_body_template": "{{ item | tojson }}",
        "success_status_codes": [200, 201],
        "response_ref_path": "",
        "timeout_seconds": 30,
        "response_transform": None,
        "read_only": False,
    },
    {
        "action": "update_variant_unit",
        "description": (
            "Change ONE supplier variant's unit. variant_id is the id of the "
            "suppliers[] entry (from get_stock_item_full); unit_id is the new unit "
            "(from get_stock_units). Use this for a single variant-unit change "
            "instead of a whole-item PUT. This is a write — human-approved."
        ),
        "method": "PATCH",
        "path_template": (
            "//api.loadedhub.com/1.0/stock/internal/item-supplier-variant/{{ variant_id }}"
        ),
        "headers": dict(_HEADERS),
        "required_fields": ["variant_id", "unit_id"],
        "field_mapping": {"variant_id": "variant_id", "unit_id": "unit_id"},
        "field_descriptions": {
            "variant_id": "The suppliers[] entry id (GUID).",
            "unit_id": "The new unit id (GUID).",
        },
        "request_body_template": '{"unitId": "{{ unit_id }}"}',
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

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec:
            sys.exit(f"No connector spec named {CONNECTOR}")

        tools = list(spec.tools or [])
        changed = False
        for tool in TOOLS:
            idx = next(
                (i for i, t in enumerate(tools) if t.get("action") == tool["action"]),
                None,
            )
            if idx is not None and tools[idx] == tool:
                print(f"{tool['action']}: already up to date")
                continue
            verb = "update" if idx is not None else "add"
            print(f"{tool['action']}: {verb}")
            if idx is not None:
                tools[idx] = tool
            else:
                tools.append(tool)
            changed = True

        if not changed:
            print("nothing to do")
            return
        if args.dry_run:
            print("(dry run — nothing written)")
            return

        spec.tools = tools
        spec.version = (spec.version or 0) + 1
        flag_modified(spec, "tools")
        db.commit()
        print(f"committed, spec version -> {spec.version}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
