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

    # What the accepted suggestions account for: {(scope, key, field): value}.
    explained: dict[tuple, Any] = {}
    added_line_ids: set[str] = set()
    struck_by_suggestion: set[str] = set()
    by_id = {str(s.get("id")): s for s in _live_suggestions(data)}
    for sid, action in _last_actions(data).items():
        if action.get("action") != "accepted":
            continue
        s = by_id.get(sid)
        if not s:
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

    for field in _HEADER_FIELDS:
        cur = data.get(field)
        if not _differs(field, cur, snap.get(field)):
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
                    supplier_name=data.get("supplier_name"),
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
