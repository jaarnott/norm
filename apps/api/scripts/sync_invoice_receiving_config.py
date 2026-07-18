"""Sync the LoadedHub invoice workflows' config into the shared config DB.

Covers both workflows — phase 1 (auto-receive draft invoices) and phase 2
(reconcile received invoices against supplier statements). Everything is
config: spec tools + consolidators on the `loadedhub` connector, procurement
binding capabilities, and playbooks. Consolidator function_code is loaded from
the canonical in-repo sources (config/consolidators/*.py) so it stays reviewed
and version-controlled.

Idempotent — safe to re-run; it upserts by action/slug and only reports what
changed. Run AFTER deploying the API code (the consolidator relies on the
extract_document / allowed_write_actions / binary-response infrastructure).

Usage:
    .venv/bin/python scripts/sync_invoice_receiving_config.py --dry-run
    .venv/bin/python scripts/sync_invoice_receiving_config.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_CONSOLIDATORS_DIR = (
    pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
)
FUNCTION_CODE_PATH = _CONSOLIDATORS_DIR / "review_and_receive_invoices.py"
RECONCILE_FUNCTION_CODE_PATH = _CONSOLIDATORS_DIR / "reconcile_received_invoices.py"

# All stock endpoints verified against the live LoadedHub app (16 Jul 2026):
# the web UI drives api.loadedhub.com/1.0/stock/... and the OAuth connector
# token authenticates against it. Paths start with //api.loadedhub.com because
# the spec's base_url_template is "https://" (rstripped and concatenated).
SPEC_TOOLS = [
    {
        "action": "list_stock_invoices",
        "method": "GET",
        "description": "List the venue's unreceived (draft) supplier invoices between two dates",
        "path_template": (
            "//api.loadedhub.com/1.0/stock/internal/invoices"
            "?from={{ from_date }}&to={{ to_date }}"
            "&status={{ status | default('NotReceived') }}"
            "&page={{ page | default(0) }}&pageSize={{ pageSize | default(100) }}"
        ),
        "headers": {"x-loaded-company-id": "{{ creds.x_loaded_company_id }}"},
        "required_fields": ["from_date", "to_date"],
        "optional_fields": ["status", "page", "pageSize"],
        "field_descriptions": {
            "from_date": "Start date YYYY-MM-DD",
            "to_date": "End date YYYY-MM-DD",
        },
    },
    {
        "action": "get_invoice_detail",
        "method": "GET",
        "description": "Get full detail for one supplier invoice: supplier, PO link, totals, attached file and line items",
        "path_template": (
            "//api.loadedhub.com/1.0/stock/invoices/{{ invoice_id }}"
            "?isAdjustingInvoice=false&includeDeleted=false"
        ),
        "headers": {"x-loaded-company-id": "{{ creds.x_loaded_company_id }}"},
        "required_fields": ["invoice_id"],
        "field_descriptions": {
            "invoice_id": "The Loaded invoice ID (from list_stock_invoices)"
        },
    },
    {
        "action": "get_stock_purchase_order",
        "method": "GET",
        "description": "Get one purchase order with its line items (item, unit, quantities, costs)",
        "path_template": "//api.loadedhub.com/1.0/stock/internal/purchase-orders/{{ purchase_order_id }}",
        "headers": {"x-loaded-company-id": "{{ creds.x_loaded_company_id }}"},
        "required_fields": ["purchase_order_id"],
    },
    {
        "action": "download_invoice_file",
        "method": "GET",
        "description": "Download the supplier's uploaded invoice document (PDF) attached to an invoice",
        "path_template": "//api.loadedhub.com/1.0/stock/internal/invoices/files/{{ file_id }}",
        "headers": {"x-loaded-company-id": "{{ creds.x_loaded_company_id }}"},
        "required_fields": ["file_id"],
        "response_format": "binary",
    },
    {
        "action": "receive_invoice",
        "method": "PUT",
        "description": (
            "[consolidator-only] Mark a supplier invoice as received in Loaded. "
            "Callable only from review_and_receive_invoices via its "
            "allowed_write_actions declaration — never bind this to an agent."
        ),
        "path_template": "//api.loadedhub.com/1.0/stock/internal/invoices/{{ invoice_id }}",
        "request_body_template": "{{ invoice | tojson }}",
        "headers": {"x-loaded-company-id": "{{ creds.x_loaded_company_id }}"},
        "required_fields": ["invoice_id", "invoice"],
        "success_status_codes": [200],
    },
]

# Phase 2 — reconcile received invoices against supplier statements.
# Endpoints verified live in the test env on 17 Jul 2026 (statement create/update
# mutations exercised there; production only read).
RECONCILE_SPEC_TOOLS = [
    {
        "action": "list_supplier_statements",
        "method": "GET",
        "description": "List supplier statements for the venue between two datetimes",
        "path_template": (
            "//api.loadedhub.com/1.0/stock/internal/supplier-statements"
            "?from={{ from_iso }}&to={{ to_iso }}&includeDeleted=false"
        ),
        "headers": {"x-loaded-company-id": "{{ creds.x_loaded_company_id }}"},
        "required_fields": ["from_iso", "to_iso"],
        "field_descriptions": {
            "from_iso": "Window start as ISO datetime, e.g. 2026-07-01T00:00:00.000Z",
            "to_iso": "Window end as ISO datetime",
        },
    },
    {
        "action": "list_received_invoices",
        "method": "GET",
        "description": "List received supplier invoices (with lines, PO number, file, reconciled flag) between two dates",
        "path_template": (
            "//api.loadedhub.com/1.0/stock/internal/stock-received"
            "?from={{ from_date }}&to={{ to_date }}&property=Invoiced"
            "&includeAdjustingInvoices=true&ifNoneGetLastReceived=false"
        ),
        "headers": {"x-loaded-company-id": "{{ creds.x_loaded_company_id }}"},
        "required_fields": ["from_date", "to_date"],
        "field_descriptions": {
            "from_date": "Start date YYYY-MM-DD",
            "to_date": "End date YYYY-MM-DD",
        },
    },
    {
        "action": "update_supplier_statement",
        "method": "PUT",
        "description": (
            "[consolidator-only] Update a supplier statement (marks invoices "
            "reconciled via reconciledStockReceivedItems). Callable only from "
            "reconcile_received_invoices — never bind this to an agent."
        ),
        "path_template": "//api.loadedhub.com/1.0/stock/internal/supplier-statements/{{ statement_id }}",
        "request_body_template": "{{ statement | tojson }}",
        "headers": {"x-loaded-company-id": "{{ creds.x_loaded_company_id }}"},
        "required_fields": ["statement_id", "statement"],
        "success_status_codes": [200],
    },
    {
        "action": "create_supplier_statement",
        "method": "POST",
        "description": (
            "[consolidator-only] Create a supplier statement. Callable only from "
            "reconcile_received_invoices after explicit user consent — never bind "
            "this to an agent."
        ),
        "path_template": "//api.loadedhub.com/1.0/stock/internal/supplier-statements",
        "request_body_template": "{{ statement | tojson }}",
        "headers": {"x-loaded-company-id": "{{ creds.x_loaded_company_id }}"},
        "required_fields": ["statement"],
        "success_status_codes": [200, 201],
    },
]

RECONCILE_CONSOLIDATOR_TOOL = {
    "action": "reconcile_received_invoices",
    "method": "GET",  # deliberate: consolidator dispatch auto-executes; the
    # deterministic gates in function_code decide every write; dry_run=true is
    # the read-only path.
    "description": (
        "Reconciles received supplier invoices against their supplier statements. "
        "For every unreconciled received invoice covered by a statement it verifies "
        "against the attached invoice copy: copy attached, PO number matches "
        "(strict), invoice date matches, and total incl tax matches within $0.02 — "
        "then AUTOMATICALLY marks passing invoices reconciled on the statement. "
        "Failing invoices are reported with exact reasons. Suppliers with no "
        "covering statement are reported in needs_statement; statements are only "
        "created when create_missing_statements=true is passed after the user "
        "explicitly agrees. Pass dry_run=true to review without changing anything."
    ),
    "required_fields": [],
    "optional_fields": [
        "from_date",
        "to_date",
        "dry_run",
        "create_missing_statements",
        "suppliers",
    ],
    "field_descriptions": {
        "from_date": "Statement search window start YYYY-MM-DD (default: 30 days ago)",
        "to_date": "Statement search window end YYYY-MM-DD (default: today)",
    },
    "field_schema": {
        "dry_run": {
            "type": "boolean",
            "description": "Report what would be reconciled without changing anything",
        },
        "create_missing_statements": {
            "type": "boolean",
            "description": "Create statements for suppliers that lack one (ONLY after the user explicitly agrees)",
        },
        "suppliers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Restrict the run to these supplier names",
        },
    },
    # Audit report the LLM must relay in full — raise the tool-result slim
    # threshold (clamped by HARD_MAX_TOOL_RESULT_CHARS in tool_loop.py).
    "max_result_chars": 100_000,
    "consolidator_config": {
        # function_code injected from RECONCILE_FUNCTION_CODE_PATH at sync time
        "max_api_calls": 120,
        "allowed_write_actions": [
            "update_supplier_statement",
            "create_supplier_statement",
        ],
    },
    # NOTE: deliberately NO display_component. A display block triggers the
    # tool loop's "display-only" early-exit (tool_loop.py Phase F) whenever the
    # model's pre-tool preamble exceeds 120 chars — which silently ends the
    # turn WITHOUT feeding the report back to the LLM. The playbook has the
    # LLM render the summary table in markdown instead.
}

CONSOLIDATOR_TOOL = {
    "action": "review_and_receive_invoices",
    "method": "GET",  # deliberate: internal/consolidator dispatch — the loop
    # auto-executes it; the deterministic gates in function_code (not the LLM)
    # decide every write, and dry_run=true runs the same path read-only.
    "description": (
        "Reviews outstanding draft supplier invoices and AUTOMATICALLY RECEIVES "
        "any that pass every deterministic check: invoice copy attached (hard "
        "stop without one), linked to a purchase order from the same supplier "
        "(PO lines/prices are NOT compared — invoices may differ from the PO), "
        "every stock item, brand and unit already exists in Loaded (nothing "
        "the receive screen would tag NEW), line arithmetic and totals "
        "consistent, and every line verified against the attached invoice copy "
        "(quantities, unit costs, units and totals; totals within $0.02; every "
        "line on the copy must be on the invoice). Invoices failing a check "
        "are never modified — they are reported with the first blocking "
        "problem only (later checks are not run). Pass dry_run=true to review "
        "without receiving."
    ),
    "required_fields": [],
    "optional_fields": ["from_date", "to_date", "dry_run"],
    "field_descriptions": {
        "from_date": "Start date YYYY-MM-DD (default: 60 days ago)",
        "to_date": "End date YYYY-MM-DD (default: today)",
    },
    "field_schema": {
        "dry_run": {
            "type": "boolean",
            "description": "Report what would be received without changing anything",
        }
    },
    # Audit report the LLM must relay in full — raise the tool-result slim
    # threshold (clamped by HARD_MAX_TOOL_RESULT_CHARS in tool_loop.py).
    "max_result_chars": 100_000,
    "consolidator_config": {
        # function_code injected from FUNCTION_CODE_PATH at sync time
        "max_api_calls": 80,
        "allowed_write_actions": ["receive_invoice"],
    },
    # NOTE: deliberately NO display_component — see the reconcile tool above.
}

PLAYBOOK = {
    "slug": "receive_loadedhub_invoices",
    "agent_slug": "procurement",
    "display_name": "Review & Receive Supplier Invoices",
    "description": (
        "Review outstanding (draft) supplier invoices in Loaded and automatically "
        "receive the ones that fully reconcile line-by-line against their purchase "
        "order and the attached supplier invoice PDF."
    ),
    "instructions": """Goal: review the venue's outstanding supplier invoices. Invoices that pass every safety check are received automatically by the review_and_receive_invoices tool — you never decide what gets received; the tool's deterministic checks do.

ROLLOUT: ALWAYS pass dry_run=true. (Remove this line after production verification.)

1. Call review_and_receive_invoices for the venue (default range: last 60 days). Before calling it, write at most ONE short status line (e.g. "Reviewing the outstanding invoices…") — the full report comes after the tool returns.
   - If the user only asks to "check", "review" or "look at" invoices, pass dry_run=true.
2. Write the report. Copy every value and ✓/✗/— exactly as returned — never invent, reformat, or fill in a "—" ("—" means that check was not run because an earlier one failed).
   - Start with a compact markdown summary table built from the tool's results rows — | Invoice | Supplier | PO | Total | Checks | Outcome | — one row per invoice (leave the reasons out of this table; they appear in the sections below).
   - Then the per-invoice audit view in two sections: "Received" (or "Would receive" on a dry run) then "Needs attention".
   - EVERY invoice that HAS details.lines (received, would receive, or failed during line/copy comparison) MUST get its own subsection — do not summarise or skip any:
     a. Heading: ### {reference_number} — {supplier_name} — {total}
     b. details.header as a markdown table — | Field | Invoice (Loaded) | PO | Invoice copy | Result | — one row per header field.
     c. details.lines as a markdown table — | Line | In Loaded | PO line | On copy | Unit | Quantity | Unit cost | Line total | Arithmetic | — one row per line (the stock_item field is the "In Loaded" column: ✗ means the stock item, brand or unit would be created as NEW; "PO line" is informational only — invoices may legitimately differ from their PO). The unit/quantity/cost/total cells arrive as ready-made comparison strings (e.g. "ord 5.0 / inv 4.95 / copy 4.95 ✓"); copy each cell verbatim. Include the "on copy only" rows and any "…more lines omitted" marker verbatim.
     d. Its checklist: when it is a string ("All 12 checks passed ✓"), print exactly that line; otherwise a compact | Check | Result | table.
     e. Its reasons, if any, as a markdown bulleted list.
   - Invoices WITHOUT details.lines were skipped before any comparison ran (no PO linked, credit note, fetch failure) — list each as one bold line "**{reference_number}** — {supplier_name} — {total}" followed by the tool's reasons as bullets. No tables for these; the tool reports only the first blocking problem, so present the bullets as-is without speculating.
3. Close with the summary counts and what manual work remains in Loaded (linking POs, adding freight lines, credit notes). Mention that the header comparison for any skipped invoice is available on request (it is in details.header of the same result).

If the user asks why a specific invoice was skipped, use get_invoice_detail together with the returned reasons — do not guess. Never suggest you can link POs, edit lines, or force-receive an invoice; that is done in Loaded by a person.""",
    "tool_filter": [
        "review_and_receive_invoices",
        "list_stock_invoices",
        "get_invoice_detail",
        "get_stock_purchase_order",
    ],
    "enabled": True,
}

RECONCILE_PLAYBOOK = {
    "slug": "reconcile_received_invoices",
    "agent_slug": "procurement",
    "display_name": "Reconcile Received Invoices Against Statements",
    "description": (
        "Reconcile received supplier invoices in Loaded against their supplier "
        "statements — verify the attached invoice copy (PO number, date, total) "
        "and tick the reconciled box for invoices that fully match."
    ),
    "instructions": """Goal: reconcile the venue's received supplier invoices against their supplier statements. Invoices that pass every check are marked reconciled automatically by the reconcile_received_invoices tool — you never decide what gets reconciled; the tool's deterministic checks do.

1. Call reconcile_received_invoices for the venue (default window: last 30 days of statements). Before calling it, write at most ONE short status line — the full report comes after the tool returns.
   - If the user only asks to "check", "review" or "look at" invoices, pass dry_run=true. After showing a dry run, if the user asks to proceed / confirms, call the tool again WITHOUT dry_run to reconcile for real.
2. Report the results, using the tool's exact values and reasons verbatim (never soften or re-derive them). Start with a compact markdown summary table built from the tool's results rows — | Invoice | Supplier | Statement | Total | Outcome | — one row per invoice. Then three sections:
   - "Reconciled" (or "Would reconcile" on a dry run) and "Could not reconcile": for EVERY invoice render a markdown comparison table from the tool's comparison data showing the actual values checked on each side — | Field | Received invoice (Loaded) | Invoice copy | Match | — with rows for invoice number, PO number, invoice date, and total incl tax. The Match cell comes from the field's `match` value: true → ✓, false → ✗, null → — (check not run). Copy the values exactly as returned; never invent or reformat them.
   - "Could not reconcile" additionally lists the exact reasons (missing copy, PO mismatch, date mismatch, total mismatch, credit).
   - "Suppliers needing a statement": supplier, invoice count, how many would reconcile once a statement exists.
3. Include each statement's amount vs reconciled amount difference from the tool's statements summary.
4. If needs_statement is non-empty, ASK THE USER whether Norm should create those statements. Only after the user explicitly says yes, call the tool again with create_missing_statements=true and suppliers set to the confirmed supplier names. Never create statements unprompted. Remind the user that an auto-created statement's number and amount must be updated from the paper statement.

If the user asks about a specific invoice, use get_invoice_detail plus the returned reasons — do not guess. Never claim you can edit statement amounts or fix mismatches; that is done in Loaded by a person.""",
    "tool_filter": [
        "reconcile_received_invoices",
        "list_supplier_statements",
        "list_received_invoices",
        "get_invoice_detail",
    ],
    "enabled": True,
}

# Write tools (receive_invoice, update/create_supplier_statement) and the raw
# file download are deliberately NOT bound to the agent — consolidators reach
# them internally via call_api under allowed_write_actions.
#
# Binding capability entries are DICTS ({action, label, enabled}) — the agents
# router and prompt_builder index into them; a bare string breaks both.
BINDING_CAPABILITY_ACTIONS = [
    "review_and_receive_invoices",
    "list_stock_invoices",
    "get_invoice_detail",
    "get_stock_purchase_order",
    "reconcile_received_invoices",
    "list_supplier_statements",
    "list_received_invoices",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.db.engine import _ConfigSessionLocal
    from app.db.config_models import AgentConnectorBinding, ConnectorSpec, Playbook

    consolidator = dict(CONSOLIDATOR_TOOL)
    consolidator["consolidator_config"] = {
        **CONSOLIDATOR_TOOL["consolidator_config"],
        "function_code": FUNCTION_CODE_PATH.read_text(encoding="utf-8"),
    }
    reconcile_consolidator = dict(RECONCILE_CONSOLIDATOR_TOOL)
    reconcile_consolidator["consolidator_config"] = {
        **RECONCILE_CONSOLIDATOR_TOOL["consolidator_config"],
        "function_code": RECONCILE_FUNCTION_CODE_PATH.read_text(encoding="utf-8"),
    }
    desired_tools = {
        t["action"]: t
        for t in [
            *SPEC_TOOLS,
            *RECONCILE_SPEC_TOOLS,
            consolidator,
            reconcile_consolidator,
        ]
    }

    db = _ConfigSessionLocal()
    changes: list[str] = []
    try:
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == "loadedhub")
            .first()
        )
        if not spec:
            raise SystemExit("loadedhub ConnectorSpec not found in config DB")

        tools = list(spec.tools or [])
        by_action = {t.get("action"): i for i, t in enumerate(tools)}
        for action, tool in desired_tools.items():
            if action in by_action:
                if tools[by_action[action]] != tool:
                    tools[by_action[action]] = tool
                    changes.append(f"spec tool updated: {action}")
            else:
                tools.append(tool)
                changes.append(f"spec tool added: {action}")
        spec.tools = tools

        binding = (
            db.query(AgentConnectorBinding)
            .filter(
                AgentConnectorBinding.agent_slug == "procurement",
                AgentConnectorBinding.connector_name == "loadedhub",
            )
            .first()
        )
        if not binding:
            raise SystemExit("procurement/loadedhub binding not found in config DB")
        caps = list(binding.capabilities or [])
        existing_actions = {c.get("action") if isinstance(c, dict) else c for c in caps}
        labels = {t["action"]: t.get("description", t["action"]) for t in tools}
        for action in BINDING_CAPABILITY_ACTIONS:
            if action not in existing_actions:
                caps.append(
                    {
                        "action": action,
                        "label": labels.get(action, action),
                        "enabled": True,
                    }
                )
                changes.append(f"binding capability added: {action}")
        binding.capabilities = caps

        for playbook_def in (PLAYBOOK, RECONCILE_PLAYBOOK):
            playbook = (
                db.query(Playbook).filter(Playbook.slug == playbook_def["slug"]).first()
            )
            if playbook:
                for key, value in playbook_def.items():
                    if getattr(playbook, key) != value:
                        setattr(playbook, key, value)
                        changes.append(
                            f"playbook {playbook_def['slug']} field updated: {key}"
                        )
            else:
                db.add(Playbook(**playbook_def))
                changes.append(f"playbook created: {playbook_def['slug']}")

        if not changes:
            print("Config already in sync — nothing to do.")
            return
        for line in changes:
            print(("DRY RUN: " if args.dry_run else "") + line)
        if args.dry_run:
            db.rollback()
        else:
            from sqlalchemy.orm.attributes import flag_modified

            flag_modified(spec, "tools")
            flag_modified(binding, "capabilities")
            db.commit()
            print(f"Applied {len(changes)} change(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
