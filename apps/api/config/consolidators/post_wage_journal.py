# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for `xero.post_wage_journal` — push one entity's
# payroll journal for a pay week to Xero as a DRAFT manual journal (plan §10
# "Push"). The xero_common helpers are prepended by
# scripts/sync_xero_connector.py.
#
# A WRITE that asks: the preview(card) below lists every line, the totals,
# the residual and the narration — what the person approves in chat, or sees
# on the Reconciliation page before pressing Push. The approved run checks
# the card's basis (the exact Xero payload) and writes.
#
# Where the figures come from: NOT from this tool. The journal is built and
# stored by Norm (services/payroll/journal.py → pay_journal_drafts) from the
# week's close snapshot and the inputs people keyed; this tool reads it
# through the engine-only built-in norm_payroll.get_journal_draft, which
# refuses unless the caller holds payroll:write, payroll is live, the run is
# attended, and the draft's inputs have not moved since it was built. So the
# card shows numbers Norm derived, never numbers a browser or a model sent.
#
# Idempotent: Xero gets Idempotency-Key
#   norm:payroll-journal:<venue>:<week_start>:<basis[:16]>
# (no organisation id — the venue's uuid names it; Xero refuses a key over
# 128 characters, and with the org a re-push key ran to 126+)
# (Orbit sent none and could double-post on a timeout), and the ledger
# (norm_payroll.record_push) is idempotent on the same key. A live push for
# the week refuses a second one unless replace='true', which voids the
# existing journal first — one live document per week, by the ledger's
# partial unique index.
#
# Requires consolidator_config: {"max_api_calls": 10,
#   "allowed_write_actions": ["create_manual_journal", "update_manual_journal",
#                             "norm_payroll.record_push"]}


def _flag(v):
    return str(v or "").strip().lower() in ("true", "1", "yes", "y")


def _money(cents):
    return "$" + format(abs(int(cents or 0)) / 100, ",.2f")


def _we(week_ending):
    y, m, d = str(week_ending)[:10].split("-")
    return d + "." + m + "." + y


def _xero_messages(row):
    msgs = []
    for e in row.get("ValidationErrors") or []:
        if isinstance(e, dict) and e.get("Message"):
            msgs.append(str(e["Message"]))
    for li in row.get("JournalLines") or []:
        for e in (li.get("ValidationErrors") or []) if isinstance(li, dict) else []:
            if isinstance(e, dict) and e.get("Message"):
                msgs.append(str(e["Message"]))
    return "; ".join(msgs) or "validation error"


def _journal_url(short_code, journal_id):
    if not short_code or not journal_id:
        return None
    return (
        "https://go.xero.com/organisationlogin/default.aspx?shortcode="
        + str(short_code)
        + "&redirecturl=/Journal/View.aspx?invoiceID="
        + str(journal_id)
    )


def run(params, call_api, log):
    vp = xero_venue_params(params)
    settings, err = xero_settings(call_api, params)
    if err:
        return {"error": err}

    # ── 1. live, attended, a person with payroll:write ─────────────────
    mode = call_api("norm_payroll", "get_mode", dict(vp))
    if not isinstance(mode, dict) or mode.get("error"):
        return {"error": (mode or {}).get("error") or "could not read the payroll mode"}
    if mode.get("mode") != "live":
        return {
            "error": "Loaded Payroll is not live for this organisation — journals are "
            "not pushed in " + str(mode.get("mode") or "off") + " mode"
        }
    q = {**vp, "kind": "journal"}
    for k in ("week_ending", "week_start", "draft_id", "basis_sha256"):
        if params.get(k):
            q[k] = params[k]
    draft = call_api("norm_payroll", "get_journal_draft", q)
    if not isinstance(draft, dict) or draft.get("error"):
        return {
            "error": (draft or {}).get("error") or "could not read the journal draft"
        }

    replace = _flag(params.get("replace"))
    live = draft.get("live_push")
    venue = draft.get("venue") or params.get("venue")
    week_ending = str(draft.get("week_ending") or "")
    week_start = str(draft.get("week_start") or "")
    if live and not replace:
        return {
            "error": "a payroll journal for "
            + str(venue)
            + " WE "
            + _we(week_ending)
            + " is already in Xero ("
            + str(live.get("xero_status") or "?")
            + ", "
            + str(live.get("xero_id") or "")
            + ") — pass replace='true' to void it and push again, or void it first"
        }

    # ── 2. the card: every line, the totals, the residual ─────────────
    row = draft.get("draft") or {}
    lines = row.get("lines") or []
    journal = draft.get("journal") or {}
    basis = str(row.get("basis_sha256") or "")
    key = (
        "norm:payroll-journal:"
        + str(draft.get("venue_id"))
        + ":"
        + week_start
        + ":"
        + basis[:16]
    )
    # A re-push after a void (or a replace) needs a new key: Xero would answer
    # the same key with the journal just voided, the ledger with the row just
    # closed.
    attempts = int(draft.get("pushes_before") or 0)
    if attempts:
        key = key + ":r" + str(attempts)
    debit = int(row.get("debit_cents") or 0)
    credit = int(row.get("credit_cents") or 0)
    changes = []
    for ln in lines:
        acct = (
            str(ln.get("account_code") or "") + " " + str(ln.get("account_name") or "")
        ).strip()
        changes.append(
            {
                "field": str(ln.get("description") or "") + " → " + acct,
                "after": str(ln.get("side")) + " " + _money(ln.get("amount_cents")),
            }
        )
    changes.append({"field": "Total debits", "after": _money(debit)})
    changes.append({"field": "Total credits", "after": _money(credit)})
    changes.append(
        {
            "field": "Residual",
            "after": _money(row.get("residual_cents"))
            + (" (balanced)" if debit == credit else ""),
        }
    )
    changes.append(
        {
            "field": "Narration",
            "after": str(draft.get("narration") or journal.get("Narration") or ""),
        }
    )
    changes.append(
        {"field": "Lands as", "after": "Draft (the accountant posts it in Xero)"}
    )
    warnings = []
    if live and replace:
        warnings.append(
            "replaces the journal already in Xero ("
            + str(live.get("xero_id"))
            + ", "
            + str(live.get("xero_status"))
            + ") — it is voided first"
        )
    preview(
        {
            "title": "Post the payroll journal to Xero",
            "target": str(venue) + " — WE " + _we(week_ending),
            "venue": venue,
            "changes": changes,
            "warnings": warnings,
            "note": str(len(lines))
            + " lines, no tax (NoTax). Nothing is paid — this records the week's "
            "wages in the books as a draft journal.",
        },
        basis={
            "draft_id": row.get("id"),
            "basis_sha256": basis,
            "journal": journal,
            "replace": replace,
            "live_push_id": (live or {}).get("id"),
        },
    )

    # ── 3. the writes ──────────────────────────────────────────────────
    replaced = None
    if live and replace:
        status = str(live.get("xero_status") or "").upper()
        target = "VOIDED" if status == "POSTED" else "DELETED"
        voided = call_api(
            "xero",
            "update_manual_journal",
            {
                **vp,
                "manual_journal_id": live.get("xero_id"),
                "journal": {"Status": target},
            },
        )
        if isinstance(voided, dict) and voided.get("not_sent"):
            return {"preview_only": True}
        vrows, verr = xero_rows(voided, "ManualJournals")
        if verr:
            return {"error": "could not void the existing journal: " + str(verr)}
        if vrows and (vrows[0].get("HasErrors") or vrows[0].get("ValidationErrors")):
            return {
                "error": "Xero refused to void the existing journal: "
                + _xero_messages(vrows[0])
            }
        rec = call_api(
            "norm_payroll",
            "record_push",
            {
                **vp,
                "op": "void",
                "push_id": live.get("id"),
                "xero_status": (vrows[0].get("Status") if vrows else target),
            },
        )
        if isinstance(rec, dict) and rec.get("error"):
            return {
                "error": "the old journal was voided in Xero but Norm could not record it: "
                + str(rec["error"])
            }
        replaced = live.get("id")
        log("voided journal " + str(live.get("xero_id")) + " before re-pushing")

    result = call_api(
        "xero",
        "create_manual_journal",
        {**vp, "journal": journal, "idempotency_key": key},
    )
    if isinstance(result, dict) and result.get("not_sent"):
        return {"preview_only": True}
    rows, jerr = xero_rows(result, "ManualJournals")
    if jerr or not rows:
        return {
            "error": "Xero did not create the journal: " + str(jerr or "empty response")
        }
    mj = rows[0]
    if mj.get("HasErrors") or mj.get("ValidationErrors"):
        return {"error": "Xero rejected the journal: " + _xero_messages(mj)}
    journal_id = mj.get("ManualJournalID")
    log(
        "created Xero manual journal "
        + str(journal_id)
        + " as "
        + str(mj.get("Status"))
    )

    rec = call_api(
        "norm_payroll",
        "record_push",
        {
            **vp,
            "kind": "journal",
            "venue_id": draft.get("venue_id"),
            "week_start": week_start,
            "draft_id": row.get("id"),
            "xero_id": journal_id,
            "xero_status": mj.get("Status"),
            "idempotency_key": key,
            "request_payload": {"journal": journal},
            "response": {
                "ManualJournalID": journal_id,
                "Status": mj.get("Status"),
                "Date": mj.get("DateString") or mj.get("Date"),
                "Narration": mj.get("Narration"),
                "UpdatedDateUTC": mj.get("UpdatedDateUTC"),
            },
        },
    )
    ledger_error = None
    push_id = None
    if not isinstance(rec, dict) or rec.get("error"):
        ledger_error = (rec or {}).get("error") or "ledger not written"
        log(
            "WARNING: journal "
            + str(journal_id)
            + " is in Xero but the ledger refused: "
            + str(ledger_error)
        )
    else:
        push_id = rec.get("push_id")
    return {
        "pushed": True,
        "push_id": push_id,
        "xero_id": journal_id,
        "xero_status": mj.get("Status"),
        "narration": journal.get("Narration"),
        "date": week_ending,
        "venue": venue,
        "week_ending": week_ending,
        "lines": len(lines),
        "debit": round(debit / 100, 2),
        "credit": round(credit / 100, 2),
        "idempotency_key": key,
        "replaced_push_id": replaced,
        "url": _journal_url(settings.get("xero_short_code"), journal_id),
        "ledger_error": ledger_error,
    }
