"""Sync the norm.review_invoices function into the `norm` ConnectorSpec.

The review consolidator's ONE call: batch replica review server-side
(app/services/invoice_review.py) — extraction, replica, suggestions,
confidence issues, sensei training, and (under mode=autopilot) auto-accepting
every suggestion and receiving the invoices with no blocking issues. The
sandbox's call_api resolves tools from the spec row FIRST — without this
entry every call dies with "Tool not found".

Deliberately bound to NO agent — a building-block tool per
docs/tool-architecture-strategy.md (callable only by engine code, declared in
the consolidator's allowed_write_actions because it can write to Loaded).

Idempotent — upserts by action. Run against the shared config DB.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

TOOL = {
    "action": "review_invoices",
    "method": "POST",
    "description": (
        "[engine-only] Batch replica review of outstanding supplier invoices: "
        "extraction, replica resolution, suggestions + confidence issues, "
        "sensei training, and — under mode=autopilot — auto-accepting every "
        "suggestion (recorded) and receiving the invoices with no blocking "
        "issues. Called by review_and_receive_invoices via call_api; not "
        "bound to any agent."
    ),
    "required_fields": [],
    "optional_fields": [
        "venue",
        "venue_id",
        "invoice_ids",
        "invoice_id",
        "from_date",
        "to_date",
        "mode",
        "max_sensei",
        "require_valid_po",
    ],
    "field_descriptions": {
        "venue": "Venue name (resolved to an id); or pass venue_id directly.",
        "invoice_ids": "Specific Loaded invoice ids; omit to review the window.",
        "mode": "approve_all | approve_fixes | autopilot (default approve_all).",
        "max_sensei": "Budget for training brand-new suppliers this run.",
        "require_valid_po": "false → an unresolvable PO reference stops blocking.",
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
