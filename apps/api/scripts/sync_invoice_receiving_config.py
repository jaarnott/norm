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
PREPARE_FUNCTION_CODE_PATH = _CONSOLIDATORS_DIR / "prepare_receive_invoice.py"

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
        "action": "list_purchase_orders",
        "method": "GET",
        "description": (
            "List the venue's OPEN purchase orders (order number, supplier, linked "
            "invoice). Loaded has no PO-by-number search, so the review consolidator "
            "uses this to resolve a referenced PO number to its id."
        ),
        "path_template": (
            "//api.loadedhub.com/1.0/stock/internal/purchase-orders"
            "?from={{ from_date | default('1901-01-01') }}"
            "&to={{ to_date | default('9999-12-31') }}"
        ),
        "headers": {"x-loaded-company-id": "{{ creds.x_loaded_company_id }}"},
        "required_fields": [],
        "optional_fields": ["from_date", "to_date"],
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
    # deterministic gates in function_code decide every write, gated by the
    # caller's per-user run mode (approve_all writes nothing).
    "description": (
        "Reconciles received supplier invoices against their supplier statements. "
        "For every unreconciled received invoice covered by a statement it verifies "
        "against the attached invoice copy: copy attached, PO number matches "
        "(strict), invoice date matches, and total incl tax matches within $0.02 — "
        "then marks passing invoices reconciled on the statement. What is written "
        "is governed by the caller's run mode (approve_all reports only). Failing "
        "invoices are reported with exact reasons. Suppliers with no covering "
        "statement are reported in needs_statement; statements are only created "
        "when create_missing_statements=true is passed after the user explicitly "
        "agrees (or in autopilot mode)."
    ),
    "required_fields": [],
    "optional_fields": [
        "from_date",
        "to_date",
        "create_missing_statements",
        "suppliers",
    ],
    "field_descriptions": {
        "from_date": "Statement search window start YYYY-MM-DD (default: 30 days ago)",
        "to_date": "Statement search window end YYYY-MM-DD (default: today)",
    },
    "field_schema": {
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
    # decide every write, gated by the caller's per-user run mode.
    "description": (
        "Reviews outstanding draft supplier invoices and receives any that pass "
        "every deterministic check: invoice copy attached (hard "
        "stop without one), linked to a purchase order from the same supplier "
        "(PO lines/prices are NOT compared — invoices may differ from the PO), "
        "every stock item, brand and unit already exists in Loaded (nothing "
        "the receive screen would tag NEW), invoice totals consistent, "
        "and every line verified against the attached invoice copy "
        "(quantities, unit costs, units and totals; totals within $0.02; every "
        "line on the copy must be on the invoice; the copy's guideline-derived "
        "delivered unit of measure must agree with Loaded's unit — a mismatch "
        "reports the recommended unit to fix in Loaded). What is written is "
        "governed by the caller's run mode (approve_all receives nothing). "
        "Invoices failing a check are never modified — every check that can "
        "run is reported, with the specific reasons."
    ),
    "required_fields": [],
    "optional_fields": ["from_date", "to_date"],
    "field_descriptions": {
        "from_date": "Start date YYYY-MM-DD (default: 60 days ago)",
        "to_date": "End date YYYY-MM-DD (default: today)",
    },
    "field_schema": {},
    # The LLM writes a SHORT summary (not the retired audit tables), but the
    # result still carries every fix_invoice payload for the doc fan-out below —
    # keep headroom above the 30k default slim threshold (clamped by
    # HARD_MAX_TOOL_RESULT_CHARS in tool_loop.py).
    "max_result_chars": 60_000,
    "consolidator_config": {
        # function_code injected from FUNCTION_CODE_PATH at sync time.
        # 120: an 18-invoice run measured 72 calls BEFORE variant-lookup
        # matching; the engine's own fetch budget adds up to 20 get_stock_item
        # calls, and overflowing the executor cap raises and kills the whole
        # run — so keep real headroom (hard cap 200).
        "max_api_calls": 120,
        "allowed_write_actions": ["receive_invoice"],
    },
    # FAN-OUT: one received_invoice working document + one COMPACT editable
    # Receive Invoice card per fix_invoice (each card payload IS a complete doc
    # — see make_fix_invoice). Keyed per invoice_id (+ venue) so a re-run
    # updates the same drafts and the Invoices page opens the SAME documents.
    # suppress_display_early_exit keeps the LLM's summary: without it the turn
    # would end on the pre-tool status line.
    "working_document": {
        "doc_type": "received_invoice",
        "sync_mode": "submit",
        "items_path": "fix_invoices",
        "ref_fields": ["invoice_id"],
    },
    "display_component": "receive_invoice_editor",
    "display_props": {"compact": True},
    "suppress_display_early_exit": True,
}

# The single-invoice "open one and receive it" tool. Unlike the batch review
# (which renders the read-only-until-you-act fix cards), this materialises a
# `received_invoice` WORKING DOCUMENT from the shaped invoice and renders the
# editable receive_invoice_editor over it — the same editor the Invoices page
# inline-expands, dual-surface (web chat + Claude via receive_display.py).
PREPARE_RECEIVE_TOOL = {
    "action": "receive_loadedhub_invoice",
    "method": "GET",  # read/consolidator dispatch; the write is the user's click
    "description": (
        "Open ONE outstanding supplier invoice as an editable Receive Invoice "
        "card so the user can check the units, quantities, costs and linked "
        "purchase order and then receive it into Loaded with a click. Pass the "
        "invoice_id (from list_stock_invoices / the outstanding list). This "
        "prepares a draft only — it never receives the invoice itself; the user "
        "does that from the card."
    ),
    "required_fields": ["invoice_id"],
    "field_descriptions": {
        "invoice_id": "The Loaded invoice id to receive (from list_stock_invoices).",
    },
    "field_schema": {},
    "consolidator_config": {
        # function_code injected from PREPARE_FUNCTION_CODE_PATH at sync time
        "max_api_calls": 10,
    },
    # Materialise a working document from the shaped result, then render the
    # editor over it — the tool loop keys off this config (tool_loop.py:509).
    "working_document": {
        "doc_type": "received_invoice",
        "sync_mode": "submit",
        "ref_fields": ["invoice_id"],
    },
    "display_component": "receive_invoice_editor",
    "display_props": {"title": "Receive Invoice"},
}

RECEIVE_ONE_PLAYBOOK = {
    "slug": "receive_loadedhub_invoice",
    "agent_slug": "procurement",
    "display_name": "Receive a Supplier Invoice",
    "description": (
        "Open a specific outstanding supplier invoice as an editable Receive "
        "Invoice card and receive it into Loaded."
    ),
    "instructions": """Goal: help the user receive ONE specific supplier invoice.

1. Identify the invoice. If the user named it (a reference number, supplier, or "the latest from X"), call list_stock_invoices (status NotReceived) for the venue and find the matching invoice's id. If several match, show the candidates (reference, supplier, date, total) and ask which one — do not guess.
2. Call receive_loadedhub_invoice with that invoice_id. This opens an editable **Receive Invoice** card: units, quantities, unit costs and the linked purchase order, pre-filled from Loaded.
3. Tell the user the card is ready below and that they review it, adjust anything that needs it, then click **Accept & Receive** to receive the invoice into Loaded. NEVER say you have received it — only the user's click on the card does that.

Do not link POs, edit lines, or receive invoices yourself in prose — everything happens on the card. If the user wants to review ALL outstanding invoices at once instead, that is the separate review-and-receive workflow.""",
    "tool_filter": [
        "receive_loadedhub_invoice",
        "list_stock_invoices",
        "get_invoice_detail",
    ],
    "enabled": True,
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
    "instructions": """Goal: review the venue's outstanding supplier invoices. What gets received automatically is decided by the tool's deterministic checks AND the user's run mode — you never decide what gets received.

RUN MODE — DO THIS FIRST, before running the review. This workflow honours a per-user run mode, and you must NOT run the review until it is set:
0. Call get_workflow_mode with workflow="review_and_receive_invoices".
   - If it returns mode "unset": DO NOT run the review. Ask the user to choose their default mode and STOP for their answer:
     • **approve all** — Norm changes nothing without your OK (everything is presented on cards to approve);
     • **approve fixes** — Norm auto-receives the exact matches; anything needing a fix waits on a card for you;
     • **autopilot** — Norm also auto-applies the fixes it can resolve confidently and receives them; anything ambiguous still waits for you.
     When they answer, call set_workflow_mode with workflow="review_and_receive_invoices" and their choice, confirm it briefly, THEN continue to step 1.
   - If it returns a set mode: go straight to step 1 (the review runs in that mode automatically). The user can change it any time by asking — call set_workflow_mode.

1. Call review_and_receive_invoices for the venue (default range: last 60 days) — do NOT pass any dry_run or mode param; the run mode alone governs what is written. Before calling it, write at most ONE short status line (e.g. "Reviewing the outstanding invoices…") — the full report comes after the tool returns.
2. Write a SHORT summary — a few sentences, no audit tables. From the tool's results: how many invoices were reviewed; how many were received automatically (in approve-all mode say "ready to approve" instead — nothing was written); how many await the user on the cards below and why in one line each (e.g. "109738996 — $0 duplicate line to strike", "CN-19980 — duplicate of an already-received invoice"), using the returned reasons — never invent or soften them. Skipped invoices with no card (fetch failures, credit notes) get one bold line each with the tool's reason.
3. Below your summary there is one compact **Receive Invoice** card per invoice that needs the user. Each card shows its suggested changes (Accept per change), what needs attention, and **Accept & Receive**; it expands to the full invoice. Close with one sentence pointing the user at the cards. If the result's `auto_submit` is true (**autopilot**), say the confident fixes apply automatically and the rest wait on the cards. NEVER claim you have applied or received anything — the user (or autopilot) does that from the cards.

If the user asks why a specific invoice was skipped, use get_invoice_detail together with the returned reasons — do not guess. Never suggest you can link POs, edit lines, or force-receive an invoice; that is done in Loaded by a person.""",
    "tool_filter": [
        "review_and_receive_invoices",
        "list_stock_invoices",
        "get_invoice_detail",
        "get_stock_purchase_order",
        "get_workflow_mode",
        "set_workflow_mode",
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
    "instructions": """Goal: reconcile the venue's received supplier invoices against their supplier statements. What gets reconciled automatically is decided by the tool's deterministic checks AND the user's run mode — you never decide.

RUN MODE — DO THIS FIRST, before reconciling. Do NOT run the reconciliation until the mode is set:
0. Call get_workflow_mode with workflow="reconcile_received_invoices".
   - If it returns mode "unset": DO NOT run the tool. Ask the user to choose their default mode and STOP for their answer: **approve all** (nothing is written — the report is for review), **approve fixes** (auto-reconcile the exact matches; creating a missing statement needs your OK), or **autopilot** (also auto-create missing statements). When they answer, call set_workflow_mode with workflow="reconcile_received_invoices" and their choice, confirm it, THEN continue to step 1.
   - If it returns a set mode: go straight to step 1. The user can change it any time by asking — call set_workflow_mode.

1. Call reconcile_received_invoices for the venue (default window: last 30 days of statements) — do NOT pass any dry_run or mode param; the run mode alone governs what is written. Before calling it, write at most ONE short status line — the full report comes after the tool returns.
2. Report the results, using the tool's exact values and reasons verbatim (never soften or re-derive them). Start with a compact markdown summary table built from the tool's results rows — | Invoice | Supplier | Statement | Total | Outcome | — one row per invoice. Then three sections:
   - "Reconciled" (in approve-all mode these read "awaiting your approval") and "Could not reconcile": for EVERY invoice render a markdown comparison table from the tool's comparison data showing the actual values checked on each side — | Field | Received invoice (Loaded) | Invoice copy | Match | — with rows for invoice number, PO number, invoice date, and total incl tax. The Match cell comes from the field's `match` value: true → ✓, false → ✗, null → — (check not run). Copy the values exactly as returned; never invent or reformat them.
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
        "get_workflow_mode",
        "set_workflow_mode",
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
    "receive_loadedhub_invoice",
    "list_stock_invoices",
    "get_invoice_detail",
    "get_stock_purchase_order",
    "list_purchase_orders",
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
    prepare_receive = dict(PREPARE_RECEIVE_TOOL)
    prepare_receive["consolidator_config"] = {
        **PREPARE_RECEIVE_TOOL["consolidator_config"],
        "function_code": PREPARE_FUNCTION_CODE_PATH.read_text(encoding="utf-8"),
    }
    desired_tools = {
        t["action"]: t
        for t in [
            *SPEC_TOOLS,
            *RECONCILE_SPEC_TOOLS,
            consolidator,
            reconcile_consolidator,
            prepare_receive,
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

        for playbook_def in (PLAYBOOK, RECONCILE_PLAYBOOK, RECEIVE_ONE_PLAYBOOK):
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
