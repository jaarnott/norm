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

# No private extraction schema here any more. Reading the copy is
# norm.invoice_copy_evidence's job: it returns what the RECEIVE flow already
# extracted for an invoice, and reads fresh only what is missing — with the
# same PDF_SCHEMA and the same per-supplier spec instructions that the dojo
# trains. The five-field schema this file used to carry asked for a single
# `purchase_order_number`, which is why an invoice printing the supplier's own
# order number above our PO read as a mismatch: 27 of 67 failures on 17 Aug
# 2026. It also hashed to a different cache key, so every copy the receive flow
# had already read was read and paid for again.

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

    def to_iso(value):
        # The invoice copy keeps dates AS PRINTED ('19/08/26', '19 Jul 26',
        # '18.08.2026', 'Jul 13, 2026' — all live-observed) while Loaded is
        # ISO, so a raw string compare failed EVERY differently-formatted
        # date: on 21 Aug 2026 the daily run reconciled 0 of 100 invoices,
        # the vast majority blocked only by format. Hand-rolled rather than
        # strptime: strptime lazily imports _strptime, which this sandbox
        # forbids. Numeric forms read day-first (NZ suppliers); with a month
        # name, the first remaining number is the day. Unparseable or
        # ambiguous text returns verbatim — an honest mismatch, never a
        # guess.
        s = " ".join(str(value or "").replace(",", " ").split())
        if not s:
            return ""
        try:
            return datetime.date.fromisoformat(s[:10]).isoformat()
        except ValueError:
            pass
        months = {
            "jan": 1,
            "feb": 2,
            "mar": 3,
            "apr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "oct": 10,
            "nov": 11,
            "dec": 12,
        }
        parts = s.replace(".", " ").replace("/", " ").replace("-", " ").split()
        if len(parts) != 3:
            return s
        month = None
        rest = []
        for part in parts:
            if not part.isdigit() and part[:3].lower() in months:
                month = months[part[:3].lower()]
            else:
                rest.append(part)
        try:
            if month is not None:
                if len(rest) != 2:
                    return s
                day, year = int(rest[0]), int(rest[1])
            else:
                day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            if year < 100:
                year += 2000
            return datetime.date(year, month, day).isoformat()
        except (TypeError, ValueError):
            return s

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
    # Optional `period` in plain English resolves through Norm's venue
    # calendar to CALENDAR dates (statement periods are calendar-dated).
    # The zero-arg default (last 30 days) is unchanged.
    period = (params.get("period") or "").strip()
    if period:
        resolve_args = {"query": period}
        if params.get("venue_id"):
            resolve_args["venue_id"] = params["venue_id"]
        resolved = call_api("norm", "resolve_dates", resolve_args)
        window = resolved.get("window") if isinstance(resolved, dict) else None
        if not isinstance(window, dict):
            data = resolved.get("data") if isinstance(resolved, dict) else None
            window = data.get("window") if isinstance(data, dict) else None
        if not isinstance(window, dict):
            return {"error": f"could not resolve '{period}' to dates"}
        from_date = str(window["start"])[:10]
        to_date = str(window["end"])[:10]
    else:
        to_date = params.get("to_date") or params.get("today")
        from_date = params.get("from_date")
        if not from_date:
            from_date = (
                datetime.date.fromisoformat(params["today"])
                - datetime.timedelta(days=30)
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

        Returns (reasons, checks, comparison, notes) — `comparison` holds the ACTUAL
        values read from each side (the received invoice in Loaded vs the
        attached invoice copy) so the report can prove what was compared.
        """
        # `reasons` stop a reconcile; `notes` explain one that went through
        # (e.g. "matched on the supplier's own order number"). Keeping them
        # apart is what lets an invoice reconcile AND still say why.
        reasons, notes, checks = [], [], {}
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
            return reasons, checks, comparison, notes

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

        pdf = evidence.get(str(inv.get("id"))) or {}
        if not isinstance(pdf, dict) or pdf.get("error"):
            err = pdf.get("error") if isinstance(pdf, dict) else "unreadable"
            checks["pdf_readable"] = "fail"
            # A transient failure says nothing about the document. Calling it
            # "unreadable" sent people hunting invoice copies that were fine —
            # 27 of 39 such findings on 24-25 Aug 2026 were the extraction
            # service being briefly unavailable, and every one of those copies
            # reads. Name what actually happened, and say it will retry.
            if isinstance(pdf, dict) and pdf.get("transient"):
                reasons.append(
                    "Could not check this invoice — the extraction service was "
                    "briefly unavailable. The copy is fine; the next run retries it."
                )
                doc_side("(not checked)")
            else:
                reasons.append("Could not read the attached invoice copy: " + str(err))
                doc_side("(unreadable)")
            return finalize()
        checks["pdf_readable"] = "pass"

        # Record the document's actual values verbatim for the report
        comparison["invoice_number"]["document"] = pdf.get("invoice_number")
        comparison["po_number"]["document"] = pdf.get(
            "customer_purchase_order_number"
        ) or pdf.get("supplier_order_number")
        comparison["invoice_date"]["document"] = to_iso(pdf.get("invoice_date")) or None
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

        # Check 2 — PO number. The copy carries OUR number and the supplier's
        # separately, so "sure" means Norm can tell them apart and one of them
        # equals what Loaded holds. Loaded's own purchaseOrderNumber is often
        # the supplier's number rather than a Loaded order, so a match on that
        # is still both sides naming the same document — it just says so.
        po_state = pdf.get("_po_verdict") or "mismatch"
        po_note = pdf.get("_po_note") or ""
        if po_state == "match":
            checks["po_match"] = "pass"
            if po_note:
                notes.append(po_note)
        else:
            checks["po_match"] = "fail"
            reasons.append(po_note or "PO number could not be matched")

        # Check 3 — invoice date
        inv_date, pdf_date = (
            date_only(inv.get("invoicedAt")),
            to_iso(pdf.get("invoice_date")),
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
    # Read every copy ONCE, up front, through the receive path's eyes: what
    # Norm already extracted when the invoice was received, and only what is
    # missing read fresh — with that supplier's own spec instructions, in
    # parallel, onto the same cache row the receive flow uses. This replaced a
    # serial per-invoice extraction with a private schema that could not tell
    # our PO number from the supplier's.
    ev = call_api(
        "norm",
        "invoice_copy_evidence",
        {
            "venue": venue,
            "invoices": [
                {
                    "id": c.get("id"),
                    "fileId": c.get("fileId"),
                    "supplierName": c.get("supplierName"),
                    # The account's supplier RECORD, not just its feed
                    # spelling. Without it the evidence service cannot reach
                    # that supplier's Loaded aliases, so it never matched the
                    # spec: 'Kaans Catering' found none while the spec is filed
                    # under "Kaan's Catering Supplies", and the copy was read
                    # with the generic prompt every single day.
                    "supplierId": c.get("supplierId"),
                    "purchaseOrderNumber": c.get("purchaseOrderNumber"),
                }
                for c in candidates
            ],
        },
    )
    # call_api returns the handler's `data` ALREADY unwrapped — see
    # function_executor._do_api_call: `return handler_result.get("data")`.
    # Reading .get("data") again found nothing, so every copy reported as
    # unreadable while the tests passed: their fake returned the HANDLER's
    # {"success", "data"} shape rather than what the sandbox actually hands in.
    # The sibling consolidator's norm.review_invoices call reads its result
    # flat; match it. On failure call_api yields {"error": ...}.
    if isinstance(ev, dict) and not ev.get("error"):
        evidence = ev
    else:
        evidence = {}
        log(
            "Could not read the invoice copies: "
            + str(ev.get("error") if isinstance(ev, dict) else ev)
        )

    # Confirmed split deliveries. The reconcile decision is already made (the
    # evidence service established it from Loaded), so this only PERSISTS it:
    # a durable note, plus a best-effort PO reference for whoever reads Loaded.
    # Under approve_all nothing is written and the fix is reported instead.
    split_fixes = []
    for c in candidates:
        head = evidence.get(str(c.get("id"))) or {}
        sp = head.get("_split") or {}
        if sp.get("kind") != "split":
            continue
        split_fixes.append(
            {
                "id": c.get("id"),
                "invoice_number": c.get("invoiceNumber") or "(no number)",
                "order_number": sp.get("order_number"),
                "sibling_reference": sp.get("sibling_reference"),
            }
        )

    split_suggestions, split_applied = [], []
    if split_fixes and dry_run:
        split_suggestions = [
            {
                "invoice": f["invoice_number"],
                "fix": (
                    "record split order "
                    + str(f["order_number"])
                    + " (also covers "
                    + str(f["sibling_reference"])
                    + ")"
                ),
            }
            for f in split_fixes
        ]
        log(
            str(len(split_fixes))
            + " split order(s) could be recorded on the invoice — run mode is "
            "approve_all, so nothing was written"
        )
    elif split_fixes:
        res = call_api(
            "norm",
            "record_split_order",
            {
                "venue": venue,
                "invoices": [
                    {
                        "id": f["id"],
                        "order_number": f["order_number"],
                        "sibling_reference": f["sibling_reference"],
                    }
                    for f in split_fixes
                ],
            },
        )
        if isinstance(res, dict) and res.get("error"):
            log("Could not record split orders: " + str(res["error"]))
        else:
            for f in split_fixes:
                r = (res or {}).get(str(f["id"])) or {}
                if r.get("ok") and not r.get("unchanged"):
                    split_applied.append(f["invoice_number"])
            if split_applied:
                log("recorded split order on " + ", ".join(split_applied))

    by_statement = {}  # statement id -> {"statement": s, "items": [inv...], "verdicts": []}
    orphans = {}  # supplierId -> {"supplier": name, "passing": [], "failing": []}

    for inv in candidates:
        stmt = covering_statement(inv)
        reasons, checks, comparison, notes = evaluate(inv)
        verdict = {
            "invoice_id": inv.get("id"),
            "invoice_number": inv.get("invoiceNumber") or "(no number)",
            "supplier_name": inv.get("supplierName"),
            "po_number": inv.get("purchaseOrderNumber"),
            "invoiced_at": date_only(inv.get("invoicedAt")),
            "total": money(inv.get("total")),
            "statement_number": stmt.get("statementNumber") if stmt else None,
            "reasons": reasons,
            "notes": notes,
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
            "notes": " • ".join(v.get("reasons") or v.get("notes") or []) or "—",
        }
        for v in reconciled + not_reconciled + needs_statement_rows
    ]
    # ── The report the chat/email is written from ────────────────────────
    # Everything above is the CARD's data — every invoice, every comparison.
    # This is the part a person reads: what needs doing, grouped by the job
    # rather than by venue, because the job is the same job. On the 29 Aug run
    # three invoices across three venues all needed the same fix (a PO added in
    # Loaded) and appeared in three separate sections, under eight tables of
    # ticks for the invoices that were already fine.
    #
    # Classified from `checks`/`comparison`, never from the reason prose: the
    # wording changes, the structure does not.
    _CAUSES = [
        (
            "credit_manual",
            "Credits — reconcile by hand",
            "Loaded cannot auto-reconcile a credit",
        ),
        (
            "po_missing_in_loaded",
            "Needs a PO number added in Loaded",
            "the copy shows the order number; the received invoice has none",
        ),
        ("po_mismatch", "PO numbers disagree", ""),
        ("invoice_number_mismatch", "Invoice numbers disagree", ""),
        ("date_mismatch", "Invoice dates disagree", ""),
        ("total_mismatch", "Totals disagree", ""),
        ("no_copy", "No invoice copy attached", "attach the copy in Loaded"),
        ("copy_unreadable", "Invoice copy could not be read", ""),
        (
            "not_checked",
            "Not checked this run",
            "the extraction service was briefly unavailable; the next run retries",
        ),
        ("other", "Could not reconcile", ""),
    ]

    def cause_of(v):
        ch, comp = v.get("checks") or {}, v.get("comparison") or {}
        # A credit needs a person whatever else is true of it, so it wins.
        if ch.get("credit") == "fail":
            return "credit_manual"
        if ch.get("file_attached") == "fail":
            return "no_copy"
        if ch.get("pdf_readable") == "fail":
            doc = str((comp.get("invoice_number") or {}).get("document") or "")
            return "not_checked" if "not checked" in doc else "copy_unreadable"
        if ch.get("po_match") == "fail":
            po = comp.get("po_number") or {}
            # The fixable shape: Loaded holds nothing, the copy holds a number.
            if (
                not str(po.get("loaded") or "").strip()
                and str(po.get("document") or "").strip()
            ):
                return "po_missing_in_loaded"
            return "po_mismatch"
        for key, cause in (
            ("invoice_number_match", "invoice_number_mismatch"),
            ("date_match", "date_mismatch"),
            ("total_match", "total_mismatch"),
        ):
            if ch.get(key) == "fail":
                return cause
        return "other"

    def detail_of(v, cause):
        comp = v.get("comparison") or {}
        if cause == "po_missing_in_loaded":
            return "copy shows " + str((comp.get("po_number") or {}).get("document"))
        field = {
            "po_mismatch": "po_number",
            "invoice_number_mismatch": "invoice_number",
            "date_mismatch": "invoice_date",
            "total_mismatch": "total_incl_tax",
        }.get(cause)
        if field:
            c = comp.get(field) or {}
            return (
                "Loaded " + str(c.get("loaded")) + " vs copy " + str(c.get("document"))
            )
        return (v.get("reasons") or [""])[0]

    by_cause = {}
    for v in not_reconciled + needs_statement_rows:
        c = cause_of(v)
        by_cause.setdefault(c, []).append(
            {
                "venue": venue,
                "invoice": v.get("invoice_number"),
                "supplier": v.get("supplier_name"),
                "total": v.get("total"),
                "detail": detail_of(v, c),
            }
        )
    exceptions = [
        {"cause": c, "title": title, "hint": hint, "invoices": by_cause[c]}
        for c, title, hint in _CAUSES
        if c in by_cause
    ]

    # A statement that has not been issued yet reads as "$0.00 vs reconciled",
    # which is not a discrepancy — it is the month in progress. La Zeppa on
    # 28 Aug returned 67 such rows for two invoices of real work. Count them;
    # list only the differences a person could act on.
    # A cent or two either way is rounding across a month of invoices, not a
    # discrepancy — the old report carried lines like "$5,377.56 vs $5,377.55 →
    # $0.01" beside a real $4,045 crossover, which is how a real one gets
    # missed. Count them; list what a person could act on.
    _ROUNDING = D("1.00")
    differences, not_yet_issued, rounding = [], 0, 0
    for s in statements:
        amount = dec(s.get("statementAmount")) or D(0)
        reconciled_amt = dec(s.get("reconciledAmount")) or D(0)
        gap = amount - reconciled_amt
        if not gap:
            continue
        if not amount and reconciled_amt:
            not_yet_issued += 1
            continue
        if abs(gap) < _ROUNDING:
            rounding += 1
            continue
        differences.append(
            {
                "venue": venue,
                "statement": s.get("statementNumber"),
                "supplier": s.get("supplierName"),
                "statement_amount": money(amount),
                "reconciled_amount": money(reconciled_amt),
                "difference": money(gap),
            }
        )

    report = {
        "venue": venue,
        "counts": {
            "reconciled": len(reconciled),
            "not_reconciled": len(not_reconciled),
            "needs_statement": len(needs_statement_rows),
            "invoices": len(reconciled)
            + len(not_reconciled)
            + len(needs_statement_rows),
        },
        "exceptions": exceptions,
        "statement_differences": differences,
        "statements_not_yet_issued": not_yet_issued,
        "statements_off_by_rounding": rounding,
        "needs_statement": needs_statement,
    }

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
        # What was recorded, or what WOULD be under a writing mode — so the
        # report can offer the fix rather than only naming the problem.
        "split_orders_recorded": split_applied,
        "split_orders_suggested": split_suggestions,
        "statements": statement_summaries,
        # What the chat/email is written from — see the block above.
        "report": report,
        "summary": {
            "reconciled": len(reconciled),
            "not_reconciled": len(not_reconciled),
            "needs_statement": len(needs_statement_rows),
        },
    }
