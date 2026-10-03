"""Label every tool an App claims with what it does — read, draft or write.

Approvals stage 2 (Oct 2026, plan: ~/.claude/plans/structured-launching-cook.md).
The agent loop, MCP, apps and the App Map decide "does this change something?"
from a tool's declared ``effect`` (app/connectors/spec_rows.py). Without one
they fall back to its HTTP method, which is how the report email, task changes
and both invoice tools — all declared GET — ran without anyone being asked.

Sets two keys on each tool row, and nothing else (scripts/tool_row_updates.py
keeps the rest of the live row, backs it up, and runs validate_config):

- ``effect``: read | draft | write
- ``approval`` (writes): default ask|auto, allow_auto, label, and for the two
  tiered invoice tools their levels (first writes nothing) and options.

What changes for people once the code that reads these is deployed:
- ``norm_email.send_report_email`` and ``norm.manage_task`` ask first (they ran
  without a card).
- ``norm.set_workflow_mode`` asks first, and can never be switched to
  "always allow" — Norm must not raise its own autonomy silently.
- A "Test" run of a scheduled task simulates every write, and runs the invoice
  tools at "Ask me" (report only).

Inert on code that predates it (nothing else reads these keys), so it can be
applied before or after the deploy.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_approval_labels.py --dry-run
    uv run python scripts/sync_approval_labels.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.tool_row_updates import RowUpdate, apply_row_updates  # noqa: E402

READ, DRAFT, WRITE = "read", "draft", "write"

#: Reads — change nothing.
READS = [
    "bamboohr.get_hr",
    "loadedhub.calculate_template_stock_requirements",
    "loadedhub.get_budgets",
    "loadedhub.get_cogs_detail_for_period",
    "loadedhub.get_invoices",
    "loadedhub.get_labour",
    "loadedhub.get_menus",
    "loadedhub.get_purchase_orders",
    "loadedhub.get_received_items_for_period",
    "loadedhub.get_recipes",
    "loadedhub.get_sales",
    "loadedhub.get_stock",
    "loadedhub.get_stocktakes",
    "norm.get_app",
    "norm.list_app_capabilities",
    "norm.open_app",
    "norm.recall_memory",
    "norm.search_tool_result",
    "norm.show_connect",
    "norm_reports.render_chart",
]

#: Drafts — prepare something a person finishes in an editor; the editor's
#: button (Place Order, Save, Share) is the approval.
DRAFTS = [
    "loadedhub.edit_recipe",
    "norm.create_purchase_order",
    "norm.save_app",  # private to its author until shared
    "norm.show_roster",  # opens the roster editor; its Save writes
]


def _ask(label: str, allow_auto: bool = True) -> dict:
    return {"default": "ask", "allow_auto": allow_auto, "label": label}


def _auto(label: str) -> dict:
    return {"default": "auto", "allow_auto": False, "label": label}


def _receiving_options() -> list[dict]:
    from app.services.venue_autopilot import GATES

    return [{"id": gid, "label": text} for gid, text in GATES.items()]


#: Writes, with how they are approved.
WRITES = {
    "loadedhub.manage_stock_item": _ask("update stock items"),
    "loadedhub.manage_menu": _ask("change menus"),
    "cook_brothers_app.record_recipe": _ask("save recipes in Orbit"),
    "gmail.send_email": _ask("send emails from your Gmail"),
    "norm_email.send_report_email": _ask("email reports"),
    "norm.manage_task": _ask("change scheduled tasks"),
    "norm.set_workflow_mode": _ask(
        "change how much Norm does on its own", allow_auto=False
    ),
    "norm.remember": _auto("remember what you tell it"),
    "loadedhub.review_and_receive_invoices": {
        "default": "ask",
        "allow_auto": True,
        "label": "receive supplier invoices",
        "levels": [
            {
                "id": "approve_all",
                "label": "Ask me",
                "description": "Norm reviews each invoice and shows it to you; "
                "nothing is received until you press Receive.",
                "writes": False,
            },
            {
                "id": "approve_fixes",
                "label": "Receive clean invoices",
                "description": "Norm receives invoices that match their order "
                "exactly; anything else waits for you.",
                "writes": True,
            },
            {
                "id": "autopilot",
                "label": "Autopilot",
                "description": "Norm also accepts its own fixes and receives "
                "anything without a blocker, within the switches you turn on.",
                "writes": True,
            },
        ],
        "options": None,  # filled from venue_autopilot.GATES at run time
    },
    "loadedhub.reconcile_received_invoices": {
        "default": "ask",
        "allow_auto": True,
        "label": "reconcile invoices against supplier statements",
        "levels": [
            {
                "id": "approve_all",
                "label": "Ask me",
                "description": "Norm reports what reconciles; nothing is "
                "ticked in Loaded.",
                "writes": False,
            },
            {
                "id": "approve_fixes",
                "label": "Tick the clean ones",
                "description": "Norm marks invoices that fully match their "
                "statement as reconciled.",
                "writes": True,
            },
            {
                "id": "autopilot",
                "label": "Autopilot",
                "description": "Norm also creates missing supplier statements "
                "for suppliers whose invoices all pass.",
                "writes": True,
            },
        ],
    },
}


def labels() -> dict[str, dict]:
    """``connector.action`` -> the keys to set on its row."""
    out: dict[str, dict] = {key: {"effect": READ} for key in READS}
    out.update({key: {"effect": DRAFT} for key in DRAFTS})
    for key, policy in WRITES.items():
        policy = dict(policy)
        if "options" in policy:
            policy["options"] = _receiving_options()
        out[key] = {"effect": WRITE, "approval": policy}
    return out


def updates() -> list[RowUpdate]:
    rows = []
    for key, fields in sorted(labels().items()):
        connector, _, action = key.partition(".")
        rows.append(RowUpdate(connector, action, fields=fields))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    apply_row_updates(updates(), dry_run=args.dry_run, label="approval-labels")


if __name__ == "__main__":
    main()
