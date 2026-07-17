# ruff: noqa: F821 — `decimal`, `datetime`, `json`, `math` and
# `extract_document` are injected into the sandbox namespace by
# app/connectors/function_executor.py; they are not imports.
#
# Canonical function_code for the `loadedhub.review_and_receive_invoices`
# consolidator. This file is the reviewed, version-controlled source of truth;
# its contents are synced verbatim into the ConnectorSpec tool's
# consolidator_config.function_code in the config DB (see
# config/consolidators/README.md).
#
# Runs inside the consolidator sandbox (app/connectors/function_executor.py):
# no imports; `math`, `json`, `datetime`, `decimal` modules and the
# `extract_document(...)` helper are injected. Requires consolidator_config:
#   {"max_api_calls": 80, "allowed_write_actions": ["receive_invoice"]}
#
# Contract: reviews draft (unreceived) supplier invoices and AUTOMATICALLY
# RECEIVES the ones that pass every deterministic gate below. The LLM never
# decides what is written — this code does. `dry_run=true` reports without
# writing. Invoices failing any gate are never modified and are reported with
# exact reasons.

PDF_SCHEMA = {
    "supplier_name": "string or null",
    "invoice_number": "string or null",
    "invoice_date": "string or null",
    "purchase_order_number": "string or null",
    "lines": [
        {
            "code": "string or null — the product/item code column",
            "description": "string",
            "quantity": "number — exactly as printed",
            "unit": "string or null",
            "unit_price_ex_tax": "number — exactly as printed",
            "line_total_ex_tax": "number — exactly as printed",
        }
    ],
    "charges": [
        {
            "description": "string — non-product charges e.g. Freight, Credit Card Fee",
            "amount_ex_tax": "number",
        }
    ],
    "subtotal_ex_tax": "number or null",
    "tax_amount": "number or null",
    "total_incl_tax": "number or null",
}

TOTALS_TOL = "0.02"  # user decision: differences <= 2c count as matching
LINE_TOL = "0.01"


def run(params, call_api, log, call_api_parallel=None):
    D = decimal.Decimal
    totals_tol = D(TOTALS_TOL)
    line_tol = D(LINE_TOL)

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

    def close(a, b, tol):
        if a is None or b is None:
            return False
        return abs(a - b) <= tol

    def norm(text):
        return "".join(ch for ch in str(text or "").lower() if ch.isalnum())

    venue = params.get("venue")
    dry_run = bool(params.get("dry_run"))
    to_date = params.get("to_date") or params.get("today")
    from_date = params.get("from_date")
    if not from_date:
        from_date = (
            datetime.date.fromisoformat(params["today"]) - datetime.timedelta(days=60)
        ).isoformat()

    base = {"venue": venue} if venue else {}

    invoices = call_api(
        "loadedhub",
        "list_stock_invoices",
        dict(base, from_date=from_date, to_date=to_date, page=0, pageSize=100),
    )
    if isinstance(invoices, dict) and invoices.get("error"):
        return {"error": "Could not list invoices: " + invoices["error"]}
    if isinstance(invoices, dict):
        invoices = invoices.get("data") or []

    drafts = [
        inv
        for inv in invoices
        if isinstance(inv, dict)
        and not inv.get("isReceived")
        and not inv.get("deletedAt")
    ]
    log(
        "Drafts to review: "
        + str(len(drafts))
        + " of "
        + str(len(invoices))
        + " listed"
    )

    received, skipped = [], []

    for stub in drafts:
        inv_id = stub.get("id")
        detail = call_api(
            "loadedhub", "get_invoice_detail", dict(base, invoice_id=inv_id)
        )
        if isinstance(detail, dict) and detail.get("error"):
            skipped.append(
                _verdict(stub, ["Could not fetch invoice detail: " + detail["error"]])
            )
            continue

        reasons = []
        checks = {}
        ref = detail.get("referenceNumber") or "(no number)"
        total = dec(detail.get("total"))
        lines = [ln for ln in detail.get("lines") or [] if not ln.get("deletedAt")]

        # Gate 2: credit notes are out of scope
        if total is not None and total < 0:
            _fail(
                checks,
                reasons,
                "credit_note",
                "Credit note (total "
                + money(total)
                + ") — out of scope for auto-receiving",
            )
        else:
            checks["credit_note"] = "pass"

        # Gate 3: must be automatched to a purchase order
        po = None
        po_id = detail.get("linkedPurchaseOrderId")
        po_number_hint = detail.get("purchaseOrderNumber")
        if not po_id:
            msg = "Not linked to a purchase order"
            if po_number_hint:
                msg += (
                    " (invoice references "
                    + str(po_number_hint)
                    + " — needs matching in Loaded)"
                )
            _fail(checks, reasons, "po_linked", msg)
        else:
            checks["po_linked"] = "pass"
            po = call_api(
                "loadedhub",
                "get_stock_purchase_order",
                dict(base, purchase_order_id=po_id),
            )
            if isinstance(po, dict) and po.get("error"):
                _fail(
                    checks,
                    reasons,
                    "po_fetch",
                    "Could not fetch linked purchase order: " + po["error"],
                )
                po = None

        # Gate 4: PO supplier must match the invoice supplier
        if po:
            if (
                po.get("supplierId")
                and detail.get("linkedSupplierId")
                and po["supplierId"] != detail["linkedSupplierId"]
            ):
                _fail(
                    checks,
                    reasons,
                    "po_supplier",
                    "Purchase order supplier ("
                    + str(po.get("supplierName"))
                    + ") does not match invoice supplier ("
                    + str(detail.get("supplierName"))
                    + ")",
                )
            else:
                checks["po_supplier"] = "pass"

        # Gate 7: every line must be matched to a stock item + unit
        unmatched = [
            ln
            for ln in lines
            if not ln.get("linkedItemId") or not ln.get("linkedUnitId")
        ]
        if unmatched:
            names = ", ".join(
                "'" + str(ln.get("description") or ln.get("code")) + "'"
                for ln in unmatched[:5]
            )
            _fail(
                checks,
                reasons,
                "items_matched",
                str(len(unmatched))
                + " line item(s) not matched to stock items: "
                + names,
            )
        else:
            checks["items_matched"] = "pass"

        # Gates 5+6: line-by-line vs the purchase order
        if po:
            po_lines = {ln.get("itemId"): ln for ln in po.get("lines") or []}
            line_set_ok, per_line_ok = True, True
            for ln in lines:
                pol = po_lines.get(ln.get("linkedItemId"))
                if not pol:
                    line_set_ok = False
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "' has no matching purchase-order line"
                    )
                    continue
                # unit must be identical
                if pol.get("unitId") != ln.get("linkedUnitId"):
                    per_line_ok = False
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "': unit differs from PO ("
                        + str(ln.get("unit"))
                        + " vs "
                        + str(pol.get("unitName"))
                        + ")"
                    )
                # unit cost must match the PO price (±1c)
                if not close(
                    dec(ln.get("unitCost")), dec(pol.get("unitCost")), line_tol
                ):
                    per_line_ok = False
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "': unit cost "
                        + money(ln.get("unitCost"))
                        + " differs from PO price "
                        + money(pol.get("unitCost"))
                    )
                # quantity variance is allowed (catch weight) but reported
                qo, qr = (
                    dec(pol.get("quantityOrdered")),
                    dec(ln.get("quantityReceived")),
                )
                if qo is not None and qr is not None and qo != qr:
                    log(
                        ref
                        + ": quantity variance on '"
                        + str(ln.get("description"))
                        + "' — "
                        + str(qo)
                        + " ordered / "
                        + str(qr)
                        + " billed (allowed, PDF-verified)"
                    )
            checks["po_line_set"] = "pass" if line_set_ok else "fail"
            checks["po_line_fields"] = "pass" if per_line_ok else "fail"

        # Gate 8: per-line arithmetic
        arith_ok = True
        for ln in lines:
            q, uc, tc = (
                dec(ln.get("quantityReceived")),
                dec(ln.get("unitCost")),
                dec(ln.get("totalCost")),
            )
            if q is None or uc is None or tc is None or not close(q * uc, tc, line_tol):
                arith_ok = False
                reasons.append(
                    "Line '"
                    + str(ln.get("description"))
                    + "': "
                    + str(q)
                    + " × "
                    + money(uc)
                    + " = "
                    + money((q or D(0)) * (uc or D(0)))
                    + " but line total is "
                    + money(tc)
                )
        checks["line_arithmetic"] = "pass" if arith_ok else "fail"

        # Gate 10: the supplier's PDF must be attached
        pdf = None
        if not detail.get("fileId"):
            _fail(
                checks,
                reasons,
                "pdf_present",
                "No invoice document attached — cannot verify against the source invoice",
            )
        else:
            checks["pdf_present"] = "pass"
            # Gate 9: extract the PDF and compare line-by-line
            pdf = extract_document(
                "loadedhub",
                "download_invoice_file",
                dict(base, file_id=detail["fileId"]),
                schema=PDF_SCHEMA,
                instructions="Extract every product line, every separate charge (freight etc.), and the totals from this supplier invoice.",
            )
            if not isinstance(pdf, dict) or pdf.get("error"):
                err = pdf.get("error") if isinstance(pdf, dict) else "unreadable"
                _fail(
                    checks,
                    reasons,
                    "pdf_readable",
                    "Could not read the attached invoice document: " + str(err),
                )
                pdf = None

        if pdf:
            pdf_ok = True
            pdf_lines = list(pdf.get("lines") or [])
            unclaimed = list(pdf_lines)
            for ln in lines:
                match = None
                for cand in unclaimed:
                    if norm(cand.get("code")) and norm(cand.get("code")) == norm(
                        ln.get("code")
                    ):
                        match = cand
                        break
                if match is None:
                    for cand in unclaimed:
                        if norm(cand.get("description")) and (
                            norm(cand.get("description")) in norm(ln.get("description"))
                            or norm(ln.get("description"))
                            in norm(cand.get("description"))
                        ):
                            match = cand
                            break
                if match is None:
                    pdf_ok = False
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "' not found on the attached invoice document"
                    )
                    continue
                unclaimed.remove(match)
                if dec(match.get("quantity")) != dec(ln.get("quantityReceived")):
                    pdf_ok = False
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "': quantity "
                        + str(ln.get("quantityReceived"))
                        + " does not equal the document's quantity "
                        + str(match.get("quantity"))
                    )
                if dec(match.get("unit_price_ex_tax")) != dec(ln.get("unitCost")):
                    pdf_ok = False
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "': unit cost "
                        + money(ln.get("unitCost"))
                        + " does not equal the document's unit price "
                        + money(match.get("unit_price_ex_tax"))
                    )
                if not close(
                    dec(match.get("line_total_ex_tax")),
                    dec(ln.get("totalCost")),
                    line_tol,
                ):
                    pdf_ok = False
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "': line total "
                        + money(ln.get("totalCost"))
                        + " does not equal the document's line total "
                        + money(match.get("line_total_ex_tax"))
                    )
            for cand in unclaimed:
                pdf_ok = False
                reasons.append(
                    "Document line '"
                    + str(cand.get("description"))
                    + "' ("
                    + money(cand.get("line_total_ex_tax"))
                    + ") has no matching invoice line"
                )
            for charge in pdf.get("charges") or []:
                amt = dec(charge.get("amount_ex_tax"))
                if amt and amt != D(0):
                    pdf_ok = False
                    reasons.append(
                        "Document includes charge '"
                        + str(charge.get("description"))
                        + "' "
                        + money(amt)
                        + " with no matching invoice line"
                    )
            checks["pdf_lines"] = "pass" if pdf_ok else "fail"

            # Gate 11 (PDF side): document total vs invoice total
            if not close(dec(pdf.get("total_incl_tax")), total, totals_tol):
                _fail(
                    checks,
                    reasons,
                    "pdf_total",
                    "Invoice total "
                    + money(total)
                    + " does not match the document total "
                    + money(pdf.get("total_incl_tax")),
                )
            else:
                checks["pdf_total"] = "pass"

        # Gate 11: internal totals
        subtotal, tax = dec(detail.get("subtotal")), dec(detail.get("taxAmount"))
        line_sum = sum((dec(ln.get("totalCost")) or D(0)) for ln in lines)
        if not close(line_sum, subtotal, totals_tol):
            _fail(
                checks,
                reasons,
                "totals",
                "Line items sum to "
                + money(line_sum)
                + " but the invoice subtotal is "
                + money(subtotal)
                + " (difference "
                + money(abs(line_sum - (subtotal or D(0))))
                + ")",
            )
        elif not close((subtotal or D(0)) + (tax or D(0)), total, totals_tol):
            _fail(
                checks,
                reasons,
                "totals",
                "Subtotal "
                + money(subtotal)
                + " + tax "
                + money(tax)
                + " = "
                + money((subtotal or D(0)) + (tax or D(0)))
                + " but the invoice total is "
                + money(total),
            )
        else:
            checks["totals"] = "pass"

        verdict = {
            "invoice_id": inv_id,
            "reference_number": ref,
            "supplier_name": detail.get("supplierName"),
            "po_number": (po or {}).get("orderNumber") or po_number_hint,
            "total": money(total),
            "reasons": reasons,
            "checks": checks,
        }

        if reasons:
            skipped.append(verdict)
            continue

        if dry_run:
            verdict["outcome"] = "would receive (dry run)"
            received.append(verdict)
            continue

        body = dict(detail)
        body["isReceived"] = True
        body["receivedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result = call_api(
            "loadedhub", "receive_invoice", dict(base, invoice_id=inv_id, invoice=body)
        )
        if isinstance(result, dict) and result.get("error"):
            verdict["reasons"] = ["Receive failed: " + result["error"]]
            skipped.append(verdict)
        elif isinstance(result, dict) and not result.get("isReceived"):
            verdict["reasons"] = [
                "Receive call succeeded but Loaded did not mark the invoice as received"
            ]
            skipped.append(verdict)
        else:
            verdict["outcome"] = "received"
            received.append(verdict)

    rows = [
        {
            "reference": v["reference_number"],
            "supplier": v.get("supplier_name"),
            "po": v.get("po_number") or "—",
            "total": v["total"],
            "outcome": v.get("outcome", "skipped"),
            "reasons": "; ".join(v["reasons"]) if v.get("reasons") else "—",
        }
        for v in received + skipped
    ]
    return {
        "venue": venue,
        "dry_run": dry_run,
        "from_date": from_date,
        "to_date": to_date,
        "reviewed": len(drafts),
        "results": rows,
        "received": received,
        "skipped": skipped,
        "summary": {"received": len(received), "skipped": len(skipped)},
    }


def _fail(checks, reasons, key, message):
    checks[key] = "fail"
    reasons.append(message)


def _verdict(stub, reasons):
    return {
        "invoice_id": stub.get("id"),
        "reference_number": stub.get("referenceNumber") or "(no number)",
        "supplier_name": stub.get("supplierName"),
        "po_number": None,
        "total": "$" + str(stub.get("total")),
        "reasons": reasons,
        "checks": {},
    }
