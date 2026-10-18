"""Install manage_supplier: a venue's Loaded supplier records (Oct 2026).

Loaded files a scanned invoice under a supplier by that supplier's aliases.
A wrong one files another business's invoices there: St. Malo Bakery at
Somewhere North carried "St.", and 19 Sydney Waste Services bills ($12,170)
went in under the bakery (17 Oct 2026). Norm only ever read these lists.

What this puts in the shared config DB, idempotently, on the loadedhub spec:

  endpoint  delete_supplier_alias_raw  DELETE /suppliers/{id}/aliases/{aliasId}
            (204; seen in Loaded's own app, 18 Oct 2026). The read,
            get_supplier_aliases, and get_suppliers already exist.
  tool      manage_supplier  op review_aliases (reads, never asks) and
            remove_alias (a write: asks first; each person may switch it to
            "always allow") — config/consolidators/manage_supplier.py

The App claim (Loaded Stock → manage_supplier) lives in
sync_marketplace_catalog.py — land it with `--only loaded-stock` AFTER this,
since its validator wants the tool row to exist.

Usage:
    .venv/bin/python scripts/sync_manage_supplier_config.py --dry-run
    .venv/bin/python scripts/sync_manage_supplier_config.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "loadedhub"
_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"

ENDPOINTS = [
    {
        "action": "delete_supplier_alias_raw",
        "method": "DELETE",
        "path_template": (
            "//api.loadedhub.com/1.0/stock/internal/suppliers/{{ supplier_id }}"
            "/aliases/{{ alias_id }}"
        ),
        "headers": {"x-loaded-company-id": "{{ creds.x_loaded_company_id }}"},
        "required_fields": ["supplier_id", "alias_id"],
        "field_mapping": {},
        "field_descriptions": {
            "supplier_id": "The supplier record's id.",
            "alias_id": "The alias's id, from get_supplier_aliases.",
        },
        "request_body_template": "",
        "success_status_codes": [200, 204],
        "timeout_seconds": 30,
        "engine_only": True,
        "read_only": False,
        "description": (
            "[consolidator-only] Remove one alias from a supplier record (204). "
            "Loaded stops filing documents that print that name under the "
            "supplier. A write — called by manage_supplier; never bind to an agent."
        ),
    },
]

TOOL = {
    "action": "manage_supplier",
    # PUT: the method the human-approval gate used to key on; effect decides now.
    "method": "PUT",
    "effect": "write",
    "read_only": False,
    "approval": {
        "default": "ask",
        "allow_auto": True,
        "label": "remove supplier aliases in Loaded",
    },
    "description": (
        "A venue's Loaded supplier records. Loaded files a scanned invoice under "
        "a supplier by that supplier's aliases (its other names), so a wrong one "
        "files another business's invoices there. op 'review_aliases' (reads; "
        "never asks): every supplier's aliases with the wrong-looking ones flagged "
        "— blank, the venue's own name, a duplicate, too short or generic to name "
        "a business ('St.'), another supplier's name (advice remove), on two "
        "suppliers or unrelated to the supplier's name (advice check: a legal or "
        "former trading name looks unrelated too). Give supplier to see one "
        "record's aliases. op 'remove_alias' (a write): supplier (name or id) and "
        "aliases (as review_aliases lists them, or their alias_id); an alias "
        "that isn't on that record stops the call. Remove what's advised remove; "
        "for a check, look at what the supplier's invoices print first. Don't ask "
        "the person first: the approval asks when it needs to."
    ),
    "required_fields": ["venue"],
    "optional_fields": ["op", "supplier", "aliases"],
    "field_descriptions": {
        "venue": "The venue name.",
        "op": "review_aliases (default) or remove_alias.",
        "supplier": "A supplier's name or id: review one record, or the record to remove aliases from.",
        "aliases": "remove_alias: the aliases to remove, as review_aliases lists them, or their alias_id.",
    },
    "field_schema": {
        "op": {"type": "string", "enum": ["review_aliases", "remove_alias"]},
        "aliases": {"type": "array", "items": {"type": "string"}},
    },
    "consolidator_config": {
        # review: the supplier list + one alias read per supplier
        "max_api_calls": 200,
        "allowed_write_actions": ["delete_supplier_alias_raw"],
    },
}

TOOLS = [(TOOL, "manage_supplier.py")]


def _tool_row(tool: dict, filename: str) -> dict:
    row = dict(tool)
    row["consolidator_config"] = {
        **tool["consolidator_config"],
        "function_code": (_DIR / filename).read_text(encoding="utf-8"),
    }
    return row


def plan(spec) -> tuple[list[dict], list[dict], list[str]]:
    """The spec's (tools, endpoints) with this install applied, and what changed."""
    from app.connectors import spec_rows

    changes: list[str] = []
    endpoints = list(spec_rows.endpoints(spec))
    have = {e.get("action"): e for e in endpoints}
    for ep in ENDPOINTS:
        live = have.get(ep["action"])
        merged = {**(live or {}), **ep}
        if live != merged:
            changes.append(f"{'updated' if live else 'added'} endpoint {ep['action']}")
    endpoints = spec_rows.upsert(
        endpoints, [{**(have.get(ep["action"]) or {}), **ep} for ep in ENDPOINTS]
    )

    tools = list(spec_rows.tools(spec))
    have_t = {t.get("action"): t for t in tools}
    rows = []
    for tool, filename in TOOLS:
        live = have_t.get(tool["action"])
        merged = {**(live or {}), **_tool_row(tool, filename)}
        if live != merged:
            changes.append(f"{'updated' if live else 'added'} tool {tool['action']}")
        rows.append(merged)
    tools = spec_rows.upsert(tools, rows)
    return tools, endpoints, changes


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
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
            raise SystemExit(f"{CONNECTOR} ConnectionSpec not found in config DB")
        if not spec_rows.is_split(spec):
            raise SystemExit(f"{CONNECTOR} is not split into tools and endpoints")

        tools, endpoints, changes = plan(spec)
        if not changes:
            print("Already in sync.")
            db.rollback()
            return
        verb = "Would apply" if dry_run else "Applied"
        if dry_run:
            db.rollback()
        else:
            # Endpoints first: the tools listener routes an endpoint row it is
            # handed into `endpoints`, so the explicit list must be in place.
            spec.endpoints = endpoints
            flag_modified(spec, "endpoints")
            spec.tools = tools
            flag_modified(spec, "tools")
            db.commit()
        for line in changes:
            print(f"{verb}: {line}")
        if not dry_run:
            from app.services.config_validator import validate_config

            summary = validate_config(config_db=db)
            print(f"validate_config: {summary}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    main(dry_run=parser.parse_args().dry_run)
