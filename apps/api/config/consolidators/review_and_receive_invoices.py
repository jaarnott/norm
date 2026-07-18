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

# Ordered, human-readable labels for every gate — drives the per-invoice
# tick/cross checklist in reports. Keys match the `checks` map; a key absent
# from `checks` means an earlier layer failed first, shown as "—" (not checked).
CHECK_LABELS = [
    ("credit_note", "Not a credit note"),
    ("pdf_present", "Invoice copy attached"),
    ("po_linked", "Linked to a purchase order"),
    ("po_fetch", "Purchase order retrieved"),
    ("po_supplier", "Supplier matches the purchase order"),
    ("items_matched", "Stock items, brands and units all exist in Loaded (no NEW)"),
    ("line_arithmetic", "Line arithmetic (qty × cost = line total)"),
    ("totals", "Invoice totals consistent"),
    ("pdf_readable", "Invoice copy readable"),
    ("pdf_invoice_number", "Invoice number matches the copy"),
    ("pdf_lines", "Lines match the invoice copy"),
    ("pdf_total", "Total matches the invoice copy"),
]

# Conservative unit-name normalisation for the invoice-vs-copy unit check.
# Both sides must be RECOGNISED here (or textually identical) before a
# mismatch counts as a failure — supplier PDFs print units too inconsistently
# ("5.6 KG", "CTN8", …) to fail on strings we can't confidently interpret.
UNIT_ALIASES = {
    "kg": "kg",
    "kgs": "kg",
    "kilo": "kg",
    "kilos": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "g": "g",
    "gm": "g",
    "gram": "g",
    "grams": "g",
    "l": "l",
    "lt": "l",
    "ltr": "l",
    "litre": "l",
    "liter": "l",
    "litres": "l",
    "liters": "l",
    "ml": "ml",
    "mls": "ml",
    "ea": "ea",
    "each": "ea",
    "unit": "ea",
    "un": "ea",
    "doz": "doz",
    "dozen": "doz",
    "dz": "doz",
    "pk": "pk",
    "pack": "pk",
    "pkt": "pk",
    "packet": "pk",
    "bx": "bx",
    "box": "bx",
    "boxes": "bx",
    "ctn": "ctn",
    "carton": "ctn",
    "cartons": "ctn",
    "cs": "cs",
    "case": "cs",
    "cases": "cs",
    "btl": "btl",
    "bottle": "btl",
    "bottles": "btl",
    "can": "can",
    "cans": "can",
    "bag": "bag",
    "bags": "bag",
    "tray": "tray",
    "trays": "tray",
    "punnet": "punnet",
    "punnets": "punnet",
    "roll": "roll",
    "rolls": "roll",
    "bunch": "bunch",
    "bunches": "bunch",
}

# Line-level detail is capped so a 200-line invoice can't blow out the report;
# every line is still CHECKED — only the per-line display rows are capped.
MAX_DETAIL_LINES = 25


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

    def unit_key(text):
        return UNIT_ALIASES.get(norm(text))

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
        po = None
        pdf = None
        po_number_hint = detail.get("purchaseOrderNumber")

        def opt_money(value):
            return money(value) if dec(value) is not None else None

        def opt_num(value):
            return str(value) if value is not None else None

        # Per-line audit records: the invoice's ACTUAL values, filled in with
        # the compared PO / invoice-copy values and ✓/✗ as each layer runs.
        # "—" always means "not checked" (an earlier layer failed first).
        line_records = []
        rec_by_id = {}
        for ln in lines[:MAX_DETAIL_LINES]:
            rec = {
                "line": str(ln.get("description") or ln.get("code") or "?"),
                "code": ln.get("code"),
                "stock_item": "✓"
                if (
                    ln.get("linkedItemId")
                    and ln.get("linkedUnitId")
                    and not (ln.get("brand") and not ln.get("linkedBrandId"))
                )
                else "✗",
                "po_line": "—",
                "on_copy": "—",
                "unit": {"invoice": ln.get("unit"), "copy": None, "result": "—"},
                "quantity": {
                    "ordered": None,
                    "invoice": opt_num(ln.get("quantityReceived")),
                    "copy": None,
                    "result": "—",
                },
                "unit_cost": {
                    "invoice": opt_money(ln.get("unitCost")),
                    "copy": None,
                    "result": "—",
                },
                "line_total": {
                    "invoice": opt_money(ln.get("totalCost")),
                    "copy": None,
                    "result": "—",
                },
                "arithmetic": "—",
            }
            line_records.append(rec)
            rec_by_id[ln.get("id")] = rec
        if len(lines) > MAX_DETAIL_LINES:
            line_records.append(
                {
                    "line": "… "
                    + str(len(lines) - MAX_DETAIL_LINES)
                    + " more lines checked but omitted from this detail view"
                }
            )

        def verdict_now():
            symbol = {"pass": "✓", "fail": "✗"}

            # Compact each line record's nested comparison dicts into the
            # display-ready cell strings the playbook renders (e.g.
            # "ord 5.0 / inv 4.95 / copy 4.95 ✓"). Keeps the LLM payload small
            # enough to survive the tool-result size cap without losing values.
            def cell(pairs, result):
                vals = [lbl + " " + str(v) for lbl, v in pairs if v not in (None, "")]
                sym = result if result in ("✓", "✗") else "—"
                if not vals:
                    return sym
                return " / ".join(vals) + " " + sym

            def compact_line(rec):
                if "stock_item" not in rec:
                    return rec  # the "… N more lines" omission marker
                unit = rec.get("unit") or {}
                qty = rec.get("quantity") or {}
                cost = rec.get("unit_cost") or {}
                tot = rec.get("line_total") or {}
                return {
                    "line": rec.get("line"),
                    "stock_item": rec.get("stock_item", "—"),
                    "po_line": rec.get("po_line", "—"),
                    "on_copy": rec.get("on_copy", "—"),
                    "unit": cell(
                        [("inv", unit.get("invoice")), ("copy", unit.get("copy"))],
                        unit.get("result"),
                    ),
                    "quantity": cell(
                        [
                            ("ord", qty.get("ordered")),
                            ("inv", qty.get("invoice")),
                            ("copy", qty.get("copy")),
                        ],
                        qty.get("result"),
                    ),
                    "unit_cost": cell(
                        [("inv", cost.get("invoice")), ("copy", cost.get("copy"))],
                        cost.get("result"),
                    ),
                    "line_total": cell(
                        [("inv", tot.get("invoice")), ("copy", tot.get("copy"))],
                        tot.get("result"),
                    ),
                    "arithmetic": rec.get("arithmetic", "—"),
                }

            def hdr(field, invoice_val, po_val, copy_val, key):
                return {
                    "field": field,
                    "invoice": invoice_val if invoice_val not in (None, "") else "—",
                    "po": po_val if po_val not in (None, "") else "—",
                    "copy": copy_val if copy_val not in (None, "") else "—",
                    "result": symbol.get(checks.get(key), "—"),
                }

            checklist_rows = [
                {"check": label, "result": symbol.get(checks.get(key), "—")}
                for key, label in CHECK_LABELS
            ]
            details = {
                "header": [
                    hdr(
                        "Invoice number",
                        ref,
                        None,
                        (pdf or {}).get("invoice_number"),
                        "pdf_invoice_number",
                    ),
                    hdr(
                        "Supplier",
                        detail.get("supplierName"),
                        (po or {}).get("supplierName"),
                        (pdf or {}).get("supplier_name"),
                        "po_supplier",
                    ),
                    hdr(
                        "PO number",
                        po_number_hint,
                        (po or {}).get("orderNumber"),
                        (pdf or {}).get("purchase_order_number"),
                        "po_linked",
                    ),
                    hdr(
                        "Subtotal (ex tax)",
                        opt_money(detail.get("subtotal")),
                        None,
                        opt_money((pdf or {}).get("subtotal_ex_tax")),
                        "totals",
                    ),
                    hdr(
                        "Tax",
                        opt_money(detail.get("taxAmount")),
                        None,
                        opt_money((pdf or {}).get("tax_amount")),
                        "totals",
                    ),
                    hdr(
                        "Total incl tax",
                        opt_money(detail.get("total")),
                        None,
                        opt_money((pdf or {}).get("total_incl_tax")),
                        "pdf_total",
                    ),
                ],
            }
            # Line records are only worth reporting once line-level comparison
            # started (the PO was fetched) — before that every cell is "—" and
            # the reasons tell the whole story. Their absence is also the
            # playbook's rendering signal: lines present ⇒ full audit tables,
            # lines absent ⇒ reason bullets only.
            if po is not None:
                details["lines"] = [compact_line(rec) for rec in line_records]
            return {
                "invoice_id": inv_id,
                "reference_number": ref,
                "supplier_name": detail.get("supplierName"),
                "po_number": (po or {}).get("orderNumber") or po_number_hint,
                "total": money(total),
                "reasons": reasons,
                "checklist": (
                    "All " + str(len(checklist_rows)) + " checks passed ✓"
                    if all(r["result"] == "✓" for r in checklist_rows)
                    else checklist_rows
                ),
                "details": details,
            }

        # Gates are evaluated in LAYERS that short-circuit: once a layer fails,
        # later layers are neither evaluated nor reported — "not linked to a
        # PO" is the whole story, not a prelude to line-level noise. This also
        # means the (expensive) PDF extraction only runs for invoices that
        # pass every cheaper gate.

        # Layer 1: credit notes are out of scope
        if total is not None and total < 0:
            _fail(
                checks,
                reasons,
                "credit_note",
                "Credit note (total "
                + money(total)
                + ") — out of scope for auto-receiving",
            )
            skipped.append(verdict_now())
            continue
        checks["credit_note"] = "pass"

        # Layer 2: an invoice copy must be attached. Without the source
        # document nothing can be verified, so stop reviewing immediately.
        if not detail.get("fileId"):
            _fail(
                checks,
                reasons,
                "pdf_present",
                "No invoice copy attached — cannot verify; attach the supplier's invoice in Loaded",
            )
            skipped.append(verdict_now())
            continue
        checks["pdf_present"] = "pass"

        # Layer 3: must be automatched to a purchase order
        po_id = detail.get("linkedPurchaseOrderId")
        if not po_id:
            msg = "Not linked to a purchase order"
            if po_number_hint:
                msg += (
                    " (invoice references "
                    + str(po_number_hint)
                    + " — needs matching in Loaded)"
                )
            _fail(checks, reasons, "po_linked", msg)
            skipped.append(verdict_now())
            continue
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
            skipped.append(verdict_now())
            continue
        checks["po_fetch"] = "pass"

        # Layer 4: the linked PO must belong to the same supplier
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

        # Layer 4 (cont.): every stock item, brand and unit must already exist
        # in Loaded — anything Loaded would show with a NEW tag on the receive
        # screen (a value with no linked id) blocks auto-receiving.
        new_values = []
        for ln in lines:
            name = str(ln.get("description") or ln.get("code") or "?")
            if not ln.get("linkedItemId"):
                new_values.append("stock item on line '" + name + "'")
            if not ln.get("linkedUnitId"):
                new_values.append(
                    "unit '" + str(ln.get("unit")) + "' on line '" + name + "'"
                )
            if ln.get("brand") and not ln.get("linkedBrandId"):
                new_values.append(
                    "brand '" + str(ln.get("brand")) + "' on line '" + name + "'"
                )
        if new_values:
            shown = "; ".join(new_values[:5])
            if len(new_values) > 5:
                shown += "; … " + str(len(new_values) - 5) + " more"
            _fail(
                checks,
                reasons,
                "items_matched",
                str(len(new_values))
                + " value(s) are not in the Loaded database (would be created as NEW): "
                + shown,
            )
        else:
            checks["items_matched"] = "pass"

        # PO lines are INFORMATIONAL only — invoices legitimately differ from
        # their purchase order (substitutions, catch weight, extra items). The
        # attached invoice copy (layer 6) is the source of truth for what was
        # billed; the PO contributes the ordered quantity for the audit view.
        if po:
            po_lines = {ln.get("itemId"): ln for ln in po.get("lines") or []}
            for ln in lines:
                rec = rec_by_id.get(ln.get("id"))
                pol = po_lines.get(ln.get("linkedItemId"))
                if rec and pol:
                    rec["po_line"] = "✓"
                    rec["quantity"]["ordered"] = opt_num(pol.get("quantityOrdered"))

        if reasons:
            skipped.append(verdict_now())
            continue

        # Layer 5: internal consistency — per-line arithmetic
        arith_ok = True
        for ln in lines:
            rec = rec_by_id.get(ln.get("id"))
            q, uc, tc = (
                dec(ln.get("quantityReceived")),
                dec(ln.get("unitCost")),
                dec(ln.get("totalCost")),
            )
            line_ok = not (
                q is None or uc is None or tc is None or not close(q * uc, tc, line_tol)
            )
            if rec:
                rec["arithmetic"] = "✓" if line_ok else "✗"
            if not line_ok:
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

        # Layer 5 (cont.): internal totals
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

        if reasons:
            skipped.append(verdict_now())
            continue

        # Layer 6: verify against the supplier's attached invoice copy
        # (only reached when every cheaper gate passed — this is the one
        # LLM-extraction call per invoice; the copy's presence was checked
        # up front in layer 2)
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
        else:
            checks["pdf_readable"] = "pass"

        if pdf:
            # The copy must be for THIS invoice (only fails on a live conflict —
            # a copy with no printed number is caught by the line-level checks)
            if (
                norm(pdf.get("invoice_number"))
                and norm(ref)
                and norm(pdf.get("invoice_number")) != norm(ref)
            ):
                _fail(
                    checks,
                    reasons,
                    "pdf_invoice_number",
                    "Attached copy is for invoice '"
                    + str(pdf.get("invoice_number"))
                    + "' but this invoice is '"
                    + ref
                    + "'",
                )
            else:
                checks["pdf_invoice_number"] = "pass"

            pdf_ok = True
            pdf_lines = list(pdf.get("lines") or [])
            unclaimed = list(pdf_lines)
            for ln in lines:
                rec = rec_by_id.get(ln.get("id"))
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
                    if rec:
                        rec["on_copy"] = "✗"
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "' not found on the attached invoice document"
                    )
                    continue
                unclaimed.remove(match)
                if rec:
                    rec["on_copy"] = "✓"
                    rec["unit"]["copy"] = match.get("unit")
                    rec["quantity"]["copy"] = opt_num(match.get("quantity"))
                    rec["unit_cost"]["copy"] = opt_money(match.get("unit_price_ex_tax"))
                    rec["line_total"]["copy"] = opt_money(
                        match.get("line_total_ex_tax")
                    )
                # Unit: invoice vs copy. Only a confident mismatch fails —
                # both sides must be recognised units (or textually equal);
                # unrecognised strings stay "not checked".
                inv_unit, copy_unit = ln.get("unit"), match.get("unit")
                if inv_unit and copy_unit:
                    ik, ck = unit_key(inv_unit), unit_key(copy_unit)
                    if norm(inv_unit) == norm(copy_unit) or (ik and ck and ik == ck):
                        if rec:
                            rec["unit"]["result"] = "✓"
                    elif ik and ck:
                        pdf_ok = False
                        if rec:
                            rec["unit"]["result"] = "✗"
                        reasons.append(
                            "Line '"
                            + str(ln.get("description"))
                            + "': unit "
                            + str(inv_unit)
                            + " does not match the document's unit "
                            + str(copy_unit)
                        )
                if dec(match.get("quantity")) != dec(ln.get("quantityReceived")):
                    pdf_ok = False
                    if rec:
                        rec["quantity"]["result"] = "✗"
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "': quantity "
                        + str(ln.get("quantityReceived"))
                        + " does not equal the document's quantity "
                        + str(match.get("quantity"))
                    )
                elif rec:
                    rec["quantity"]["result"] = "✓"
                if dec(match.get("unit_price_ex_tax")) != dec(ln.get("unitCost")):
                    pdf_ok = False
                    if rec:
                        rec["unit_cost"]["result"] = "✗"
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "': unit cost "
                        + money(ln.get("unitCost"))
                        + " does not equal the document's unit price "
                        + money(match.get("unit_price_ex_tax"))
                    )
                elif rec:
                    rec["unit_cost"]["result"] = "✓"
                if not close(
                    dec(match.get("line_total_ex_tax")),
                    dec(ln.get("totalCost")),
                    line_tol,
                ):
                    pdf_ok = False
                    if rec:
                        rec["line_total"]["result"] = "✗"
                    reasons.append(
                        "Line '"
                        + str(ln.get("description"))
                        + "': line total "
                        + money(ln.get("totalCost"))
                        + " does not equal the document's line total "
                        + money(match.get("line_total_ex_tax"))
                    )
                elif rec:
                    rec["line_total"]["result"] = "✓"
            for cand in unclaimed:
                pdf_ok = False
                line_records.append(
                    {
                        "line": str(cand.get("description")) + " — on copy only",
                        "stock_item": "—",
                        "po_line": "—",
                        "on_copy": "✗",
                        "quantity": {
                            "ordered": None,
                            "invoice": None,
                            "copy": opt_num(cand.get("quantity")),
                            "result": "✗",
                        },
                        "unit_cost": {
                            "invoice": None,
                            "copy": opt_money(cand.get("unit_price_ex_tax")),
                            "result": "✗",
                        },
                        "line_total": {
                            "invoice": None,
                            "copy": opt_money(cand.get("line_total_ex_tax")),
                            "result": "✗",
                        },
                        "arithmetic": "—",
                    }
                )
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
                    line_records.append(
                        {
                            "line": str(charge.get("description"))
                            + " — charge on copy only",
                            "stock_item": "—",
                            "po_line": "—",
                            "on_copy": "✗",
                            "line_total": {
                                "invoice": None,
                                "copy": money(amt),
                                "result": "✗",
                            },
                            "arithmetic": "—",
                        }
                    )
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

        verdict = verdict_now()

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

    def checks_summary(v):
        checklist = v.get("checklist")
        if isinstance(checklist, str):  # "All N checks passed ✓"
            return str(len(CHECK_LABELS)) + "✓"
        results = [c["result"] for c in checklist or []]
        if not results:
            return "—"
        parts = [str(results.count("✓")) + "✓"]
        if "✗" in results:
            parts.append(str(results.count("✗")) + "✗")
        if "—" in results:
            parts.append(str(results.count("—")) + " not checked")
        return " ".join(parts)

    rows = [
        {
            "reference": v["reference_number"],
            "supplier": v.get("supplier_name"),
            "po": v.get("po_number") or "—",
            "total": v["total"],
            "checks": checks_summary(v),
            "outcome": v.get("outcome", "skipped"),
            "reasons": " • ".join(v["reasons"]) if v.get("reasons") else "—",
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
    }
