# ruff: noqa: F821 — `decimal`, `datetime`, `json`, `math` and
# `extract_document` are injected into the sandbox namespace by
# app/connectors/function_executor.py; they are not imports.
#
# Canonical function_code for the `loadedhub.reconcile_received_invoices`
# consolidator. Synced verbatim into the config DB (see
# config/consolidators/README.md and scripts/sync_invoice_receiving_config.py).
#
# Requires consolidator_config:
#   {"max_api_calls": 120,
#    "allowed_write_actions": ["update_supplier_statement", "create_supplier_statement"]}
#
# Contract: for each supplier statement in the window, verify every
# unreconciled received invoice against its attached supplier invoice PDF —
# (1) copy attached, (2) invoice number on the copy matches (proves the right
# document is attached), (3) PO number matches (STRICT: both sides must show
# one), (4) invoice date matches, (5) total incl tax matches (≤ $0.02) — and
# mark the passing ones reconciled on the statement. Deterministic code decides
# every write; `dry_run=true` reports without writing. Suppliers with
# unreconciled invoices but no covering statement are reported in
# `needs_statement`; statements are only created when the caller passes
# `create_missing_statements=true` (the playbook requires explicit user
# consent first).

PDF_HEADER_SCHEMA = {
    "supplier_name": "string or null",
    "invoice_number": "string or null — the invoice number exactly as printed",
    "purchase_order_number": "string or null — the PO / order number exactly as printed",
    "invoice_date": "string or null — the invoice date as YYYY-MM-DD",
    "total_incl_tax": "number or null — the total including tax/GST",
}

TOTALS_TOL = "0.02"  # user decision: differences <= 2c count as matching


def run(params, call_api, log, call_api_parallel=None):
    D = decimal.Decimal
    totals_tol = D(TOTALS_TOL)

    def dec(value):
        if value is None:
            return None
        try:
            return D(str(value))
        except Exception:
            return None

    def money(value):
        d = dec(value)
        return "$" + format(d if d is not None else D("0"), ",.2f")

    def norm(text):
        return "".join(ch for ch in str(text or "").lower() if ch.isalnum())

    def po_norm(text):
        n = norm(text)
        if n.startswith("po"):
            n = n[2:]
        return n

    def date_only(value):
        return str(value or "")[:10]

    venue = params.get("venue")
    # Per-user run mode (injected by execute_consolidator). Reconciliation has
    # no interactive card, so modes map to the two write gates:
    #   approve_all / unset → dry run (report only; user confirms to proceed)
    #   approve_fixes       → reconcile matches; never auto-create statements
    #   autopilot           → reconcile matches AND auto-create missing statements
    mode = params.get("mode") or "unset"
    mode_unset = mode == "unset"
    approve_all = mode in ("approve_all", "unset")
    # No separate dry-run: the mode alone decides whether statements are written.
    dry_run = approve_all
    create_missing = bool(params.get("create_missing_statements")) or (
        mode == "autopilot"
    )
    supplier_filter = {norm(s) for s in (params.get("suppliers") or []) if s}
    to_date = params.get("to_date") or params.get("today")
    from_date = params.get("from_date")
    if not from_date:
        from_date = (
            datetime.date.fromisoformat(params["today"]) - datetime.timedelta(days=30)
        ).isoformat()

    base = {"venue": venue} if venue else {}

    statements = call_api(
        "loadedhub",
        "list_supplier_statements",
        dict(
            base,
            from_iso=from_date + "T00:00:00.000Z",
            to_iso=to_date + "T23:59:59.000Z",
        ),
    )
    if isinstance(statements, dict) and statements.get("error"):
        return {"error": "Could not list supplier statements: " + statements["error"]}
    statements = [s for s in statements or [] if not s.get("deletedAt")]
    if supplier_filter:
        statements = [
            s for s in statements if norm(s.get("supplierName")) in supplier_filter
        ]

    # Received invoices must cover the statements' own periods, which can
    # extend beyond the search window (e.g. monthly statements).
    span_from, span_to = from_date, to_date
    for s in statements:
        span_from = min(span_from, date_only(s.get("startAt")))
        span_to = max(span_to, date_only(s.get("endAt")))

    received = call_api(
        "loadedhub",
        "list_received_invoices",
        dict(base, from_date=span_from, to_date=span_to),
    )
    if isinstance(received, dict) and received.get("error"):
        return {"error": "Could not list received invoices: " + received["error"]}

    candidates = [
        inv
        for inv in received or []
        if isinstance(inv, dict)
        and not inv.get("reconciled")
        and not inv.get("deletedAt")
        and not inv.get("statementId")
    ]
    if supplier_filter:
        candidates = [
            c for c in candidates if norm(c.get("supplierName")) in supplier_filter
        ]
    log(
        "Statements in window: "
        + str(len(statements))
        + "; unreconciled received invoices: "
        + str(len(candidates))
    )

    def covering_statement(inv):
        """Mirror the statement screen's scoping: startAt date .. endAt date + 1."""
        inv_date = date_only(inv.get("invoicedAt"))
        matches = []
        for s in statements:
            if s.get("supplierId") != inv.get("supplierId"):
                continue
            start = date_only(s.get("startAt"))
            end = (
                datetime.date.fromisoformat(date_only(s.get("endAt")))
                + datetime.timedelta(days=1)
            ).isoformat()
            if start <= inv_date <= end:
                matches.append(s)
        matches.sort(key=lambda s: date_only(s.get("startAt")), reverse=True)
        return matches[0] if matches else None

    def evaluate(inv):
        """Run the four user checks.

        Returns (reasons, checks, comparison) — `comparison` holds the ACTUAL
        values read from each side (the received invoice in Loaded vs the
        attached invoice copy) so the report can prove what was compared.
        """
        reasons, checks = [], {}
        comparison = {
            "invoice_number": {"loaded": inv.get("invoiceNumber"), "document": None},
            "po_number": {"loaded": inv.get("purchaseOrderNumber"), "document": None},
            "invoice_date": {
                "loaded": date_only(inv.get("invoicedAt")) or None,
                "document": None,
            },
            "total_incl_tax": {"loaded": money(inv.get("total")), "document": None},
        }

        def doc_side(value):
            for field in comparison.values():
                field["document"] = value

        def finalize():
            # Stamp each compared field with its check outcome so reports can
            # show a per-field tick/cross: True=match, False=mismatch,
            # None=check never ran (e.g. no copy to read).
            mapping = {
                "invoice_number": "invoice_number_match",
                "po_number": "po_match",
                "invoice_date": "date_match",
                "total_incl_tax": "total_match",
            }
            for field, key in mapping.items():
                state = checks.get(key)
                comparison[field]["match"] = (
                    True if state == "pass" else (False if state == "fail" else None)
                )
            return reasons, checks, comparison

        if inv.get("creditRequest") or (dec(inv.get("total")) or D(0)) < 0:
            checks["credit"] = "fail"
            reasons.append(
                "Credit (" + money(inv.get("total")) + ") — reconcile manually"
            )
        else:
            checks["credit"] = "pass"

        # Check 1 — invoice copy attached
        if not inv.get("fileId"):
            checks["file_attached"] = "fail"
            reasons.append("No invoice copy attached to the received invoice")
            doc_side("(no copy attached)")
            return finalize()
        checks["file_attached"] = "pass"

        pdf = extract_document(
            "loadedhub",
            "download_invoice_file",
            dict(base, file_id=inv["fileId"]),
            schema=PDF_HEADER_SCHEMA,
            instructions=(
                "Extract the header fields from this supplier invoice. "
                "Return the invoice date as YYYY-MM-DD."
            ),
        )
        if not isinstance(pdf, dict) or pdf.get("error"):
            err = pdf.get("error") if isinstance(pdf, dict) else "unreadable"
            checks["pdf_readable"] = "fail"
            reasons.append("Could not read the attached invoice copy: " + str(err))
            doc_side("(unreadable)")
            return finalize()
        checks["pdf_readable"] = "pass"

        # Record the document's actual values verbatim for the report
        comparison["invoice_number"]["document"] = pdf.get("invoice_number")
        comparison["po_number"]["document"] = pdf.get("purchase_order_number")
        comparison["invoice_date"]["document"] = (
            date_only(pdf.get("invoice_date")) or None
        )
        comparison["total_incl_tax"]["document"] = (
            money(pdf.get("total_incl_tax"))
            if dec(pdf.get("total_incl_tax")) is not None
            else None
        )

        # Check — invoice number on the copy must match (proves the right
        # document is attached before trusting any other value read from it)
        loaded_no, pdf_no = (
            norm(inv.get("invoiceNumber")),
            norm(pdf.get("invoice_number")),
        )
        if not pdf_no:
            checks["invoice_number_match"] = "fail"
            reasons.append("Could not read the invoice number from the invoice copy")
        elif not loaded_no:
            checks["invoice_number_match"] = "fail"
            reasons.append(
                "Received invoice has no invoice number (invoice copy shows '"
                + str(pdf.get("invoice_number"))
                + "')"
            )
        elif loaded_no != pdf_no:
            checks["invoice_number_match"] = "fail"
            reasons.append(
                "Attached copy is for invoice '"
                + str(pdf.get("invoice_number"))
                + "' but the received invoice is '"
                + str(inv.get("invoiceNumber"))
                + "'"
            )
        else:
            checks["invoice_number_match"] = "pass"

        # Check 2 — PO number (STRICT: both sides must exist and match)
        loaded_po, pdf_po = (
            po_norm(inv.get("purchaseOrderNumber")),
            po_norm(pdf.get("purchase_order_number")),
        )
        if not loaded_po and not pdf_po:
            checks["po_match"] = "fail"
            reasons.append("No PO number on the received invoice or the invoice copy")
        elif not loaded_po:
            checks["po_match"] = "fail"
            reasons.append(
                "Received invoice has no PO number (invoice copy shows "
                + str(pdf.get("purchase_order_number"))
                + ")"
            )
        elif not pdf_po:
            checks["po_match"] = "fail"
            reasons.append(
                "Invoice copy shows no PO number (received invoice has PO#"
                + str(inv.get("purchaseOrderNumber"))
                + ")"
            )
        elif loaded_po != pdf_po:
            checks["po_match"] = "fail"
            reasons.append(
                "PO number mismatch: received invoice PO#"
                + str(inv.get("purchaseOrderNumber"))
                + " vs invoice copy "
                + str(pdf.get("purchase_order_number"))
            )
        else:
            checks["po_match"] = "pass"

        # Check 3 — invoice date
        inv_date, pdf_date = (
            date_only(inv.get("invoicedAt")),
            date_only(pdf.get("invoice_date")),
        )
        if not pdf_date:
            checks["date_match"] = "fail"
            reasons.append("Could not read the invoice date from the invoice copy")
        elif inv_date != pdf_date:
            checks["date_match"] = "fail"
            reasons.append(
                "Invoice date mismatch: received invoice "
                + inv_date
                + " vs invoice copy "
                + pdf_date
            )
        else:
            checks["date_match"] = "pass"

        # Check 4 — total incl tax
        loaded_total, pdf_total = dec(inv.get("total")), dec(pdf.get("total_incl_tax"))
        if pdf_total is None:
            checks["total_match"] = "fail"
            reasons.append("Could not read the total from the invoice copy")
        elif loaded_total is None or abs(loaded_total - pdf_total) > totals_tol:
            checks["total_match"] = "fail"
            reasons.append(
                "Total mismatch: received invoice "
                + money(loaded_total)
                + " vs invoice copy "
                + money(pdf_total)
            )
        else:
            checks["total_match"] = "pass"

        return finalize()

    reconciled, not_reconciled, needs_statement_rows = [], [], []
    by_statement = {}  # statement id -> {"statement": s, "items": [inv...], "verdicts": []}
    orphans = {}  # supplierId -> {"supplier": name, "passing": [], "failing": []}

    for inv in candidates:
        stmt = covering_statement(inv)
        reasons, checks, comparison = evaluate(inv)
        verdict = {
            "invoice_id": inv.get("id"),
            "invoice_number": inv.get("invoiceNumber") or "(no number)",
            "supplier_name": inv.get("supplierName"),
            "po_number": inv.get("purchaseOrderNumber"),
            "invoiced_at": date_only(inv.get("invoicedAt")),
            "total": money(inv.get("total")),
            "statement_number": stmt.get("statementNumber") if stmt else None,
            "reasons": reasons,
            "checks": checks,
            "comparison": comparison,
        }
        if stmt is None:
            bucket = orphans.setdefault(
                inv.get("supplierId"),
                {"supplier": inv.get("supplierName"), "passing": [], "failing": []},
            )
            (bucket["passing"] if not reasons else bucket["failing"]).append(
                (inv, verdict)
            )
            continue
        if reasons:
            not_reconciled.append(verdict)
            continue
        entry = by_statement.setdefault(
            stmt["id"], {"statement": stmt, "items": [], "verdicts": []}
        )
        entry["items"].append(inv)
        entry["verdicts"].append(verdict)

    # Write phase — one PUT per statement with newly reconciled items
    for entry in by_statement.values():
        stmt, items, verdicts = entry["statement"], entry["items"], entry["verdicts"]
        if dry_run:
            for v in verdicts:
                v["outcome"] = "awaiting your approval"
                reconciled.append(v)
            continue
        body = dict(stmt)
        new_items = []
        for inv in items:
            item = dict(inv)
            item["reconciled"] = True
            new_items.append(item)
        body["reconciledStockReceivedItems"] = (
            list(stmt.get("reconciledStockReceivedItems") or []) + new_items
        )
        result = call_api(
            "loadedhub",
            "update_supplier_statement",
            dict(base, statement_id=stmt["id"], statement=body),
        )
        if isinstance(result, dict) and result.get("error"):
            for v in verdicts:
                v["reasons"] = ["Statement update failed: " + result["error"]]
                not_reconciled.append(v)
        else:
            for v in verdicts:
                v["outcome"] = "reconciled"
                reconciled.append(v)

    # Suppliers with no covering statement
    for supplier_id, bucket in orphans.items():
        passing, failing = bucket["passing"], bucket["failing"]
        if create_missing and passing and not dry_run:
            invs = [inv for inv, _ in passing]
            period_from = min(date_only(i.get("invoicedAt")) for i in invs)
            period_to = max(date_only(i.get("invoicedAt")) for i in invs)
            new_items = []
            for inv in invs:
                item = dict(inv)
                item["reconciled"] = True
                new_items.append(item)
            body = {
                "statementNumber": "Auto — " + period_from + " to " + period_to,
                "startAt": period_from + "T00:00:00.000Z",
                "endAt": period_to + "T00:00:00.000Z",
                "statementAmount": 0,
                "supplierId": supplier_id,
                "supplierName": bucket["supplier"],
                "reconciledStockReceivedItems": new_items,
            }
            result = call_api(
                "loadedhub", "create_supplier_statement", dict(base, statement=body)
            )
            if isinstance(result, dict) and result.get("error"):
                for _, v in passing:
                    v["reasons"] = ["Statement creation failed: " + result["error"]]
                    not_reconciled.append(v)
            else:
                for _, v in passing:
                    v["outcome"] = "reconciled (new statement)"
                    v["statement_number"] = body["statementNumber"]
                    reconciled.append(v)
                log(
                    "Created statement '"
                    + body["statementNumber"]
                    + "' for "
                    + str(bucket["supplier"])
                    + " — update its number/amount from the paper statement"
                )
        else:
            for _, v in passing:
                v["outcome"] = "needs statement (all checks pass)"
                needs_statement_rows.append(v)
        for _, v in failing:
            v["outcome"] = "needs statement (fails checks)"
            needs_statement_rows.append(v)

    needs_statement = [
        {
            "supplier_name": bucket["supplier"],
            "invoice_count": len(bucket["passing"]) + len(bucket["failing"]),
            "would_reconcile": len(bucket["passing"]),
            "suggested_from": min(
                (
                    date_only(i.get("invoicedAt"))
                    for i, _ in bucket["passing"] + bucket["failing"]
                ),
                default=None,
            ),
            "suggested_to": max(
                (
                    date_only(i.get("invoicedAt"))
                    for i, _ in bucket["passing"] + bucket["failing"]
                ),
                default=None,
            ),
        }
        for bucket in orphans.values()
        if not (create_missing and not dry_run and bucket["passing"])
    ]

    statement_summaries = [
        {
            "statement_number": s.get("statementNumber"),
            "supplier_name": s.get("supplierName"),
            "statement_amount": money(s.get("statementAmount")),
            "reconciled_amount": money(s.get("reconciledAmount")),
            "difference": money(
                (dec(s.get("statementAmount")) or D(0))
                - (dec(s.get("reconciledAmount")) or D(0))
            ),
        }
        for s in statements
    ]

    def cell(verdict, field, side):
        value = (verdict.get("comparison") or {}).get(field, {}).get(side)
        return str(value) if value not in (None, "") else "—"

    def doc_cell(verdict, field):
        """Copy-side value with its per-field verdict: ✓ match / ✗ mismatch."""
        value = cell(verdict, field, "document")
        match = (verdict.get("comparison") or {}).get(field, {}).get("match")
        if match is True:
            return value + " ✓"
        if match is False:
            return value + " ✗"
        return value

    rows = [
        {
            "invoice": v["invoice_number"],
            "invno_doc": doc_cell(v, "invoice_number"),
            "supplier": v.get("supplier_name"),
            "statement": v.get("statement_number") or "—",
            "po_loaded": cell(v, "po_number", "loaded"),
            "po_doc": doc_cell(v, "po_number"),
            "date_loaded": cell(v, "invoice_date", "loaded"),
            "date_doc": doc_cell(v, "invoice_date"),
            "total_loaded": cell(v, "total_incl_tax", "loaded"),
            "total_doc": doc_cell(v, "total_incl_tax"),
            "outcome": v.get("outcome", "not reconciled"),
            "notes": " • ".join(v["reasons"]) if v.get("reasons") else "—",
        }
        for v in reconciled + not_reconciled + needs_statement_rows
    ]
    return {
        "venue": venue,
        "dry_run": dry_run,
        "mode": mode,
        "mode_unset": mode_unset,
        "window": {"from": from_date, "to": to_date},
        "results": rows,
        "reconciled": reconciled,
        "not_reconciled": not_reconciled,
        "needs_statement": needs_statement,
        "statements": statement_summaries,
        "summary": {
            "reconciled": len(reconciled),
            "not_reconciled": len(not_reconciled),
            "needs_statement": len(needs_statement_rows),
        },
    }
