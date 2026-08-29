"""Sync the engine-only norm.* functions into the `norm` ConnectionSpec.

Covers norm.review_invoices (the review consolidator's one call),
norm.invoice_copy_evidence and norm.record_split_order (reconciliation's).

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

REVIEW_TOOL = {
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

# Reconciliation's ONE call into the receive side. A READ: it returns what the
# receive flow already extracted for each invoice and reads fresh only what is
# missing, using the same PDF_SCHEMA and per-supplier spec instructions so the
# cache row is shared rather than paid for twice.
EVIDENCE_TOOL = {
    "action": "invoice_copy_evidence",
    "method": "GET",
    "description": (
        "[engine-only] What each invoice COPY says — the header Norm already "
        "extracted on the receive side, reading fresh only what is missing. "
        "Called by reconcile_received_invoices via call_api; not bound to any "
        "agent."
    ),
    "required_fields": ["invoices"],
    "optional_fields": ["venue", "venue_id"],
    "field_descriptions": {
        "venue": "Venue name (resolved to an id); or pass venue_id directly.",
        "invoices": "List of {id, fileId?, supplierName?, purchaseOrderNumber?}.",
    },
    "read_only": True,
}

# Reconciliation's only write to an INVOICE. It does not make the reconcile
# decision — invoice_copy_evidence already established the split from Loaded —
# it persists the evidence: a durable note, plus a best-effort PO reference for
# whoever reads Loaded's invoice list (a user's Save wipes that reference;
# verified 25 Aug 2026, which is why nothing depends on it).
SPLIT_TOOL = {
    "action": "record_split_order",
    "method": "POST",
    "description": (
        "[engine-only] Record a confirmed split delivery on invoices that carry "
        "no purchase order: a 'Split order:' note (durable) and the order number "
        "as a reference (convenience). Never links the order — Loaded is 1:1 and "
        "the link belongs to the sibling invoice. Called by "
        "reconcile_received_invoices via call_api; not bound to any agent."
    ),
    "required_fields": ["invoices"],
    "optional_fields": ["venue", "venue_id"],
    "field_descriptions": {
        "venue": "Venue name (resolved to an id); or pass venue_id directly.",
        "invoices": "List of {id, order_number, sibling_reference}.",
    },
    "read_only": False,
}

TOOLS = [REVIEW_TOOL, EVIDENCE_TOOL, SPLIT_TOOL]


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    spec = (
        db.query(ConnectionSpec).filter(ConnectionSpec.connector_name == "norm").first()
    )
    if not spec:
        raise SystemExit("norm ConnectionSpec not found in config DB")

    tools = list(spec.tools or [])
    changed = []
    for tool in TOOLS:
        by_action = {t.get("action"): i for i, t in enumerate(tools)}
        if tool["action"] in by_action:
            if tools[by_action[tool["action"]]] != tool:
                tools[by_action[tool["action"]]] = tool
                changed.append(f"updated tool {tool['action']}")
        else:
            tools.append(tool)
            changed.append(f"added tool {tool['action']}")

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
