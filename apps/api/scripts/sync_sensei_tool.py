"""Sync the norm.sensei_train_supplier function into the `norm` ConnectorSpec.

The review engine calls call_api("norm", "sensei_train_supplier") when an
invoice's supplier has NO spec prompt: the SENSEI files the invoice into the
dojo as the supplier's founding regression sample and runs the analysis agent
synchronously, so the supplier's first spec (auto-applied when green) shapes
that very extraction. The sandbox's call_api resolves tools from the spec row
FIRST — without this entry every call dies with "Tool not found".

Deliberately bound to NO agent — a building-block tool per
docs/tool-architecture-strategy.md (callable only by engine code).

Idempotent — upserts by action. Run against the shared config DB.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

TOOL = {
    "action": "sensei_train_supplier",
    "method": "POST",
    "description": (
        "[engine-only LLM function] File an invoice into the dojo and run the "
        "sensei (analysis agent) synchronously to create the supplier's first "
        "spec when none exists. Called by review_and_receive_invoices via "
        "call_api; not bound to any agent."
    ),
    "required_fields": ["invoice_id", "supplier_name"],
    "optional_fields": ["venue", "venue_id"],
    "field_descriptions": {
        "invoice_id": "The Loaded invoice to file as the training sample.",
        "supplier_name": "The invoice's supplier name (spec lookup key).",
        "venue": "Venue name (resolved to an id); or pass venue_id directly.",
    },
    "read_only": False,
}


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

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
        print("Already in sync.")
        return
    if dry_run:
        print("Would apply:", "; ".join(changed))
        return
    spec.tools = tools
    flag_modified(spec, "tools")
    db.commit()
    print("Applied:", "; ".join(changed))


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
