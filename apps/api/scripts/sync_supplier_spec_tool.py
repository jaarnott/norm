"""Sync norm.get_supplier_invoice_specs into the `norm` ConnectorSpec.

Supplier invoice specs are admin-maintained rows (config DB, Settings →
Supplier Specs): per-supplier extraction instructions + name aliases. The
review engine fetches them once per run via
``call_api("norm", "get_supplier_invoice_specs")`` and appends a matching
spec's instructions to the PDF-extraction prompt. The spec tool row must exist
because the sandbox's call_api resolves the tool def before routing to the
internal handler.

Deliberately bound to NO agent — a building-block tool per
docs/tool-architecture-strategy.md (callable only by engine code).

Also ensures the ``supplier_invoice_specs`` table exists on the shared config
DB (create_all only adds missing tables — the config DB is not
alembic-managed).

Idempotent — upserts by action. Run against the shared config DB:
    .venv/bin/python scripts/sync_supplier_spec_tool.py [--dry-run]
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

TOOL = {
    "action": "get_supplier_invoice_specs",
    "method": "GET",
    "description": (
        "[engine-only] Enabled supplier invoice specs (name, aliases, "
        "extraction instructions) for per-supplier PDF-extraction notes. "
        "Called by review_and_receive_invoices via call_api; not bound to "
        "any agent."
    ),
    "required_fields": [],
    "optional_fields": [],
    "field_descriptions": {},
    "read_only": True,
}


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConfigBase, ConnectorSpec
    from app.db.engine import _ConfigSessionLocal, _config_engine

    if not dry_run:
        # Ensure the supplier_invoice_specs table exists (no-op when present).
        ConfigBase.metadata.create_all(bind=_config_engine)

    db = _ConfigSessionLocal()
    spec = (
        db.query(ConnectorSpec).filter(ConnectorSpec.connector_name == "norm").first()
    )
    if not spec:
        raise SystemExit("norm ConnectorSpec not found in config DB")

    tools = list(spec.tools or [])
    by_action = {t.get("action"): i for i, t in enumerate(tools)}
    changed = []
    if TOOL["action"] in by_action:
        if tools[by_action[TOOL["action"]]] != TOOL:
            tools[by_action[TOOL["action"]]] = TOOL
            changed.append(f"updated tool {TOOL['action']}")
    else:
        tools.append(TOOL)
        changed.append(f"added tool {TOOL['action']}")

    if not changed:
        print("norm.get_supplier_invoice_specs already up to date")
        return
    if dry_run:
        print("DRY RUN — would apply:", *changed, sep="\n  ")
        return
    spec.tools = tools
    flag_modified(spec, "tools")
    db.commit()
    print("Applied:", *changed, sep="\n  ")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
