# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for the `xero.create_bill` consolidator — create a
# supplier bill (an ACCPAY invoice) in the venue's Xero organisation. The
# xero_common helpers are prepended by scripts/sync_xero_connector.py.
#
# A WRITE that asks: the tool loop runs it in preview mode first, so the
# preview(card) below is what the person approves — supplier (new contact or
# not), dates, every line with its account and tax type, the totals, the
# status it lands in. The approved run re-checks the card's basis and writes.
#
# What it resolves, in order:
#   1. the venue's Xero settings (guard + account mapping + tracking),
#   2. the supplier -> a Xero contact (exact name, else Xero's search, else a
#      new supplier contact),
#   3. the organisation's tax rates -> the GST and no-GST expense tax types
#      (NZ INPUT2/NONE, AU INPUT/EXEMPTEXPENSES — never hardcoded),
#   4. each line's account code: the line's own, else the mapping by category
#      (food / beverage / other), else the mapping's default.
#
# Xero facts (verified live 17 Oct 2026): PUT /Invoices strictly creates;
# summarizeErrors=false returns 200 with Invoices[0].HasErrors +
# ValidationErrors on a bad payload; InvoiceNumber on an ACCPAY bill is the
# supplier's number; the Idempotency-Key header makes a retry return the
# same bill instead of a second one.
#
# Requires consolidator_config: {"max_api_calls": 8,
#   "allowed_write_actions": ["create_contact", "create_invoice"]}

CATEGORY_KEYS = {
    "food": "food",
    "kitchen": "food",
    "beverage": "beverage",
    "beverages": "beverage",
    "bar": "beverage",
    "drinks": "beverage",
    "other": "other",
    "other stock": "other",
}


def _lines_param(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return [ln for ln in (raw or []) if isinstance(ln, dict)]


def _flag(v, default=False):
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("true", "1", "yes", "y")


def run(params, call_api, log):
    vp = xero_venue_params(params)
    settings, err = xero_settings(call_api, params)
    if err:
        return {"error": err}
    mapping = settings.get("xero_mapping") or {}
    accounts_map = {
        str(k).lower(): str(v).strip()
        for k, v in (mapping.get("accounts") or {}).items()
        if v
    }
    tracking = mapping.get("tracking") or {}
    short_code = settings.get("xero_short_code")
    currency = settings.get("xero_currency")

    supplier = str(params.get("supplier") or "").strip()
    contact_id = str(params.get("supplier_contact_id") or "").strip()
    invoice_number = str(params.get("invoice_number") or "").strip()
    reference = str(params.get("reference") or "").strip()
    date = str(params.get("date") or "")[:10]
    due_date = str(params.get("due_date") or "")[:10]
    status = str(params.get("status") or mapping.get("bill_status") or "DRAFT").upper()
    if status not in ("DRAFT", "SUBMITTED", "AUTHORISED"):
        status = "DRAFT"
    amount_types = str(
        params.get("line_amount_types")
        or mapping.get("line_amount_types")
        or "Exclusive"
    ).capitalize()
    if amount_types not in ("Exclusive", "Inclusive", "NoTax"):
        amount_types = "Exclusive"
    lines = _lines_param(params.get("lines"))
    create_contact = _flag(params.get("create_contact"), True)

    if not supplier and not contact_id:
        return {"error": "supplier (name) or supplier_contact_id is required"}
    if not date:
        return {"error": "date (YYYY-MM-DD) is required"}
    if not lines:
        return {"error": "lines is required: [{description, quantity, unit_amount, …}]"}
    if not due_date:
        try:
            y, m, d = (int(x) for x in date.split("-"))
            due = datetime.date(y, m, d) + datetime.timedelta(
                days=int(mapping.get("due_days") or 20)
            )
            due_date = due.isoformat()
        except (TypeError, ValueError):
            return {"error": f"date '{date}' is not YYYY-MM-DD"}

    # ── 2. the contact ─────────────────────────────────────────────────
    contact_name = supplier
    contact_is_new = False
    contact_note = None
    if not contact_id:
        payload = call_api(
            "xero", "list_contacts", {**vp, "page": "1", "search_term": supplier}
        )
        rows, cerr = xero_rows(payload, "Contacts")
        if cerr:
            return {"error": f"Xero contacts unavailable: {cerr}"}
        exact = [
            c
            for c in rows
            if str(c.get("Name") or "").strip().lower() == supplier.lower()
        ]
        active = [
            c for c in rows if str(c.get("ContactStatus") or "ACTIVE") == "ACTIVE"
        ]
        if exact:
            contact_id = str(exact[0].get("ContactID") or "")
            contact_name = str(exact[0].get("Name") or supplier)
        elif len(active) == 1:
            contact_id = str(active[0].get("ContactID") or "")
            contact_name = str(active[0].get("Name") or supplier)
            contact_note = (
                f"matched Xero contact '{contact_name}' (searched '{supplier}')"
            )
        elif len(active) > 1:
            names = ", ".join(str(c.get("Name")) for c in active[:6])
            return {
                "error": f"'{supplier}' matches {len(active)} Xero contacts ({names}) — "
                "pass supplier_contact_id, or the exact name"
            }
        elif not create_contact:
            return {
                "error": f"no Xero contact named '{supplier}' (create_contact is off)"
            }
        else:
            contact_is_new = True

    # ── 3. tax types from the organisation's rates ─────────────────────
    tax_payload = call_api("xero", "list_tax_rates", dict(vp))
    tax_rows, terr = xero_rows(tax_payload, "TaxRates")
    if terr:
        return {"error": f"Xero tax rates unavailable: {terr}"}
    expense_rates = [
        t
        for t in tax_rows
        if str(t.get("Status") or "ACTIVE") == "ACTIVE" and t.get("CanApplyToExpenses")
    ]
    # No next()/max(default=) here: the sandbox's builtins are a short list.
    gst_rate = None
    for t in expense_rates:
        rate = float(t.get("EffectiveRate") or 0)
        if rate > 0 and (
            gst_rate is None or rate > float(gst_rate.get("EffectiveRate") or 0)
        ):
            gst_rate = t
    zero_rates = [t for t in expense_rates if not float(t.get("EffectiveRate") or 0)]
    no_gst_rate = zero_rates[0] if zero_rates else None
    for key in ("NONE", "EXEMPTEXPENSES", "BASEXCLUDED"):
        hit = [t for t in zero_rates if t.get("TaxType") == key]
        if hit:
            no_gst_rate = hit[0]
            break
    gst_type = (gst_rate or {}).get("TaxType")
    gst_pct = float((gst_rate or {}).get("EffectiveRate") or 0)
    no_gst_type = (no_gst_rate or {}).get("TaxType") or "NONE"
    if amount_types == "NoTax":
        gst_type = no_gst_type

    # ── 4. lines: account codes + tax ──────────────────────────────────
    acc_payload = call_api("xero", "list_accounts", {**vp, "where": 'Status=="ACTIVE"'})
    acc_rows, aerr = xero_rows(acc_payload, "Accounts")
    if aerr:
        return {"error": f"Xero accounts unavailable: {aerr}"}
    account_names = {
        str(a.get("Code")): str(a.get("Name") or "") for a in acc_rows if a.get("Code")
    }

    items = []
    warnings = []
    missing = []
    for i, ln in enumerate(lines, 1):
        desc = str(ln.get("description") or "").strip() or f"Line {i}"
        qty = xero_money(ln.get("quantity")) or 1.0
        unit = xero_money(ln.get("unit_amount"))
        if unit is None:
            total = xero_money(ln.get("total"))
            unit = round(total / qty, 4) if total is not None and qty else None
        if unit is None:
            return {"error": f"line {i} ('{desc}') has no unit_amount or total"}
        code = str(ln.get("account_code") or "").strip()
        if not code:
            cat = CATEGORY_KEYS.get(str(ln.get("category") or "").strip().lower())
            code = (cat and accounts_map.get(cat)) or accounts_map.get("default") or ""
        if not code:
            missing.append(desc)
            continue
        if code not in account_names:
            warnings.append(f"account code {code} is not an active account in Xero")
        tax_type = str(ln.get("tax_type") or "").strip()
        if not tax_type:
            gst_flag = ln.get("gst")
            if gst_flag is None or gst_flag == "":
                has_gst = True
            elif isinstance(gst_flag, (int, float)):
                has_gst = float(gst_flag) > 0
            else:
                has_gst = str(gst_flag).strip().lower() not in (
                    "no",
                    "none",
                    "false",
                    "0",
                    "0.0",
                    "exempt",
                )
            tax_type = gst_type if has_gst else no_gst_type
        item = {
            "Description": desc,
            "Quantity": qty,
            "UnitAmount": unit,
            "AccountCode": code,
            "TaxType": tax_type,
        }
        if tracking.get("category_id") and tracking.get("option_id"):
            item["Tracking"] = [
                {
                    "TrackingCategoryID": tracking["category_id"],
                    "TrackingOptionID": tracking["option_id"],
                }
            ]
        items.append(item)
    if missing:
        return {
            "error": "no account code for: "
            + "; ".join(missing)
            + " — give each line an account_code (see get_accounts) or set the "
            "venue's Xero mapping on the Bills page"
        }

    ex_gst = round(sum(it["Quantity"] * it["UnitAmount"] for it in items), 2)
    gst = 0.0
    if amount_types == "Exclusive":
        gst = round(
            sum(
                it["Quantity"] * it["UnitAmount"] * gst_pct / 100
                for it in items
                if it["TaxType"] == gst_type
            ),
            2,
        )
        total = round(ex_gst + gst, 2)
    elif amount_types == "Inclusive":
        total = ex_gst
        incl_gst = sum(
            it["Quantity"] * it["UnitAmount"]
            for it in items
            if it["TaxType"] == gst_type
        )
        gst = round(incl_gst - incl_gst / (1 + gst_pct / 100), 2) if gst_pct else 0.0
        ex_gst = round(total - gst, 2)
    else:
        total = ex_gst

    bill = {
        "Type": "ACCPAY",
        "Contact": {"ContactID": contact_id} if contact_id else {"Name": supplier},
        "Date": date,
        "DueDate": due_date,
        "LineAmountTypes": amount_types,
        "Status": status,
        "LineItems": items,
    }
    if invoice_number:
        bill["InvoiceNumber"] = invoice_number
    if reference:
        bill["Reference"] = reference

    key_source = str(params.get("idempotency_key") or "").strip() or (
        "norm:"
        + ":".join(
            str(x).strip().lower()
            for x in (
                vp.get("venue_id") or vp.get("venue"),
                supplier,
                invoice_number or date,
                ex_gst,
            )
        )
    )
    idempotency_key = key_source[:128]

    money = lambda v: f"{currency + ' ' if currency else '$'}{v:,.2f}"  # noqa: E731
    changes = [
        {
            "field": "Supplier",
            "after": contact_name
            + (" (new contact in Xero)" if contact_is_new else ""),
        },
        {"field": "Invoice number", "after": invoice_number or "—"},
        {"field": "Date", "after": date},
        {"field": "Due", "after": due_date},
    ]
    for it in items:
        acct = f"{it['AccountCode']} {account_names.get(it['AccountCode'], '')}".strip()
        changes.append(
            {
                "field": it["Description"][:60],
                "after": f"{it['Quantity']:g} × {money(it['UnitAmount'])} → {acct} ({it['TaxType']})",
            }
        )
    changes += [
        {"field": "Total ex GST", "after": money(ex_gst)},
        {"field": "GST", "after": money(gst)},
        {"field": "Total", "after": money(total)},
        {"field": "Lands as", "after": status.title()},
    ]
    if contact_note:
        warnings.append(contact_note)
    preview(
        {
            "title": "Create a bill in Xero",
            "target": f"{contact_name} — {invoice_number or date}",
            "venue": params.get("venue"),
            "changes": changes,
            "warnings": warnings,
            "note": (
                f"{len(items)} line{'s' if len(items) != 1 else ''}; amounts are "
                f"{amount_types.lower()} of GST. Nothing is paid — this records the bill."
            ),
        },
        basis={"contact_id": contact_id, "bill": bill},
    )

    # ── the writes ─────────────────────────────────────────────────────
    if contact_is_new:
        created = call_api(
            "xero",
            "create_contact",
            {**vp, "contact": {"Name": supplier, "IsSupplier": True}},
        )
        if isinstance(created, dict) and created.get("not_sent"):
            return {"preview_only": True}
        rows, cerr = xero_rows(created, "Contacts")
        if cerr or not rows or not rows[0].get("ContactID"):
            return {
                "error": f"could not create the Xero contact '{supplier}': {cerr or 'no id returned'}"
            }
        contact_id = str(rows[0]["ContactID"])
        bill["Contact"] = {"ContactID": contact_id}

    result = call_api(
        "xero",
        "create_invoice",
        {**vp, "invoice": bill, "idempotency_key": idempotency_key},
    )
    if isinstance(result, dict) and result.get("not_sent"):
        return {"preview_only": True}
    rows, ierr = xero_rows(result, "Invoices")
    if ierr or not rows:
        return {"error": f"Xero did not create the bill: {ierr or 'empty response'}"}
    inv = rows[0]
    if inv.get("HasErrors") or inv.get("ValidationErrors"):
        msgs = "; ".join(
            str(e.get("Message"))
            for e in (inv.get("ValidationErrors") or [])
            if isinstance(e, dict)
        )
        return {"error": f"Xero rejected the bill: {msgs or 'validation error'}"}
    invoice_id = inv.get("InvoiceID")
    log(
        f"created Xero bill {inv.get('InvoiceNumber')} ({invoice_id}) as {inv.get('Status')}"
    )
    return {
        "created": True,
        "invoice_id": invoice_id,
        "number": inv.get("InvoiceNumber"),
        "status": inv.get("Status"),
        "supplier": contact_name,
        "contact_id": contact_id,
        "contact_created": contact_is_new,
        "date": date,
        "due_date": due_date,
        "subtotal": xero_money(inv.get("SubTotal")),
        "tax": xero_money(inv.get("TotalTax")),
        "total": xero_money(inv.get("Total")),
        "currency": inv.get("CurrencyCode") or currency,
        "url": xero_bill_url(short_code, invoice_id),
        "idempotency_key": idempotency_key,
    }
