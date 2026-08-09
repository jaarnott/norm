"""Sync the norm.match_supplier LLM function into the `norm` ConnectorSpec.

The review engine's supplier gate (stage 2) calls
call_api("norm", "match_supplier") when an invoice has no linked supplier or
the copy names a different business. The Python handler has existed in
app/agents/internal_tools.py since the gate shipped, but the sandbox's
call_api resolves tools from the spec row FIRST — without this entry every
call died with "Tool not found: norm.match_supplier" and the gate silently
degraded to "no matching Loaded supplier found", so the card never got a
switch-supplier suggestion.

Deliberately bound to NO agent — a building-block tool per
docs/tool-architecture-strategy.md (callable only by engine code).

Idempotent — upserts by action. Run against the shared config DB.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

TOOL = {
    "action": "match_supplier",
    "method": "GET",
    "description": (
        "[engine-only LLM function] Match a copy-printed supplier name to ONE "
        "Loaded supplier record (spec-row aliases ride along as hints). "
        "Called by review_and_receive_invoices via call_api; not bound to "
        "any agent."
    ),
    "required_fields": ["supplier_name"],
    "optional_fields": ["venue", "venue_id"],
    "field_descriptions": {
        "supplier_name": "The supplier name as printed on the invoice copy.",
        "venue": "Venue name (resolved to an id); or pass venue_id directly.",
    },
    "read_only": True,
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
