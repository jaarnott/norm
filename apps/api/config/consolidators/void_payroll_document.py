# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for `xero.void_payroll_document` — take one
# push-ledger row's document out of Xero and mark the row voided (plan §10,
# §15 rollback): a wage journal, an intercompany invoice or bill, or an
# entertainer bill. Ask-only: a person approves every void. The xero_common
# helpers are prepended by scripts/sync_xero_connector.py.
#
# The document's status is read FIRST and decides what Xero allows:
#   journal          DRAFT → DELETED, POSTED → VOIDED
#   invoice / bill   DRAFT or SUBMITTED → DELETED, AUTHORISED → VOIDED,
#                    PAID → refused (reverse the payment in Xero first)
#   already VOIDED or DELETED in Xero → nothing to send; the ledger row is
#   marked voided so the week's slot is free again.
# The journal rule is the one to confirm on the Xero Demo Company (Phase 4).
#
# Requires consolidator_config: {"max_api_calls": 8,
#   "allowed_write_actions": ["update_manual_journal", "update_invoice",
#                             "norm_payroll.record_push"]}

_LABELS = {
    "journal": "payroll journal",
    "ico_invoice": "intercompany invoice",
    "ico_bill": "intercompany bill",
    "entertainer_bill": "entertainer bill",
}


def _xero_messages(row):
    msgs = []
    for e in row.get("ValidationErrors") or []:
        if isinstance(e, dict) and e.get("Message"):
            msgs.append(str(e["Message"]))
    return "; ".join(msgs) or "validation error"


def run(params, call_api, log):
    push_id = str(params.get("push_id") or "").strip()
    if not push_id:
        return {"error": "push_id is required — the ledger row of the document to void"}
    data = call_api("norm_payroll", "get_journal_draft", {"push_id": push_id})
    if not isinstance(data, dict) or data.get("error"):
        return {"error": (data or {}).get("error") or "could not read the push"}
    push = data.get("push") or {}
    kind = str(push.get("kind") or "")
    label = _LABELS.get(kind, "document")
    if push.get("voided_at"):
        return {
            "error": "that "
            + label
            + " was already voided on "
            + str(push["voided_at"])[:10]
        }
    xero_id = push.get("xero_id")
    if not xero_id:
        return {"error": "that ledger row has no Xero id to void"}

    vp = {"venue_id": push.get("venue_id")}
    settings, err = xero_settings(call_api, vp)
    if err:
        return {"error": err}
    mode = call_api("norm_payroll", "get_mode", dict(vp))
    if not isinstance(mode, dict) or mode.get("error"):
        return {"error": (mode or {}).get("error") or "could not read the payroll mode"}
    if mode.get("mode") != "live":
        return {
            "error": "Loaded Payroll is not live for this organisation — nothing is changed in Xero in "
            + str(mode.get("mode") or "off")
            + " mode"
        }

    # ── what Xero says the document is now ─────────────────────────────
    is_journal = kind == "journal"
    if is_journal:
        read = call_api(
            "xero", "get_manual_journal", {**vp, "manual_journal_id": xero_id}
        )
        rows, rerr = xero_rows(read, "ManualJournals")
    else:
        read = call_api("xero", "get_invoice", {**vp, "invoice_id": xero_id})
        rows, rerr = xero_rows(read, "Invoices")
    if rerr and "404" in str(rerr):
        rows, rerr, status = [], None, "DELETED"
    elif rerr:
        return {"error": "could not read the " + label + " in Xero: " + str(rerr)}
    else:
        status = str((rows[0].get("Status") if rows else "") or "").upper()
    number = (
        rows[0].get("InvoiceNumber") if rows and not is_journal else None
    ) or push.get("xero_number")

    if status in ("VOIDED", "DELETED"):
        target = None
        what = (
            "already "
            + status.lower()
            + " in Xero — Norm's ledger row is marked voided"
        )
    elif is_journal:
        target = "DELETED" if status in ("DRAFT", "") else "VOIDED"
        what = "set " + target + " in Xero (it is " + (status or "unknown") + ")"
    elif status in ("DRAFT", "SUBMITTED", ""):
        target = "DELETED"
        what = "deleted in Xero (it is " + (status or "unknown") + ")"
    elif status == "AUTHORISED":
        target = "VOIDED"
        what = "voided in Xero (it is authorised)"
    else:
        return {
            "error": "the "
            + label
            + " is "
            + status
            + " in Xero — reverse the payment there first, then void it"
        }

    preview(
        {
            "title": "Void a payroll document in Xero",
            "target": label
            + (" " + str(number) if number else "")
            + " — WE "
            + str(push.get("week_start") or ""),
            "venue": settings.get("organisation"),
            "changes": [
                {"field": "Document", "after": label + " " + str(xero_id)},
                {"field": "Status now", "after": status or "unknown"},
                {"field": "What happens", "after": what},
            ],
            "warnings": []
            if target
            else ["nothing is sent to Xero — only Norm's record changes"],
            "note": "The week's slot is free again afterwards; a new push creates a new document.",
        },
        basis={
            "push_id": push_id,
            "xero_id": xero_id,
            "status_before": status,
            "target": target,
        },
    )

    # ── the write ──────────────────────────────────────────────────────
    after = status
    if target:
        if is_journal:
            out = call_api(
                "xero",
                "update_manual_journal",
                {**vp, "manual_journal_id": xero_id, "journal": {"Status": target}},
            )
            rows, werr = xero_rows(out, "ManualJournals")
        else:
            out = call_api(
                "xero",
                "update_invoice",
                {**vp, "invoice_id": xero_id, "invoice": {"Status": target}},
            )
            rows, werr = xero_rows(out, "Invoices")
        if isinstance(out, dict) and out.get("not_sent"):
            return {"preview_only": True}
        if werr:
            return {"error": "Xero did not void the " + label + ": " + str(werr)}
        if rows and (rows[0].get("HasErrors") or rows[0].get("ValidationErrors")):
            return {
                "error": "Xero refused to void the "
                + label
                + ": "
                + _xero_messages(rows[0])
            }
        after = str((rows[0].get("Status") if rows else target) or target).upper()
        log(label + " " + str(xero_id) + " set " + after)
    rec = call_api(
        "norm_payroll",
        "record_push",
        {**vp, "op": "void", "push_id": push_id, "xero_status": after},
    )
    if isinstance(rec, dict) and rec.get("not_sent"):
        return {"preview_only": True}
    if not isinstance(rec, dict) or rec.get("error"):
        return {
            "error": "the "
            + label
            + " is "
            + after
            + " in Xero but Norm could not record it: "
            + str((rec or {}).get("error"))
        }
    return {
        "voided": True,
        "push_id": push_id,
        "kind": kind,
        "xero_id": xero_id,
        "number": number,
        "xero_status_before": status,
        "xero_status_after": after,
        "sent": bool(target),
    }
