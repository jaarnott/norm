"""Set `summary_fields` on loadedhub read actions that lack them.

WHY THIS MATTERS

`summary_fields` is the only thing that makes an oversized result *usable*.
Without it, `_slim_tool_result` (and the MCP surface's `shape_result`) fall
through to the `_too_large` branch, which drops every row and hands the model a
single sample item plus "narrow your request". With it, the result is projected
to the useful columns and the model still gets all the rows.

Only 3 of 31 reachable read actions had them. `list_supplier_statements`
truncated at just 21 items over MCP for exactly this reason.

WHERE THESE FIELD NAMES COME FROM

Every list below was read off a **real production payload** returned by the tool
during a live MCP session, not from documentation or from another codebase's
types. That distinction is load-bearing here: two of five response transforms
written from Loaded's TypeScript types were wrong on first contact with the real
API (`reason` was actually `note`; a `leaveTypeName` that does not exist), and a
wrong field name fails *silently* — the column is simply absent from the
projection.

Actions with no observed payload are deliberately omitted rather than guessed.

    uv run python scripts/sync_summary_fields.py --dry-run
    uv run python scripts/sync_summary_fields.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# action -> summary_fields, each verified against a live response.
SUMMARY_FIELDS: dict[str, list[str]] = {
    # 120 rows for one venue for one day — the largest routine offender.
    "get_cogs_detail": [
        "product",
        "group",
        "category",
        "quantity",
        "revenue",
        "cost",
        "discounts",
    ],
    "get_pos_discounts": [
        "discountType",
        "discountsAmount",
        "discountsCount",
        "discountInvoices",
    ],
    "get_staff_orders": ["label", "id", "quantity", "amount"],
    # 65 rows for a single staff member's day. The POS/category identifier
    # UUIDs are dropped: they are half the payload and the model never reads
    # them, while itemName/itemGroupName carry the meaning.
    "get_staff_item_orders": [
        "staffName",
        "itemName",
        "itemGroupName",
        "quantity",
        "amount",
    ],
    # `breaks` is a nested array and `staffMemberId` a UUID — both dropped.
    "get_timeclock_entries": [
        "staffMemberFirstName",
        "staffMemberLastName",
        "roleName",
        "clockinTime",
        "clockoutTime",
        "totalHours",
        "totalCost",
    ],
    "get_roster_vs_actual": [
        "startTime",
        "rosteredCost",
        "actualCost",
        "rosteredHours",
        "actualHours",
    ],
    # The one that demonstrably truncated at 21 items over MCP.
    #
    # These names were WRONG on the first attempt, and the failure is worth
    # recording: they were copied from the `*_for_period` wrapper's payload,
    # which returns received-invoice rows, whereas the raw action returns
    # *statement* rows — a different shape entirely. Six of the field sets in
    # this file came from calling that exact action; this was the one inferred
    # from a neighbour, and it silently projected every row down to nothing but
    # supplierName. Verified against a live response second time around.
    "list_supplier_statements": [
        "supplierName",
        "statementNumber",
        "statementAmount",
        "startAt",
        "endAt",
        "reconciledAmount",
        "reconciledCount",
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    changes: list[str] = []
    try:
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == "loadedhub")
            .first()
        )
        if not spec:
            raise SystemExit("loadedhub ConnectorSpec not found")

        tools = list(spec.tools or [])
        by_action = {t.get("action"): i for i, t in enumerate(tools)}

        missing = [a for a in SUMMARY_FIELDS if a not in by_action]
        if missing:
            raise SystemExit(f"actions not present in the spec: {missing}")

        for action, fields in SUMMARY_FIELDS.items():
            tool = dict(tools[by_action[action]])
            existing = tool.get("summary_fields")
            if existing == fields:
                continue
            if existing:
                # Never silently overwrite a human's curation.
                changes.append(f"skipped: {action} already has {existing}")
                continue
            tool["summary_fields"] = fields
            tools[by_action[action]] = tool
            changes.append(f"set: {action} -> {fields}")

        if not any(c.startswith("set:") for c in changes):
            for line in changes:
                print(line)
            print("Nothing to set — already in sync.")
            return

        spec.tools = tools
        for line in changes:
            print(("DRY RUN: " if args.dry_run else "") + line)

        if args.dry_run:
            db.rollback()
            return
        flag_modified(spec, "tools")
        db.commit()
        print(
            f"\nApplied to {len([c for c in changes if c.startswith('set:')])} tools."
        )
        print(
            "Re-run scripts/sync_for_period_config.py so the *_for_period "
            "wrappers inherit these."
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
