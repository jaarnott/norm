# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for the `xero.get_bills` consolidator — supplier
# bills (ACCPAY invoices) in the venue's Xero organisation, with the totals the
# bills view tiles show. The xero_common helpers are prepended by
# scripts/sync_xero_connector.py. The tool row carries
# display_component "xero_bills": the component renders THIS document.
#
# Xero facts (verified live 17 Oct 2026): Invoices page 100 at a time;
# Statuses= filters server-side; summaryOnly=true drops line items; amounts are
# SubTotal / TotalTax / Total / AmountDue / AmountPaid in CurrencyCode.
#
# Requires consolidator_config: {"max_api_calls": 8,
#   "allowed_write_actions": ["resolve_dates"]}  (resolve_dates is a norm read)

STATUS_SETS = {
    "unpaid": "AUTHORISED,SUBMITTED",
    "awaiting_approval": "SUBMITTED",
    "draft": "DRAFT",
    "paid": "PAID",
    "all": "DRAFT,SUBMITTED,AUTHORISED,PAID",
}


def run(params, call_api, log):
    status = str(params.get("status") or "unpaid").strip().lower().replace(" ", "_")
    if status not in STATUS_SETS:
        status = "unpaid"
    supplier = str(params.get("supplier") or "").strip().lower()
    limit = int(params.get("limit") or 200)
    vp = xero_venue_params(params)
    settings, err = xero_settings(call_api, params)
    if err:
        return {"error": err}

    from_date, to_date, window_desc, win_err = xero_window(call_api, params, vp)
    if win_err:
        return {"error": win_err}

    where = ['Type=="ACCPAY"']
    if from_date:
        y, m, d = from_date.split("-")
        where.append(f"Date>=DateTime({int(y)},{int(m)},{int(d)})")
    if to_date:
        y, m, d = to_date.split("-")
        where.append(f"Date<=DateTime({int(y)},{int(m)},{int(d)})")

    short_code = settings.get("xero_short_code")
    org = {
        "name": settings.get("organisation"),
        "currency": settings.get("xero_currency"),
    }

    bills = []
    page = 1
    while page <= 6:
        payload = call_api(
            "xero",
            "list_invoices",
            {
                **vp,
                "page": str(page),
                "where": "&&".join(where),
                "statuses": STATUS_SETS[status],
                "order": "DueDate",
                "summary_only": "true",
            },
        )
        rows, err = xero_rows(payload, "Invoices")
        if err:
            return {"error": f"Xero bills unavailable: {err}"}
        bills.extend(rows)
        if len(rows) < 100:
            break
        page += 1

    # Not date.today(): in the sandbox it dies on a lazy `import time`
    # (the same trap as strftime/strptime).
    today = datetime.datetime.now().date()

    def days_between(due):
        try:
            y, m, d = (int(x) for x in due.split("-"))
            return (today - datetime.date(y, m, d)).days
        except (TypeError, ValueError, AttributeError):
            return None

    out = []
    for inv in bills:
        contact = inv.get("Contact") or {}
        name = str(contact.get("Name") or "")
        if supplier and supplier not in name.lower():
            continue
        due = xero_date(inv.get("DueDateString"))
        amount_due = xero_money(inv.get("AmountDue")) or 0.0
        overdue_days = days_between(due) if due else None
        is_overdue = bool(
            amount_due > 0
            and overdue_days is not None
            and overdue_days > 0
            and str(inv.get("Status")) in ("AUTHORISED", "SUBMITTED")
        )
        out.append(
            {
                "invoice_id": inv.get("InvoiceID"),
                "number": inv.get("InvoiceNumber"),
                "reference": inv.get("Reference") or None,
                "supplier": name or None,
                "contact_id": contact.get("ContactID"),
                "date": xero_date(inv.get("DateString")),
                "due_date": due,
                "status": inv.get("Status"),
                "subtotal": xero_money(inv.get("SubTotal")),
                "tax": xero_money(inv.get("TotalTax")),
                "total": xero_money(inv.get("Total")),
                "amount_due": amount_due,
                "amount_paid": xero_money(inv.get("AmountPaid")),
                "currency": inv.get("CurrencyCode") or org.get("currency"),
                "overdue": is_overdue,
                "days_overdue": overdue_days if is_overdue else 0,
                "has_attachment": bool(inv.get("HasAttachments")),
                "url": xero_bill_url(short_code, inv.get("InvoiceID")),
            }
        )

    out.sort(key=lambda b: (b.get("due_date") or "9999", b.get("supplier") or ""))

    summary = {
        "count": len(out),
        "total": round(sum(b["total"] or 0 for b in out), 2),
        "amount_due": round(sum(b["amount_due"] for b in out), 2),
        "overdue": round(sum(b["amount_due"] for b in out if b["overdue"]), 2),
        "overdue_count": sum(1 for b in out if b["overdue"]),
        "due_7_days": round(
            sum(
                b["amount_due"]
                for b in out
                if b["due_date"]
                and not b["overdue"]
                and b["amount_due"] > 0
                and days_between(b["due_date"]) is not None
                and -7 <= days_between(b["due_date"]) <= 0
            ),
            2,
        ),
    }

    by_supplier = {}
    for b in out:
        key = b.get("supplier") or "(no supplier)"
        row = by_supplier.setdefault(
            key,
            {
                "supplier": key,
                "count": 0,
                "total": 0.0,
                "amount_due": 0.0,
                "overdue": 0.0,
            },
        )
        row["count"] += 1
        row["total"] = round(row["total"] + (b["total"] or 0), 2)
        row["amount_due"] = round(row["amount_due"] + b["amount_due"], 2)
        if b["overdue"]:
            row["overdue"] = round(row["overdue"] + b["amount_due"], 2)
    suppliers = sorted(by_supplier.values(), key=lambda r: -r["amount_due"])

    return {
        "venue": params.get("venue"),
        "organisation": org.get("name"),
        "currency": org.get("currency"),
        "as_of": today.isoformat(),
        "filters": {
            "status": status,
            "from": from_date or None,
            "to": to_date or None,
            "window": window_desc,
            "supplier": supplier or None,
        },
        "summary": summary,
        "by_supplier": suppliers,
        "bills": out[:limit],
        "shown": min(len(out), limit),
    }
