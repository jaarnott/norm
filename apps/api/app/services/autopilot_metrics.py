"""Did the human do anything autopilot wouldn't have?

Autopilot means: accept every suggestion (except ``delete_invoice``, which it
skips), then receive if nothing blocks. So every human receive is a free
experiment in that counterfactual — but only if we can tell what the human
actually did. Accepting all of Norm's suggestions AND quietly retyping a
quantity is a failure for autopilot even though every suggestion was accepted,
and that distinction is the whole point of this module.

The two public classifiers are PURE — no DB, no network, no clock — so the
taxonomy can be pinned exhaustively in tests. ``record_receive_outcome`` is the
only impure part, and it is written so that it can never fail a receive: by the
time it runs, Loaded has already accepted the PUT, and a metric is never worth
turning a completed receive into an error.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Mirrors invoice_review's tolerances — the same numbers that decide whether a
# suggestion was needed decide whether a value was hand-changed.
_MONEY_TOL = 0.011
_QTY_TOL = 0.001

# Header fields a human can change and that reach Loaded.
_HEADER_FIELDS = (
    "reference_number",
    "linked_supplier_id",
    "purchase_order_number",
    "linked_purchase_order_id",
    "issued_at",
    "due_at",
    "received_at",
    "total",
    "tax_amount",
    "discount_amount",
    "notes",
)
# NOT compared, deliberately:
#   subtotal      — receive_request_from_doc re-derives it from the lines
#   supplier_name — display mirror of linked_supplier_id (would double-count)

_LINE_FIELDS = (
    "quantity_received",
    "unit_cost",
    "unit",
    "linked_unit_id",
    "unit_ratio",
    "linked_item_id",
    "code",
    "description",
    "sale_tax_rate",
)
# NOT compared, deliberately:
#   total_cost                        — recomputed from qty x cost everywhere
#   tax_amount, brand, linked_brand_id — Loaded-derived, healed on every open
#   item_name/unit_name/quantity_ordered/reference_cost/on_order/
#   substitute_for/display_code/item_is_new/unit_is_new — written by the
#   server's own enrichers, never by a human

# Quantities compare tighter than money; everything else numeric takes the
# money tolerance.
_QTY_LIKE = {"quantity_received", "unit_ratio"}


def _differs(field: str, a: Any, b: Any) -> bool:
    """Values differ, with the same tolerances the review engine uses."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        tol = _QTY_TOL if field in _QTY_LIKE else _MONEY_TOL
        return abs(float(a) - float(b)) > tol
    if a is None or b is None:
        # None vs "" is not a change (an empty note is no note); None vs 0 is.
        if field == "notes":
            return bool(str(a or "").strip()) != bool(str(b or "").strip())
        return a is not b and (a is not None or b is not None)
    if isinstance(a, str) or isinstance(b, str):
        return str(a).strip() != str(b).strip()
    return a != b


def _last_actions(data: dict) -> dict[str, dict]:
    """Last action per suggestion/issue id — ``undone`` reverses an accept, so
    last-write-wins handles undo for free."""
    out: dict[str, dict] = {}
    for a in data.get("suggestion_actions") or []:
        if isinstance(a, dict) and a.get("suggestion_id"):
            out[str(a["suggestion_id"])] = a
    return out


def _live_suggestions(data: dict) -> list[dict]:
    """The suggestions autopilot would have acted on: replica_v1 entries,
    minus ``delete_invoice`` (autopilot skips it, so counting it as pending
    would mark every duplicate-flagged invoice as edited)."""
    return [
        s
        for s in data.get("suggestions") or []
        if isinstance(s, dict)
        and s.get("id")
        and s.get("kind")
        and s.get("kind") != "delete_invoice"
    ]


# ---------------------------------------------------------------------------
# The counterfactual: what would autopilot, with EVERY flag on, have sent —
# computed purely from artifacts the review already stores on the document
# (loaded_snapshot, server_filled, suggestions, issues). No forensics on which
# buttons a human pressed: the verdict is a field-level diff between the
# receive that happened and the receive the simulation produces. This is the
# report's headline ("with 'receive without a valid purchase order' on, 9 of
# your invoices would have auto-received identically"); the action-log
# classifier below it remains the audit trail the viewer shows.
# ---------------------------------------------------------------------------

#: The simulator's placeholder for "autopilot would CREATE this in Loaded" —
#: matched against the human's receive by NAME, since the created id can't be
#: known without doing the create.
_CREATE = "__create__"

_SIM_DELETE_KINDS = {"delete_invoice", "delete_non_invoice", "delete_unreadable"}

#: Line fields the sent-vs-simulated diff compares. Deliberately narrower
#: than _LINE_FIELDS: unit identity is compared semantically (see
#: _unit_matches), and code/description/sale_tax_rate are never a human-vs-
#: autopilot divergence on the same line id.
_SIM_LINE_FIELDS = ("quantity_received", "unit_cost")
_SIM_HEADER_FIELDS = (
    "reference_number",
    "linked_supplier_id",
    "linked_purchase_order_id",
)


def _pristine_doc(data: dict) -> dict | None:
    """The document as autopilot first saw it: Loaded's snapshot overlaid
    with Norm's own seeds (`server_filled`) — no human touch. None when the
    doc carries no usable snapshot."""
    snap = data.get("loaded_snapshot")
    if not isinstance(snap, dict) or not isinstance(snap.get("header"), dict):
        return None
    doc: dict = dict(snap["header"])
    filled = data.get("server_filled") or {}
    lines = []
    for ln in snap.get("lines") or []:
        if not isinstance(ln, dict):
            continue
        ln = dict(ln)
        f = filled.get(str(ln.get("id")))
        if isinstance(f, dict):
            ln.update(f)
        lines.append(ln)
    doc["lines"] = lines
    return doc


def simulate_autopilot(data: dict) -> dict | None:
    """Autopilot's counterfactual for one reviewed document, all flags on.

    Accept every suggestion (the existing pure applier), then walk the
    blocking issues: collect each one's gate into ``gates_needed``; a blocker
    an accepted suggestion already satisfies (its ``clears_when``) needs no
    flag; a blocker with NO gate goes to ``ungated`` — no flag exists, a
    person had to look. Folded create-actions apply placeholder values
    (matched later by name); a delete-gated blocker means autopilot would
    have DELETED the draft rather than received it.

    Pure — no Loaded, no DB. Returns ``{doc, gates_needed, ungated,
    would_delete}`` or None when there is no replica_v1 review to simulate.
    """
    if data.get("doc_schema") != "replica_v1":
        return None
    sim = _pristine_doc(data)
    if sim is None:
        return None
    from app.services.invoice_review import _clears, apply_suggestion

    for s in _live_suggestions(data):
        try:
            apply_suggestion(sim, s)
        except Exception:  # noqa: BLE001 — one bad apply must not void the verdict
            logger.info("simulate_autopilot: apply failed for %s", s.get("id"))
    gates: list[str] = []
    ungated: list[str] = []
    would_delete = False
    lines_by_id = {
        str(ln.get("id")): ln for ln in sim.get("lines") or [] if ln.get("id")
    }
    for i in data.get("issues") or []:
        if not isinstance(i, dict) or not i.get("blocking"):
            continue
        if _clears(sim, i):
            continue  # an accepted suggestion satisfies it — no flag needed
        act = i.get("action") if isinstance(i.get("action"), dict) else {}
        kind = str(act.get("kind") or "")
        payload = act.get("payload") if isinstance(act.get("payload"), dict) else {}
        apply_now = act.get("apply") if isinstance(act.get("apply"), dict) else None
        if kind in _SIM_DELETE_KINDS:
            would_delete = True
        ln = lines_by_id.get(str(i.get("line_id") or ""))
        if ln is not None:
            if kind == "create_unit" and payload.get("unit_name"):
                ln["unit"] = payload["unit_name"]
                ln["linked_unit_id"] = _CREATE
                ln["unit_ratio"] = None
            elif kind == "create_item":
                ln["linked_item_id"] = _CREATE
                if payload.get("name"):
                    ln["item_name"] = payload["name"]
            elif kind == "guess_unit" and apply_now:
                for k in ("unit", "linked_unit_id", "unit_ratio"):
                    if k in apply_now:
                        ln[k] = apply_now[k]
            elif kind == "strike":
                ln["struck"] = True
        gate = i.get("gate")
        if gate:
            if gate not in gates:
                gates.append(str(gate))
        else:
            code = str(i.get("code") or "issue")
            if code not in ungated:
                ungated.append(code)
    return {
        "doc": sim,
        "gates_needed": gates,
        "ungated": ungated,
        "would_delete": would_delete,
    }


def _norm_name(v: object) -> str:
    return "".join(ch for ch in str(v or "").lower() if ch.isalnum())


def _unit_matches(sent: dict, sim: dict) -> bool:
    """The same physical unit? Ids when both are real; NAMES when the
    simulation only knows 'a unit called 6x700ml would be created' — or when
    the human picked an equivalently-named existing record."""
    from app.services.invoice_units import _unit_norm, units_equivalent

    if sim.get("linked_unit_id") and sim.get("linked_unit_id") != _CREATE:
        if str(sent.get("linked_unit_id") or "") == str(sim["linked_unit_id"]):
            return True
    a, b = sent.get("unit"), sim.get("unit")
    if not a and not b:
        return True
    return bool(a and b and (_unit_norm(a) == _unit_norm(b) or units_equivalent(a, b)))


def _item_matches(sent: dict, sim: dict) -> bool:
    if sim.get("linked_item_id") == _CREATE:
        # Autopilot would have created an item by this name; the human's
        # receive used SOME item — same decision if the names agree.
        a, b = _norm_name(sent.get("item_name")), _norm_name(sim.get("item_name"))
        return bool(sent.get("linked_item_id")) and bool(
            a and b and (a == b or a in b or b in a)
        )
    return str(sent.get("linked_item_id") or "") == str(sim.get("linked_item_id") or "")


def receive_diff(data: dict, sim_doc: dict) -> list[dict]:
    """Field-level differences between the receive that happened (``data``,
    the doc as received) and the simulated one. Empty = autopilot would have
    sent the identical receive. Compares the fields
    ``receive_request_from_doc`` projects into the receive payload."""
    diffs: list[dict] = []
    for field in _SIM_HEADER_FIELDS:
        if _differs(field, data.get(field), sim_doc.get(field)):
            diffs.append(
                {
                    "path": f"header.{field}",
                    "sent": data.get(field),
                    "auto": sim_doc.get(field),
                }
            )
    sim_lines = {
        str(ln.get("id")): ln for ln in sim_doc.get("lines") or [] if ln.get("id")
    }
    seen: set[str] = set()
    for ln in data.get("lines") or []:
        if not isinstance(ln, dict) or not ln.get("id"):
            continue
        lid = str(ln["id"])
        seen.add(lid)
        sl = sim_lines.get(lid)
        if sl is None:
            if not ln.get("struck"):
                diffs.append(
                    {
                        "path": f"line:{lid}.added",
                        "sent": ln.get("description"),
                        "auto": None,
                    }
                )
            continue
        if bool(ln.get("struck")) != bool(sl.get("struck")):
            diffs.append(
                {
                    "path": f"line:{lid}.struck",
                    "sent": bool(ln.get("struck")),
                    "auto": bool(sl.get("struck")),
                }
            )
            continue
        if ln.get("struck"):
            continue  # struck on both sides — the values are moot
        if not _item_matches(ln, sl):
            diffs.append(
                {
                    "path": f"line:{lid}.item",
                    "sent": ln.get("item_name") or ln.get("linked_item_id"),
                    "auto": sl.get("item_name") or sl.get("linked_item_id"),
                }
            )
        if not _unit_matches(ln, sl):
            diffs.append(
                {
                    "path": f"line:{lid}.unit",
                    "sent": ln.get("unit"),
                    "auto": sl.get("unit"),
                }
            )
        for field in _SIM_LINE_FIELDS:
            if _differs(field, ln.get(field), sl.get(field)):
                diffs.append(
                    {
                        "path": f"line:{lid}.{field}",
                        "sent": ln.get(field),
                        "auto": sl.get(field),
                    }
                )
    for lid, sl in sim_lines.items():
        if lid not in seen and not sl.get("struck"):
            diffs.append(
                {
                    "path": f"line:{lid}.missing",
                    "sent": None,
                    "auto": sl.get("description"),
                }
            )
    return diffs


def auto_verdict(data: dict, *, received: bool = True) -> dict:
    """The end-state verdict stored under ``detail.auto``.

    matched     autopilot (all flags on) sends the identical receive
    differed    it would have sent something else — the diffs say what
    never_auto  blockers no flag can authorise — a person had to look
    unscored    no review to simulate from (or the simulation failed)
    """
    try:
        sim = simulate_autopilot(data)
        if sim is None:
            return {"verdict": "unscored"}
        diffs = receive_diff(data, sim["doc"])
        if sim["would_delete"] and received:
            diffs.append({"path": "outcome", "sent": "received", "auto": "deleted"})
        if sim["ungated"]:
            verdict = "never_auto"
        elif diffs:
            verdict = "differed"
        else:
            verdict = "matched"
        return {
            "verdict": verdict,
            "gates_needed": sim["gates_needed"],
            "ungated": sim["ungated"],
            "diffs": diffs[:40],
        }
    except Exception as exc:  # noqa: BLE001 — metrics must never break a receive
        logger.info("auto_verdict failed: %s", exc)
        return {"verdict": "unscored"}


#: Fields a folded blocker-action changes on its line, by action kind — the
#: value-blind excuse used when an accepted blocker recorded no values.
_ISSUE_ACTION_FIELDS = {
    "create_unit": ("unit", "linked_unit_id", "unit_ratio"),
    "guess_unit": ("unit", "linked_unit_id", "unit_ratio"),
    "create_item": ("linked_item_id", "item_name", "linked_brand_id", "brand"),
    "create_brand": ("linked_brand_id", "brand"),
}


def manual_edits(data: dict) -> list[str]:
    """Field paths the HUMAN typed, as opposed to accepted from a suggestion.

    Baseline is ``loaded_snapshot`` — Loaded's own values, refreshed on every
    open — because editor changes stay local until the receive PUT. Every
    difference from it is subtracted by the accepted suggestions that explain
    it; what remains was typed by a person.

    A field counts as explained ONLY while the working value still equals the
    accepted value, so accept-then-tweak reads as manual. That is the honest
    answer for the autopilot question: autopilot would have stopped at the
    accepted value.
    """
    snap = data.get("loaded_snapshot")
    if not isinstance(snap, dict):
        return []  # no baseline → say nothing rather than guess
    # `loaded_snapshot` is {"header": {...}, "lines": [...]} — see
    # received_invoice.loaded_snapshot. Reading header fields off `snap` itself
    # found nothing, so every populated header field always "differed" and every
    # receive was recorded as hand-edited: 29 of the first 31 production rows
    # carried 4-9 "manual edits" that were ONLY header fields, the same ones
    # each time (reference_number 30x, linked_supplier_id 30x, issued_at 30x).
    # `clean` therefore never happened and autopilot readiness read 0% from the
    # day it shipped. The lines below were always read correctly, which is why
    # only the header was affected.
    # No header baseline is NOT the same as "Loaded held nothing": treating it
    # as empty is precisely what produced the phantom edits, so say nothing
    # about the header instead — the same rule the missing-snapshot case above
    # already follows.
    snap_header = snap.get("header")
    header_fields = _HEADER_FIELDS if isinstance(snap_header, dict) else ()
    snap_header = snap_header if isinstance(snap_header, dict) else {}

    # What the accepted suggestions account for: {(scope, key, field): value}.
    explained: dict[tuple, Any] = {}
    # ...and what NORM filled in before anyone looked. `loaded_snapshot` is
    # Loaded's raw draft, but the server then resolves the supplier variant and
    # completes the line (invoice_po_reference.seed_working_from_loaded), so
    # those fields differ from the baseline without a person having touched
    # them: a Service Foods invoice received untouched was recorded as
    # hand-edited on unit, linked_unit_id, unit_ratio and linked_item_id.
    # Same rule as an accepted suggestion — explained only while the value is
    # still the one that was filled.
    for lid, fields in (data.get("server_filled") or {}).items():
        if isinstance(fields, dict):
            for field, value in fields.items():
                explained[("line", str(lid), field)] = value
    added_line_ids: set[str] = set()
    struck_by_suggestion: set[str] = set()
    # Fields an accepted blocker-action OWNS on its line, excused even when no
    # values were recorded (gate-walk records and blocker accepts predating
    # the card recording before/after). Value-blind, so accept-then-tweak on
    # these fields is excused too — the recorded `after` path above it keeps
    # the strict contract wherever values exist.
    explained_any: set[tuple] = set()
    by_id = {str(s.get("id")): s for s in _live_suggestions(data)}
    issues_by_id = {
        str(i.get("id")): i
        for i in data.get("issues") or []
        if isinstance(i, dict) and i.get("id")
    }
    for sid, action in _last_actions(data).items():
        if action.get("action") != "accepted":
            continue
        s = by_id.get(sid)
        if not s:
            # The one-button doctrine folds remedy suggestions ONTO their
            # blockers (fold_remedies_into_blockers), so a create/strike
            # accept is recorded against the ISSUE id and appears in no
            # suggestion list. Skipping those read every folded accept as
            # hand-typing: a no-edits receive of Federal Merchants 396152
            # reported "hand-edited unit, linked_unit_id, unit_ratio" for
            # the one line whose unit was CREATED via the blocker's Accept
            # (19 Aug 2026).
            issue = issues_by_id.get(sid)
            if not isinstance(issue, dict):
                continue
            act = issue.get("action") if isinstance(issue.get("action"), dict) else {}
            lid = str(issue.get("line_id") or "")
            after = action.get("after")
            if not isinstance(after, dict):
                after = act.get("apply") if isinstance(act.get("apply"), dict) else None
            if str(act.get("kind")) == "strike" and lid:
                struck_by_suggestion.add(lid)
            if isinstance(after, dict) and lid:
                for field, value in after.items():
                    explained[("line", lid, field)] = value
            elif lid:
                for field in _ISSUE_ACTION_FIELDS.get(str(act.get("kind")), ()):
                    explained_any.add(("line", lid, field))
            continue
        after = action.get("after")
        if not isinstance(after, dict):
            after = s.get("apply") if isinstance(s.get("apply"), dict) else {}
        if s.get("kind") == "add_line":
            before = action.get("before")
            lid = (
                (before or {}).get("added_line_id")
                if isinstance(before, dict)
                else None
            )
            if lid:
                added_line_ids.add(str(lid))
            continue
        if s.get("kind") == "strike" and s.get("line_id"):
            struck_by_suggestion.add(str(s["line_id"]))
        for field, value in (after or {}).items():
            if s.get("line_id"):
                explained[("line", str(s["line_id"]), field)] = value
            else:
                explained[("header", field)] = value

    out: list[str] = []

    for field in header_fields:
        cur = data.get(field)
        if not _differs(field, cur, snap_header.get(field)):
            continue
        key = ("header", field)
        if key in explained and not _differs(field, cur, explained[key]):
            continue  # exactly what the accepted suggestion wrote
        out.append(f"header.{field}")

    snap_lines = {
        str(ln.get("id")): ln
        for ln in (snap.get("lines") or [])
        if isinstance(ln, dict) and ln.get("id")
    }
    seen: set[str] = set()
    for ln in data.get("lines") or []:
        if not isinstance(ln, dict) or not ln.get("id"):
            continue
        lid = str(ln["id"])
        seen.add(lid)
        base = snap_lines.get(lid)
        if base is None:
            # A line Loaded never had: an accepted add_line, or hand-added.
            if lid not in added_line_ids:
                out.append(f"line:{lid}.added")
            continue
        for field in _LINE_FIELDS:
            cur = ln.get(field)
            if not _differs(field, cur, base.get(field)):
                continue
            key = ("line", lid, field)
            if key in explained and not _differs(field, cur, explained[key]):
                continue
            if key in explained_any:
                continue  # an accepted blocker-action owns this field
            out.append(f"line:{lid}.{field}")
        # `struck` is local-only, so it never appears in the snapshot.
        if ln.get("struck") and lid not in struck_by_suggestion:
            out.append(f"line:{lid}.struck")

    for lid in snap_lines:
        if lid not in seen:
            # Shouldn't happen (the editor strikes, never deletes) — record it
            # rather than let a real divergence pass silently.
            out.append(f"line:{lid}.removed")
    return out


def classify_outcome(data: dict, *, received: bool) -> dict:
    """The counts + the verdict. Pure; see the module docstring."""
    suggestions = _live_suggestions(data)
    actions = _last_actions(data)
    sugg_ids = {str(s["id"]) for s in suggestions}
    issue_ids = {
        str(i.get("id"))
        for i in data.get("issues") or []
        if isinstance(i, dict) and i.get("id")
    }

    accepted = dismissed = 0
    dismissed_ids: list[str] = []
    for sid in sugg_ids:
        a = actions.get(sid)
        act = (a or {}).get("action")
        if act == "accepted":
            accepted += 1
        elif act == "dismissed":
            dismissed += 1
            dismissed_ids.append(sid)
    pending_ids = [
        sid
        for sid in sugg_ids
        if (actions.get(sid) or {}).get("action") not in ("accepted", "dismissed")
    ]
    pending = len(pending_ids)

    # A blocking issue waved through ("I've checked this") shares the action
    # log but lives in a different id namespace — it is NOT a dismissed
    # suggestion. It means autopilot would have STOPPED, which is reported
    # separately rather than counted as autopilot being wrong.
    waved = sum(
        1
        for iid in issue_ids
        if (actions.get(iid) or {}).get("action") in ("accepted", "dismissed")
    )
    blocking = sum(1 for i in data.get("issues") or [] if i.get("blocking"))

    fields = manual_edits(data)
    reviewed = bool(data.get("reviewed_at"))

    if not received:
        outcome = "dojo"
    elif not reviewed:
        outcome = "not_reviewed"
    elif dismissed or pending or fields:
        outcome = "edited"
    elif suggestions:
        outcome = "clean"
    else:
        outcome = "no_suggestions"

    kinds: dict[str, int] = {}
    for s in data.get("suggestions") or []:
        if isinstance(s, dict) and s.get("kind"):
            kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1

    return {
        "outcome": outcome,
        "suggestion_count": len(suggestions),
        "accepted_count": accepted,
        "dismissed_count": dismissed,
        "pending_count": pending,
        "manual_edit_count": len(fields),
        "blocking_issue_count": blocking,
        "issues_waved_count": waved,
        "confidence": data.get("confidence"),
        "detail": {
            "manual_fields": fields,
            "suggestion_kinds": kinds,
            "dismissed_ids": dismissed_ids,
            "pending_ids": pending_ids,
            # Did the review run against the invoice as Loaded holds it now?
            # A stale baseline can make an untouched field look hand-edited.
            "baseline_fresh": bool(
                data.get("reviewed_invoice_fingerprint")
                and data.get("reviewed_invoice_fingerprint")
                == data.get("loaded_invoice_fingerprint")
            ),
            "is_credit_note": bool(data.get("is_credit_note")),
            "reviewed_at": data.get("reviewed_at"),
        },
    }


def record_receive_outcome(
    db,  # noqa: ARG001 — kept for call-site symmetry; we use our OWN session
    *,
    venue_id: str,
    invoice_id: str,
    data: dict,
    mode: str,
    actor: str = "user",
    user_id: str | None = None,
    working_document_id: str | None = None,
    thread_id: str | None = None,
    received: bool = True,
    outcome_override: str | None = None,
    dojo: dict | None = None,
    supplier_name: str | None = None,
) -> None:
    """Write one outcome row. Never raises, never touches the caller's session.

    Its own session on purpose: a failed flush poisons a SQLAlchemy session
    until it is rolled back, and on this path Loaded has ALREADY accepted the
    receive — a metric bug must not turn that into an error the user sees.
    """
    try:
        from sqlalchemy.exc import IntegrityError

        from app.db.engine import SessionLocal
        from app.db.models import InvoiceAutopilotOutcome, Venue

        if not venue_id or not invoice_id:
            return
        data = data if isinstance(data, dict) else {}
        if not data and not outcome_override:
            # The legacy client-built receive path carries no working document,
            # so there is nothing honest to say about how it was received. An
            # explicit outcome (Cannot receive) still counts: the human's
            # verdict IS the measurement, doc or no doc.
            return

        counts = classify_outcome(data, received=received)
        outcome = outcome_override or counts["outcome"]
        if dojo:
            counts["detail"]["dojo"] = dojo
        if received and data:
            # The end-state verdict: would autopilot, all flags on, have sent
            # THIS receive? Powers the per-flag report ("with X on, N of your
            # invoices would have auto-received identically").
            counts["detail"]["auto"] = auto_verdict(data, received=received)

        session = SessionLocal()
        try:
            exists = (
                session.query(InvoiceAutopilotOutcome)
                .filter(
                    InvoiceAutopilotOutcome.venue_id == venue_id,
                    InvoiceAutopilotOutcome.invoice_id == invoice_id,
                    InvoiceAutopilotOutcome.outcome == outcome,
                )
                .first()
            )
            if exists:
                return  # a retry, not a second event
            venue = session.query(Venue).filter(Venue.id == venue_id).first()
            session.add(
                InvoiceAutopilotOutcome(
                    organization_id=getattr(venue, "organization_id", None),
                    venue_id=venue_id,
                    invoice_id=invoice_id,
                    reference_number=data.get("reference_number"),
                    # The document is the best source, but "Can't receive" can be
                    # pressed on an invoice that was never opened as a draft — there is
                    # no document then, and the row landed in the report's
                    # "(no supplier)" bucket, which is the one place a supplier's
                    # training history most needs to be attributed.
                    supplier_name=data.get("supplier_name") or supplier_name,
                    linked_supplier_id=data.get("linked_supplier_id"),
                    outcome=outcome,
                    received=bool(received),
                    mode=mode,
                    actor=actor,
                    user_id=user_id,
                    working_document_id=working_document_id,
                    thread_id=thread_id,
                    suggestion_count=counts["suggestion_count"],
                    accepted_count=counts["accepted_count"],
                    dismissed_count=counts["dismissed_count"],
                    pending_count=counts["pending_count"],
                    manual_edit_count=counts["manual_edit_count"],
                    blocking_issue_count=counts["blocking_issue_count"],
                    issues_waved_count=counts["issues_waved_count"],
                    confidence=counts["confidence"],
                    detail=counts["detail"],
                )
            )
            session.commit()
        except IntegrityError:
            session.rollback()  # the unique index won a race — fine
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — a metric must never fail a receive
        logger.exception(
            "autopilot outcome not recorded for invoice %s", str(invoice_id)[:12]
        )
