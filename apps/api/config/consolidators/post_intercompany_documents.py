# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for `xero.post_intercompany_documents` — for one
# entity's pay week, one DRAFT invoice (ACCREC, we invoice them) or bill
# (ACCPAY, they bill us) per counterparty whose staff were lent or borrowed
# (plan §10 "Intercompany documents", report 03 §9). The xero_common helpers
# are prepended by scripts/sync_xero_connector.py.
#
# The lines come from Norm's stored ICO drafts (norm_payroll.get_journal_draft
# kind=ico — built from the close snapshot's is_from_other_venue facts and
# the gratuity shares paid elsewhere): one line per person at the
# GST-exclusive labour cost to the venue's intercompany wages account, plus a
# "Leave & Entitlements (12%)" line at the entity's uplift to the leave
# account. The tax types come from THIS tenant's rates (never OUTPUT2/INPUT2
# constants — AU is 10%): the highest active revenue rate for an invoice,
# expense rate for a bill. The leave line follows the entity's rule
# (pay_entities.ico_leave_tax, the accountant's call — get_journal_draft
# passes it as leave_tax):
#   account_default  no tax type when the line has its own leave account, so
#                    Xero applies that account's default rate; the side's
#                    rate when it falls back to the wages account (Orbit's rule)
#   no_gst           the tenant's no-GST type for the side (NONE in NZ)
#   standard_gst     the side's rate, as the wages lines; a leave account
#                    whose class can't take it refuses that document by name
# Each side of a recharge is built from its own entity's rule, so the card
# warns when the counterparty's differs (the invoice and bill won't agree).
#
# A missing counterparty contact refuses THAT group by name; the rest still
# post. A group already pushed is skipped unless replace='true', which voids
# the old document and creates a new one — never an update in place. Keys:
#   norm:ico:<venue>:<week_start>:<invoice|bill>:<counterparty venue id>
# (no organisation id: the venue's uuid already names it, and with it the key
# was 138 characters — Xero refuses an Idempotency-Key over 128 with a 400,
# live Federal Street test, 23 Oct 2026)
#
# Requires consolidator_config: {"max_api_calls": 60,
#   "allowed_write_actions": ["create_invoice", "update_invoice",
#                             "norm_payroll.record_push"]}


def _flag(v):
    return str(v or "").strip().lower() in ("true", "1", "yes", "y")


def _money(cents):
    return "$" + format(abs(int(cents or 0)) / 100, ",.2f")


def _we(week_ending):
    y, m, d = str(week_ending)[:10].split("-")
    return d + "." + m + "." + y


def _due(week_ending):
    y, m, d = (int(x) for x in str(week_ending)[:10].split("-"))
    return (datetime.date(y, m, d) + datetime.timedelta(days=14)).isoformat()


def _best_rate(rates, flag):
    """The highest active rate that can apply to the side — (TaxType, pct)."""
    best = None
    for t in rates:
        if str(t.get("Status") or "ACTIVE") != "ACTIVE" or not t.get(flag):
            continue
        pct = float(t.get("EffectiveRate") or 0)
        if best is None or pct > best[1]:
            best = (t.get("TaxType"), pct)
    return best or (None, 0.0)


def _zero_rate(rates, flag):
    """The tenant's no-GST type for the side: NONE (NZ "No GST") or
    BASEXCLUDED (AU) when active, else any active 0% rate that applies."""
    found = None
    for t in rates:
        if str(t.get("Status") or "ACTIVE") != "ACTIVE" or not t.get(flag):
            continue
        if float(t.get("EffectiveRate") or 0) != 0:
            continue
        if t.get("TaxType") in ("NONE", "BASEXCLUDED"):
            return t.get("TaxType")
        found = found or t.get("TaxType")
    return found


LEAVE_TAX_WORDS = {
    "account_default": "the leave account's own rate",
    "no_gst": "no GST",
    "standard_gst": "GST at the standard rate",
}


def _xero_messages(row):
    msgs = []
    for e in row.get("ValidationErrors") or []:
        if isinstance(e, dict) and e.get("Message"):
            msgs.append(str(e["Message"]))
    for li in row.get("LineItems") or []:
        for e in (li.get("ValidationErrors") or []) if isinstance(li, dict) else []:
            if isinstance(e, dict) and e.get("Message"):
                msgs.append(str(e["Message"]))
    return "; ".join(msgs) or "validation error"


def run(params, call_api, log):
    vp = xero_venue_params(params)
    settings, err = xero_settings(call_api, params)
    if err:
        return {"error": err}
    mode = call_api("norm_payroll", "get_mode", dict(vp))
    if not isinstance(mode, dict) or mode.get("error"):
        return {"error": (mode or {}).get("error") or "could not read the payroll mode"}
    if mode.get("mode") != "live":
        return {
            "error": "Loaded Payroll is not live for this organisation — intercompany "
            "documents are not pushed in " + str(mode.get("mode") or "off") + " mode"
        }
    q = {**vp, "kind": "ico"}
    for k in ("week_ending", "week_start"):
        if params.get(k):
            q[k] = params[k]
    data = call_api("norm_payroll", "get_journal_draft", q)
    if not isinstance(data, dict) or data.get("error"):
        return {
            "error": (data or {}).get("error")
            or "could not read the intercompany drafts"
        }

    docs = data.get("documents") or []
    venue = data.get("venue") or params.get("venue")
    week_ending = str(data.get("week_ending") or "")
    week_start = str(data.get("week_start") or "")
    if not docs:
        return {
            "pushed": [],
            "skipped": [],
            "refused": [],
            "note": "no intercompany lines for "
            + str(venue)
            + " WE "
            + week_ending
            + " — nothing to push",
        }
    wages_account = str(data.get("wages_account") or "")
    if not wages_account:
        return {
            "error": "no intercompany wages account is mapped for "
            + str(venue)
            + " — set it under Payroll settings → Xero mapping"
        }
    replace = _flag(params.get("replace"))
    leave_rule = str(data.get("leave_tax") or "account_default")
    if leave_rule not in LEAVE_TAX_WORDS:
        return {"error": "unknown leave-line tax rule " + leave_rule}

    # ── the tenant's own tax types and chart ───────────────────────────
    tax_payload = call_api("xero", "list_tax_rates", dict(vp))
    tax_rows, terr = xero_rows(tax_payload, "TaxRates")
    if terr:
        return {"error": "Xero tax rates unavailable: " + str(terr)}
    revenue_type, revenue_pct = _best_rate(tax_rows, "CanApplyToRevenue")
    expense_type, expense_pct = _best_rate(tax_rows, "CanApplyToExpenses")
    acc_payload = call_api("xero", "list_accounts", {**vp, "where": 'Status=="ACTIVE"'})
    acc_rows, aerr = xero_rows(acc_payload, "Accounts")
    if aerr:
        return {"error": "Xero accounts unavailable: " + str(aerr)}
    classes = {
        str(a.get("Code")): str(a.get("Class") or "").upper()
        for a in acc_rows
        if a.get("Code")
    }
    names = {
        str(a.get("Code")): str(a.get("Name") or "") for a in acc_rows if a.get("Code")
    }
    # A line sent without a TaxType takes its account's default rate in Xero;
    # the card works the GST out the same way so the total it shows is real.
    account_tax = {
        str(a.get("Code")): a.get("TaxType") for a in acc_rows if a.get("Code")
    }
    rate_of = {
        t.get("TaxType"): float(t.get("EffectiveRate") or 0)
        for t in tax_rows
        if str(t.get("Status") or "ACTIVE") == "ACTIVE"
    }
    if wages_account not in classes:
        return {
            "error": "the intercompany wages account "
            + wages_account
            + " is not an active account in Xero"
        }

    # ── one document per counterparty ──────────────────────────────────
    plan = []
    skipped = []
    refused = []
    for doc in docs:
        cp = str(doc.get("counterparty") or doc.get("counterparty_venue_id"))
        kind = str(doc.get("kind"))
        is_invoice = kind == "ico_invoice"
        contact = doc.get("contact") or {}
        if not contact.get("contact_id"):
            refused.append(
                {
                    "counterparty": cp,
                    "kind": kind,
                    "reason": "no Xero contact mapped for "
                    + cp
                    + " — set it under Payroll settings → Xero mapping",
                }
            )
            continue
        live = doc.get("live_push")
        if live and not replace:
            skipped.append(
                {
                    "counterparty": cp,
                    "kind": kind,
                    "xero_id": live.get("xero_id"),
                    "xero_status": live.get("xero_status"),
                    "reason": "already in Xero — pass replace='true' to void and re-create",
                }
            )
            continue
        tax_type, pct = (
            (revenue_type, revenue_pct) if is_invoice else (expense_type, expense_pct)
        )
        leave_account = str(data.get("leave_account") or wages_account)
        leave_class = classes.get(leave_account, "")
        if leave_rule == "no_gst":
            leave_tax = _zero_rate(
                tax_rows, "CanApplyToRevenue" if is_invoice else "CanApplyToExpenses"
            )
            if not leave_tax and int(doc.get("uplift_cents") or 0):
                refused.append(
                    {
                        "counterparty": cp,
                        "kind": kind,
                        "reason": "this Xero organisation has no no-GST tax rate for "
                        + ("sales" if is_invoice else "purchases")
                        + " — the leave line can't go out with no GST",
                    }
                )
                continue
        elif leave_rule == "standard_gst":
            leave_tax = tax_type
            if int(doc.get("uplift_cents") or 0) and (
                (is_invoice and leave_class == "EXPENSE")
                or (not is_invoice and leave_class == "REVENUE")
            ):
                refused.append(
                    {
                        "counterparty": cp,
                        "kind": kind,
                        "reason": "the leave line's account "
                        + leave_account
                        + " is "
                        + ("an expense" if leave_class == "EXPENSE" else "a revenue")
                        + " account, which can't carry GST on "
                        + ("an invoice" if is_invoice else "a bill")
                        + " — map another leave account or change the leave line's "
                        "tax rule on Payroll settings → Entities",
                    }
                )
                continue
        else:
            # Orbit's rule: its own account takes its own default rate.
            leave_tax = tax_type if leave_account == wages_account else None
        items = []
        wages = 0
        for ln in doc.get("lines") or []:
            if ln.get("key") == "leave_uplift":
                continue
            cents = int(ln.get("amount_cents") or 0)
            if not cents:
                continue
            wages += cents
            items.append(
                {
                    "Description": str(ln.get("staff_name") or "")
                    + " — "
                    + format(float(ln.get("hours") or 0), ".2f")
                    + "h",
                    "Quantity": 1,
                    "UnitAmount": round(cents / 100, 2),
                    "AccountCode": wages_account,
                    "TaxType": tax_type,
                }
            )
        uplift = int(doc.get("uplift_cents") or 0)
        bp = int(doc.get("uplift_bp") or 0)
        if uplift:
            leave_line = {
                "Description": "Leave & Entitlements (" + format(bp / 100, "g") + "%)",
                "Quantity": 1,
                "UnitAmount": round(uplift / 100, 2),
                "AccountCode": leave_account,
            }
            if leave_tax:
                leave_line["TaxType"] = leave_tax
            items.append(leave_line)
        if not items:
            continue
        reference = "ICO Wages WE " + _we(week_ending)
        invoice = {
            "Type": "ACCREC" if is_invoice else "ACCPAY",
            "Contact": {"ContactID": contact["contact_id"]},
            "Date": week_ending,
            "DueDate": _due(week_ending),
            "LineAmountTypes": "Exclusive",
            "Reference": reference if is_invoice else reference + " — " + str(venue),
            "Status": "DRAFT",
            "LineItems": items,
        }
        if not is_invoice:
            # Xero de-duplicates bills on the supplier's number: keep it
            # readable but distinct per counterparty.
            invoice["InvoiceNumber"] = reference + " — " + str(venue)
        gst = 0.0
        for it in items:
            applied = it.get("TaxType") or account_tax.get(str(it["AccountCode"]))
            gst += int(round(it["UnitAmount"] * 100)) * rate_of.get(applied, 0.0) / 100
        gst = int(round(gst))
        subtotal = wages + uplift
        plan.append(
            {
                "doc": doc,
                "counterparty": cp,
                "kind": kind,
                "contact": contact,
                "invoice": invoice,
                "wages_cents": wages,
                "uplift_cents": uplift,
                "gst_cents": gst,
                "total_cents": subtotal + gst,
                "people": len(items) - (1 if uplift else 0),
                "key": "norm:ico:"
                + str(data.get("venue_id"))
                + ":"
                + week_start
                + ":"
                + ("invoice" if is_invoice else "bill")
                + ":"
                + str(doc.get("counterparty_venue_id"))
                # a re-push after a void needs a new key (see post_wage_journal)
                + (
                    ":r" + str(int(doc.get("pushes_before") or 0))
                    if doc.get("pushes_before")
                    else ""
                ),
                "live": live,
                "leave_tax_default": uplift and not leave_tax,
                "rules_differ": uplift
                and str(doc.get("counterparty_leave_tax") or "account_default")
                != leave_rule,
            }
        )

    if not plan:
        why = [r["reason"] for r in refused] + [
            s["counterparty"] + " already pushed" for s in skipped
        ]
        return {
            "error": "nothing to push for WE " + week_ending + ": " + "; ".join(why)
        }

    changes = []
    for p in plan:
        head = ("Invoice to " if p["kind"] == "ico_invoice" else "Bill from ") + p[
            "counterparty"
        ]
        changes.append(
            {
                "field": head,
                "after": str(p["contact"].get("name") or p["contact"]["contact_id"])
                + " · "
                + str(p["people"])
                + (" person" if p["people"] == 1 else " people")
                + " · wages "
                + _money(p["wages_cents"])
                + " · leave "
                + _money(p["uplift_cents"])
                + " · GST "
                + _money(p["gst_cents"])
                + " · total "
                + _money(p["total_cents"]),
            }
        )
        for it in p["invoice"]["LineItems"]:
            changes.append(
                {
                    "field": "↳ " + it["Description"],
                    "after": "$"
                    + format(it["UnitAmount"], ",.2f")
                    + " → "
                    + str(it["AccountCode"])
                    + " "
                    + names.get(str(it["AccountCode"]), "")
                    + " ("
                    + (
                        str(it["TaxType"])
                        if it.get("TaxType")
                        else "account default "
                        + str(account_tax.get(str(it["AccountCode"])) or "none")
                    )
                    + ")",
                }
            )
    warnings = [r["reason"] for r in refused]
    warnings += [
        s["counterparty"] + " (" + s["kind"] + ") " + s["reason"] for s in skipped
    ]
    if replace and any(p["live"] for p in plan):
        warnings.append(
            "replaces documents already in Xero — each is voided or deleted first"
        )
    if any(p["leave_tax_default"] for p in plan):
        warnings.append(
            "the leave & entitlements line carries no tax type, so Xero applies its "
            "account's default rate (the entity's leave-line tax rule)"
        )
    for p in plan:
        if p["rules_differ"]:
            theirs = str(p["doc"].get("counterparty_leave_tax") or "account_default")
            warnings.append(
                p["counterparty"]
                + " builds its side of this recharge with "
                + LEAVE_TAX_WORDS.get(theirs, theirs)
                + " on the leave line, this entity with "
                + LEAVE_TAX_WORDS[leave_rule]
                + " — the invoice and the bill won't agree until both entities "
                "use the same rule (Payroll settings → Entities)"
            )
    preview(
        {
            "title": "Post intercompany documents to Xero",
            "target": str(venue) + " — WE " + _we(week_ending),
            "venue": venue,
            "changes": changes,
            "warnings": warnings,
            "note": str(len(plan))
            + " document"
            + ("s" if len(plan) != 1 else "")
            + ", amounts exclusive of GST, due in 14 days, landing as drafts.",
        },
        basis={
            "documents": [
                {
                    "draft_id": p["doc"].get("draft_id"),
                    "invoice": p["invoice"],
                    "key": p["key"],
                }
                for p in plan
            ],
            "replace": replace,
        },
    )

    # ── the writes ─────────────────────────────────────────────────────
    pushed = []
    voided = []
    errors = []
    records = []
    for p in plan:
        live = p["live"]
        if live and replace:
            status = str(live.get("xero_status") or "").upper()
            target = "DELETED" if status in ("DRAFT", "SUBMITTED", "") else "VOIDED"
            out = call_api(
                "xero",
                "update_invoice",
                {
                    **vp,
                    "invoice_id": live.get("xero_id"),
                    "invoice": {"Status": target},
                },
            )
            if isinstance(out, dict) and out.get("not_sent"):
                return {"preview_only": True}
            vrows, verr = xero_rows(out, "Invoices")
            if verr or (
                vrows
                and (vrows[0].get("HasErrors") or vrows[0].get("ValidationErrors"))
            ):
                errors.append(
                    {
                        "counterparty": p["counterparty"],
                        "kind": p["kind"],
                        "error": "could not void the existing document: "
                        + str(verr or _xero_messages(vrows[0])),
                    }
                )
                continue
            rec = call_api(
                "norm_payroll",
                "record_push",
                {**vp, "op": "void", "push_id": live.get("id"), "xero_status": target},
            )
            if isinstance(rec, dict) and rec.get("error"):
                errors.append(
                    {
                        "counterparty": p["counterparty"],
                        "kind": p["kind"],
                        "error": "voided in Xero but Norm could not record it: "
                        + str(rec["error"]),
                    }
                )
                continue
            voided.append(
                {
                    "push_id": live.get("id"),
                    "xero_id": live.get("xero_id"),
                    "status": target,
                }
            )
        result = call_api(
            "xero",
            "create_invoice",
            {**vp, "invoice": p["invoice"], "idempotency_key": p["key"]},
        )
        if isinstance(result, dict) and result.get("not_sent"):
            return {"preview_only": True}
        rows, ierr = xero_rows(result, "Invoices")
        if ierr or not rows:
            errors.append(
                {
                    "counterparty": p["counterparty"],
                    "kind": p["kind"],
                    "error": "Xero did not create it: " + str(ierr or "empty response"),
                }
            )
            continue
        inv = rows[0]
        if inv.get("HasErrors") or inv.get("ValidationErrors"):
            errors.append(
                {
                    "counterparty": p["counterparty"],
                    "kind": p["kind"],
                    "error": "Xero rejected it: " + _xero_messages(inv),
                }
            )
            continue
        invoice_id = inv.get("InvoiceID")
        log(
            "created Xero "
            + ("invoice" if p["kind"] == "ico_invoice" else "bill")
            + " "
            + str(inv.get("InvoiceNumber"))
            + " ("
            + str(invoice_id)
            + ")"
        )
        records.append(
            {
                "kind": p["kind"],
                "venue_id": data.get("venue_id"),
                "week_start": week_start,
                "draft_id": p["doc"].get("draft_id"),
                "counterparty_venue_id": p["doc"].get("counterparty_venue_id"),
                "xero_id": invoice_id,
                "xero_number": inv.get("InvoiceNumber"),
                "xero_status": inv.get("Status"),
                "idempotency_key": p["key"],
                "request_payload": {"invoice": p["invoice"]},
                "response": {
                    "InvoiceID": invoice_id,
                    "InvoiceNumber": inv.get("InvoiceNumber"),
                    "Status": inv.get("Status"),
                    "Total": inv.get("Total"),
                },
            }
        )
        pushed.append(
            {
                "counterparty": p["counterparty"],
                "kind": p["kind"],
                "xero_id": invoice_id,
                "number": inv.get("InvoiceNumber"),
                "status": inv.get("Status"),
                "subtotal": xero_money(inv.get("SubTotal")),
                "tax": xero_money(inv.get("TotalTax")),
                "total": xero_money(inv.get("Total")),
                "url": xero_invoice_url(settings.get("xero_short_code"), invoice_id)
                if p["kind"] == "ico_invoice"
                else xero_bill_url(settings.get("xero_short_code"), invoice_id),
                "idempotency_key": p["key"],
            }
        )
    ledger_error = None
    if records:
        rec = call_api(
            "norm_payroll", "record_push", {**vp, "op": "record", "documents": records}
        )
        if not isinstance(rec, dict) or rec.get("error"):
            ledger_error = (rec or {}).get("error") or "ledger not written"
            log(
                "WARNING: documents are in Xero but the ledger refused: "
                + str(ledger_error)
            )
        else:
            by_key = {
                r.get("idempotency_key"): r.get("id") for r in rec.get("pushes") or []
            }
            for p in pushed:
                p["push_id"] = by_key.get(p["idempotency_key"])
    return {
        "venue": venue,
        "week_ending": week_ending,
        "pushed": pushed,
        "skipped": skipped,
        "refused": refused,
        "voided": voided,
        "errors": errors,
        "ledger_error": ledger_error,
    }
