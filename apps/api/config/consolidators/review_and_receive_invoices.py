# ruff: noqa: F821 — `datetime` is injected into the sandbox namespace by
# app/connectors/function_executor.py; it is not an import.
#
# Canonical function_code for the `loadedhub.review_and_receive_invoices`
# consolidator. This file is the reviewed, version-controlled source of truth;
# its contents are synced verbatim into the ConnectorSpec tool's
# consolidator_config.function_code in the config DB (see
# config/consolidators/README.md).
#
# ORCHESTRATION ONLY. All invoice intelligence — extraction, the replica,
# suggestions, confidence issues, the sensei, the receive write — lives
# server-side in app/services/invoice_review.py, reached through ONE internal
# tool: call_api("norm", "review_invoices"). This code decides WHICH invoices
# and WHICH policy (the run mode), then reports. It performs no matching, no
# validation math, and no Loaded calls of its own.
#
# Requires consolidator_config:
#   {"max_api_calls": 10, "allowed_write_actions": ["review_invoices"]}
#
# Run modes (injected by execute_consolidator from the user's workflow mode):
#   approve_all / unset → review + cards only, never writes ("dry run")
#   approve_fixes       → the service receives READY invoices with NOTHING to
#                         change; anything with a suggestion waits on a card
#   autopilot           → the service auto-accepts every suggestion (each
#                         recorded with actor "norm") and receives every
#                         invoice with no blocking issues
#
# "No valid purchase order" blocks autopilot by default; venues that don't
# care set require_valid_po false on the task config (norm.update_task_config)
# and the issue stops blocking.


def run(params, call_api, log, call_api_parallel=None):
    venue = params.get("venue")
    mode = params.get("mode") or "unset"
    mode_unset = mode == "unset"
    approve_all = mode in ("approve_all", "unset")
    autopilot = mode == "autopilot"
    dry_run = approve_all
    require_valid_po = params.get("require_valid_po") is not False

    # Optional `period` in plain English resolves through Norm's venue
    # calendar to CALENDAR dates (invoice dates are calendar-dated — no
    # trading boundary, so the window is sliced to dates). The zero-arg
    # default (last 60 days) that the playbooks rely on is unchanged.
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
        if not from_date and params.get("today"):
            from_date = (
                datetime.date.fromisoformat(params["today"])
                - datetime.timedelta(days=60)
            ).isoformat()

    # Single-invoice review: the Invoices page (or the receive-one chat tool)
    # opening ONE invoice. Same service pipeline, present-only — the editor
    # card is where the user acts.
    only_invoice_id = params.get("invoice_id")

    request = {
        "venue": venue,
        "require_valid_po": require_valid_po,
        # The sensei trains at most 2 brand-new suppliers per run, BEFORE
        # their extraction (the fresh spec is part of the extraction's cache
        # key) — the service owns the ordering; this is just the budget.
        "max_sensei": 2,
    }
    if only_invoice_id:
        request["invoice_ids"] = [only_invoice_id]
        request["mode"] = "approve_all"  # never auto-write a single review
        log("Single-invoice review: " + str(only_invoice_id))
    else:
        request["from_date"] = from_date
        request["to_date"] = to_date
        # Pass the mode through, including "unset". Substituting approve_all
        # here looked like a safe default and was really an override: the
        # server resolves the VENUE's setting and treats this as a ceiling, so
        # a hard-coded approve_all pinned every venue to it — the ladder could
        # be set to autopilot and never once take effect. The single-invoice
        # branch above still passes approve_all deliberately, which is a real
        # narrowing and still honoured.
        request["mode"] = mode

    result = call_api("norm", "review_invoices", request)
    if not isinstance(result, dict) or result.get("error"):
        return {
            "error": "Review service failed: "
            + str(result.get("error") if isinstance(result, dict) else result)
        }

    cards = result.get("cards") or []
    received_in = result.get("received") or []
    skipped_in = result.get("skipped") or []
    for s in result.get("sensei") or []:
        log(
            "sensei: trained on '"
            + str(s.get("supplier_name"))
            + "' (invoice "
            + str(s.get("invoice_id"))
            + ")"
        )

    def money(value):
        try:
            return "$" + format(float(value), ",.2f")
        except Exception:
            return "$" + str(value)

    def verdict(v, outcome):
        return {
            "invoice_id": v.get("invoice_id"),
            "reference_number": v.get("reference_number") or "(no number)",
            "supplier_name": v.get("supplier_name"),
            "po_number": v.get("po_number"),
            "total": money(v.get("total")),
            "reasons": v.get("reasons") or [],
            "outcome": outcome,
            "confidence": v.get("confidence"),
            "suggestions": v.get("suggestions") or 0,
        }

    received = [verdict(v, v.get("outcome") or "received") for v in received_in]
    skipped = [
        verdict(v, v.get("outcome") or "needs review")
        for v in skipped_in
        if v.get("outcome") != "ready to receive — awaiting approval"
    ]
    # "Ready, awaiting approval" reads as success in the chat report — the
    # user approves from the card (approve_all) — so report it under
    # received-pending rather than as a skip.
    awaiting = [
        verdict(v, "awaiting your approval")
        for v in skipped_in
        if v.get("outcome") == "ready to receive — awaiting approval"
    ]
    received = received + awaiting

    def confidence_summary(v):
        bits = []
        if v.get("confidence") == "ready":
            bits.append("ready ✓")
        elif v.get("reasons"):
            bits.append(str(len(v["reasons"])) + " blocking ✗")
        if v.get("suggestions"):
            bits.append(str(v["suggestions"]) + " suggested")
        return " ".join(bits) if bits else "—"

    rows = [
        {
            "reference": v["reference_number"],
            "supplier": v.get("supplier_name"),
            "po": v.get("po_number") or "—",
            "total": v["total"],
            "checks": confidence_summary(v),
            "outcome": v.get("outcome", "skipped"),
            "reasons": " • ".join(v["reasons"]) if v.get("reasons") else "—",
        }
        for v in received + skipped
    ]

    log(
        "Reviewed "
        + str(len(received_in) + len(skipped_in))
        + ": "
        + str(len(received_in))
        + " received, "
        + str(len(cards))
        + " card(s)"
    )

    return {
        "venue": venue,
        # What actually happened, not what this file guessed would. The venue
        # owns the rung and the server applies it, so predicting "dry run" from
        # a personal mode this workflow no longer reads would report "nothing
        # was received" over a batch that received.
        "dry_run": not received_in,
        "from_date": from_date,
        "to_date": to_date,
        "reviewed": len(received_in) + len(skipped_in),
        "results": rows,
        "received": received,
        "skipped": skipped,
        # Legacy field retained for the chat surface's fix-picker; the new
        # cards carry structured suggestions inside the working document.
        "fixes": [],
        "fix_invoices": cards,
        "mode": mode,
        "mode_unset": mode_unset,
        "auto_submit": autopilot,
        "summary": {"received": len(received_in), "skipped": len(skipped_in)},
    }
