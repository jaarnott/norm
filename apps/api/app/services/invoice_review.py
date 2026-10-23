"""The invoice review service — the Replica as the single engine.

Four views of one invoice, and the card's tabs are exactly these:

- **Extracted** — what the AI read off the supplier's invoice, untouched.
- **Loaded** — what Loaded's Receive screen shows.
- **Replica** — what we think the invoice says: the extraction resolved
  against the catalogue (``invoice_replica.build_replica``), then finished by
  every rule Norm has (``replica_finish``). Final; nothing overrides it later.
- **Norm** — the working values: Loaded, plus the decisions made on top of
  it (accepted suggestions, a person's own edits — ``invoice_edits``). Every
  place the Replica differs from Loaded is ONE suggestion. Nothing else makes
  one, and nothing else writes a line (owner, 19 Oct 2026: no ghost data).

The pipeline, one for every surface (web editor, embedded card, chat batch):

1. Shape the WORKING VALUES from Loaded's draft, opening where Loaded's
   screen opens (``enrich_loaded_snapshot`` resolves it once, on the
   snapshot; ``start_from_screen`` opens the lines on it).
2. Extract the invoice (cached, ``invoice_extraction``) and build the replica.
3. Pair replica lines to Loaded lines ONCE (``pair_lines``), finish the
   replica, and turn every difference into a suggestion
   (``build_suggestions``).
4. Raise the blockers — what stops a receive — and give each the
   suggestion(s) that clear it (``link_fixes``): a blocker is never a
   decision of its own, it marks one.
5. Autopilot (``review_invoices(..., mode="autopilot")``) runs the SAME
   pipeline, accepts what the Replica is sure of plus what the venue's
   switches allow (``autopilot_accept``), and receives through
   ``do_receive`` when no blocker stands.

The working document contract (``doc_schema: "replica_v2"``): working values
at the top level, the ``replica`` / ``loaded_snapshot`` /
``extracted_snapshot`` sidecars, ``suggestions`` ({id, kind, field, line_id,
current, proposed, explanation, apply|payload, sure, gate?}), ``issues``
({id, code, blocking, line_id, message, gate?, clears_when?, fix: [ids]}),
``suggestion_actions`` (the record), ``confidence``, ``reviewed_at`` and the
fingerprint pair.
"""

from __future__ import annotations

import copy
import datetime
import decimal
import logging
import re

from sqlalchemy.orm import Session

from app.services import invoice_line_match as line_match
from app.services import venue_autopilot as _VA
from app.services.invoice_extraction import (
    compose_pdf_instructions,
    extract_invoice_copy,
    extraction_from_loaded,
    find_spec_for_supplier,
    is_feed_invoice,
    ordermentum_order_number,
    ordermentum_twin_message,
    pdf_instructions_for,
)
from app.services.autopilot_metrics import record_receive_outcome
from app.services.invoice_po_reference import (
    attach_po_reference,
    enrich_loaded_snapshot,
    fetch_po_reference,
    loaded_reference,
    po_id_for,
    project_po_reference,
    start_from_screen,
)
from app.services.invoice_replica import build_replica, received_copy, received_feed_for
from app.services.replica_finish import (
    LOADED_ROUNDING,
    TOTALS_ISSUE,
    _totals_view,
    finish_replica,
)
from app.services.received_invoice import (
    LoadedInvoiceClient,
    _norm,
    build_received_invoice_data,
    do_receive,
    invalidate_conflicting_drafts,
    open_drafts,
    receive_request_from_doc,
)

logger = logging.getLogger(__name__)

#: v2 (12 Oct 2026): every rule inside the replica, blockers point at their
#: fix. A draft reviewed under v1 re-reviews once on its next open, keeping
#: the decisions already made (``carry_forward_decisions``).
DOC_SCHEMA = "replica_v2"

_QTY_TOL = 0.001
_MONEY_TOL = 0.011

#: Loaded stores received quantities to 2 decimal places — measured across
#: every quantity it holds in production (868 values: 714 at 0dp, 72 at 1dp,
#: 82 at 2dp, none finer). A suggestion proposing more precision than that can
#: never be satisfied: accept 0.565, PUT it, Loaded stores 0.57, the next open
#: reseeds from Loaded and the suggestion returns — permanently pending, and
#: every receive reads "autopilot would have differed" (SI03448887, 26 Aug
#: 2026). So the copy's quantity is judged at the precision Loaded can keep.
_LOADED_QTY_DP = 2

# extracted_snapshot header projection — verbatim extraction values.
_SNAPSHOT_HEADER_KEYS = (
    "document_type",
    "invoice_number",
    "invoice_date",
    "supplier_name",
    "customer_purchase_order_number",
    "supplier_order_number",
    "subtotal_ex_tax",
    "discount_amount",
    "tax_amount",
    "total_incl_tax",
    "supplier_differs",
)


def _f(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _storable_qty(value: object) -> object:
    """The copy's quantity as Loaded would keep it. Non-numbers pass through.

    HALF-UP, not Python's ``round``: 0.565 banker's-rounds to 0.56 while Loaded
    stores 0.57, and proposing 0.56 against Loaded's 0.57 would invent the very
    difference this is meant to remove.
    """
    f = _f(value)
    if f is None:
        return value
    quantum = decimal.Decimal(1).scaleb(-_LOADED_QTY_DP)
    return float(
        decimal.Decimal(str(f)).quantize(quantum, rounding=decimal.ROUND_HALF_UP)
    )


def _differ(a: object, b: object, tol: float) -> bool:
    fa, fb = _f(a), _f(b)
    if fa is not None and fb is not None:
        return abs(fa - fb) > tol
    return a != b


def _date_part(v: object) -> str:
    return str(v or "")[:10]


def _extracted_snapshot(extraction: dict) -> dict:
    return {
        "header": {k: extraction.get(k) for k in _SNAPSHOT_HEADER_KEYS},
        "lines": [
            dict(ln) for ln in extraction.get("lines") or [] if isinstance(ln, dict)
        ],
    }


# ---------------------------------------------------------------------------
# Pairing — replica lines ↔ Loaded lines, ONCE, at review time
# ---------------------------------------------------------------------------


def pair_lines(
    replica_lines: list[dict], doc_lines: list[dict]
) -> tuple[dict[str, str], list[dict], list[dict], list[dict]]:
    """Pair each replica line to a working (Loaded) line.

    Tiers per replica line, among still-unclaimed lines: linked_item_id →
    normalized code → plain description match (the parity-tested
    ``invoice_line_match`` rules). Several unclaimed lines of the same item or
    code are the same product — Alsco lists each rental item once a week
    (16 Oct 2026) — so the one whose quantity and cost also agree is taken,
    else the first: the copy's values are written over it either way. A
    replica line whose only hits are already claimed is CONTESTED (returned
    as ambiguous for the arbiter).

    Returns ``(pairs {replica_id: loaded_id}, ambiguous replica lines,
    unpaired replica lines, unpaired loaded lines)``.
    """
    unclaimed: dict[str, dict] = {
        str(ln.get("id")): ln for ln in doc_lines if ln.get("id")
    }
    pairs: dict[str, str] = {}
    ambiguous: list[dict] = []
    unpaired_rep: list[dict] = []

    def _claim(rl_id: str, ln: dict) -> None:
        lid = str(ln.get("id"))
        pairs[rl_id] = lid
        unclaimed.pop(lid, None)

    def _best(rl: dict, hits: list[dict]) -> dict:
        qty, cost = _f(rl.get("quantity_received")), _f(rl.get("unit_cost"))
        for ln in hits:
            if not _differ(ln.get("quantity_received"), qty, _QTY_TOL) and not _differ(
                ln.get("unit_cost"), cost, _MONEY_TOL
            ):
                return ln
        return hits[0]

    for rl in replica_lines:
        rl_id = str(rl.get("id"))
        contested = False  # a tier matched only lines another rep line claimed

        item = rl.get("linked_item_id")
        if item:
            hits = [ln for ln in unclaimed.values() if ln.get("linked_item_id") == item]
            if hits:
                _claim(rl_id, _best(rl, hits))
                continue
            if not hits and any(ln.get("linked_item_id") == item for ln in doc_lines):
                contested = True

        code = _norm(rl.get("code")) if rl.get("code") else ""
        if code:
            hits = [ln for ln in unclaimed.values() if _norm(ln.get("code")) == code]
            if hits:
                _claim(rl_id, _best(rl, hits))
                continue
            if not hits and any(_norm(ln.get("code")) == code for ln in doc_lines):
                contested = True

        pool = [
            {"code": ln.get("code"), "description": ln.get("description"), "_ln": ln}
            for ln in unclaimed.values()
        ]
        m = line_match.plain_match(
            {"code": rl.get("code"), "description": rl.get("description")}, pool
        )
        if m is not None:
            _claim(rl_id, m["_ln"])
            continue

        (ambiguous if contested else unpaired_rep).append(rl)

    # Last resort: MONEY. A Loaded line with no code and no description matches
    # nothing above, so a copy line describing the same delivery looks like two
    # separate problems — "a Loaded line not on the copy" (strike it) and "a
    # copy line not in Loaded" (add it). Both were suggested on Red and White
    # Cellars INV562277, and the outcome was the worst of both: Loaded's own
    # freight line was deleted and the replacement never arrived, because
    # Loaded's invoice PUT does not create lines from an entry with no id.
    #
    # Quantity AND cost agreeing to the cent is strong evidence it is the same
    # line — and only when exactly one candidate matches on each side, because
    # two lines at the same price is exactly when a guess would be wrong.
    if unpaired_rep and unclaimed:
        for rl in list(unpaired_rep):
            qty, cost = _f(rl.get("quantity_received")), _f(rl.get("unit_cost"))
            if qty is None or cost is None:
                continue
            hits = [
                ln
                for ln in unclaimed.values()
                if not _differ(ln.get("quantity_received"), qty, _QTY_TOL)
                and not _differ(ln.get("unit_cost"), cost, _MONEY_TOL)
            ]
            if len(hits) != 1:
                continue
            # And only into a line that names nothing — a Loaded line WITH a
            # code or description that failed every match above is a different
            # product that happens to cost the same.
            other = hits[0]
            if other.get("code") or other.get("description"):
                continue
            _claim(str(rl.get("id")), other)
            unpaired_rep.remove(rl)

    return pairs, ambiguous, unpaired_rep, list(unclaimed.values())


# ---------------------------------------------------------------------------
# Suggestions + issues — every replica↔Loaded difference, structured
# ---------------------------------------------------------------------------


def _sugg(
    out: list[dict],
    kind: str,
    *,
    field: str | None = None,
    line_id: str | None = None,
    current: object = None,
    proposed: object = None,
    explanation: str,
    apply: dict | None = None,
    payload: dict | None = None,
    sid: str | None = None,
    confidence: str | None = None,
    sure: bool = True,
    gate: str | None = None,
    fixes: str | None = None,
) -> None:
    """One suggestion. ``sure`` False marks the Replica's guess — never
    applied unattended unless ``gate`` names a venue switch that is on (the
    creates, deletes, phantom strikes and unit picks carry one). ``fixes``
    names the blocker a rule set this value to clear."""
    parts = [kind] + [p for p in (field, line_id) if p]
    entry: dict = {
        "id": sid or ":".join(parts),
        "kind": kind,
        "field": field,
        "line_id": line_id,
        "current": current,
        "proposed": proposed,
        "explanation": explanation,
        "sure": sure,
    }
    if apply is not None:
        entry["apply"] = apply
    if payload is not None:
        entry["payload"] = payload
    if gate:
        entry["gate"] = gate
    if fixes:
        entry["fixes"] = fixes
    # Model-produced suggestions carry their confidence (high|medium) so the
    # card can show how sure the resolver was. Deterministic ones omit it.
    if confidence is not None:
        entry["confidence"] = confidence
    out.append(entry)


def _money(value: object) -> str:
    f = _f(value)
    return "$" + format(f, ",.2f") if f is not None else str(value)


def _reconciled_unit_cost(rl: dict) -> tuple[object, object]:
    """``(unit_cost_to_propose, billed_total_if_it_disagreed)``.

    A supplier billing by weight prints a quantity Loaded cannot hold: 0.923 kg
    becomes 0.92, and the line's money moves. The copy then contradicts itself —
    Bidfood 90ea78ed (26 Aug 2026) printed 0.92 x 53.61 with a line total of
    49.48, sixteen cents adrift, and Loaded rejected the whole receive with
    ``invoice-totals-mismatch``.

    The MONEY is the fact worth keeping: it is what the supplier charged and
    what the statement reconciles against. The quantity cannot be corrected
    (Loaded rounds it straight back), so solve for the unit cost that
    reproduces the billed total at the quantity Loaded can store. Only when
    that cost actually reproduces it — otherwise say nothing and let the totals
    gate ask a person rather than invent a number that nearly works.
    """
    cost, qty, billed = (
        rl.get("unit_cost"),
        _f(rl.get("quantity_received")),
        _f(rl.get("total_cost")),
    )
    if _f(cost) is None or not qty or billed is None:
        return cost, None
    if abs(qty * _f(cost) - billed) <= _MONEY_TOL:
        return cost, None  # the copy agrees with itself
    # Only when the ROUNDED-QUANTITY story actually explains the gap: the
    # quantity implied by the billed total at the printed price must round to
    # the quantity on the line. 49.48 / 53.61 = 0.9229… -> 0.92, which is what
    # Loaded holds, so "the supplier billed 0.923 kg" fits. A line billing $90
    # for 2 x $10 implies 9 units, nothing to do with rounding — there the
    # figure that was misread is anyone's guess, and inventing a $45 unit price
    # would be worse than saying nothing.
    implied = billed / _f(cost)
    if abs(round(implied, _LOADED_QTY_DP) - round(qty, _LOADED_QTY_DP)) > 1e-9:
        return cost, None
    fixed = round(billed / qty, 2)
    if abs(qty * fixed - billed) > _MONEY_TOL:
        return cost, None  # no storable cost reproduces the billed total
    return fixed, billed


def _line_suggestions(suggestions: list[dict], rl: dict, ln: dict, lid: str) -> None:
    """Every field where the Replica's line differs from Loaded's, as one
    suggestion each. The Replica's values are final (``replica_finish`` has
    run), so this only compares — it decides nothing."""
    desc = ln.get("description") or rl.get("description") or ln.get("code") or "line"
    fixes = rl.get("fixes") or {}
    copy_qty = _storable_qty(rl.get("quantity_received"))
    if _differ(ln.get("quantity_received"), copy_qty, _QTY_TOL) and (
        rl.get("quantity_received") is not None
    ):
        _sugg(
            suggestions,
            "line_value",
            field="quantity_received",
            line_id=lid,
            current=ln.get("quantity_received"),
            proposed=copy_qty,
            explanation=rl.get("quantity_received_why")
            or (
                f"the invoice bills quantity {copy_qty} for "
                f"'{desc}' — Loaded read {ln.get('quantity_received')}"
            ),
            # The STORABLE value, not the invoice's raw one: applying 0.565
            # would be silently rounded to 0.57 by Loaded and the suggestion
            # would come straight back on the next open.
            apply={"quantity_received": copy_qty},
            fixes=fixes.get("quantity_received"),
            confidence=rl.get("fixes_confidence")
            if "quantity_received" in fixes
            else None,
        )
    if rl.get("unit_cost_set"):
        # A rule set the cost outright (a misread figure).
        copy_cost, billed = rl.get("unit_cost"), None
    else:
        copy_cost, billed = _reconciled_unit_cost(rl)
    if _differ(ln.get("unit_cost"), copy_cost, _MONEY_TOL) and copy_cost is not None:
        _sugg(
            suggestions,
            "line_value",
            field="unit_cost",
            line_id=lid,
            current=ln.get("unit_cost"),
            proposed=copy_cost,
            explanation=rl.get("unit_cost_why")
            or (
                (
                    f"the invoice bills {_money(billed)} for '{desc}' but "
                    f"{rl.get('quantity_received')} x {_money(rl.get('unit_cost'))} "
                    f"is {_money(_f(rl.get('quantity_received')) * _f(rl.get('unit_cost')))}"
                    f" — unit cost {copy_cost} makes the line match the invoice"
                )
                if billed is not None
                else (
                    f"the invoice prices '{desc}' at {copy_cost} — "
                    f"Loaded read {ln.get('unit_cost')}"
                )
            ),
            apply={"unit_cost": copy_cost},
            fixes=fixes.get("unit_cost"),
            confidence=rl.get("fixes_confidence") if "unit_cost" in fixes else None,
        )
    if "total_cost" in fixes and _differ(
        ln.get("total_cost"), rl.get("total_cost"), _MONEY_TOL
    ):
        _sugg(
            suggestions,
            "line_value",
            field="total_cost",
            line_id=lid,
            current=ln.get("total_cost"),
            proposed=rl.get("total_cost"),
            explanation=rl.get("total_cost_why")
            or f"the invoice's line total for '{desc}' is {rl.get('total_cost')}",
            apply={"total_cost": rl.get("total_cost")},
            fixes=fixes.get("total_cost"),
            confidence=rl.get("fixes_confidence"),
        )
    _unit_suggestion(suggestions, rl, ln, lid, desc)
    if rl.get("linked_item_id") and rl["linked_item_id"] != ln.get("linked_item_id"):
        # Both halves matter. Unlinked → the plain "link this item". LINKED to
        # something else → a real disagreement: the draft opens on Loaded's own
        # code match (start_from_screen), so this is the only place a
        # wrong code match can be challenged.
        linked_to = ln.get("item_name") if ln.get("linked_item_id") else None
        _sugg(
            suggestions,
            "line_value",
            field="linked_item_id",
            line_id=lid,
            current=linked_to,
            proposed=rl.get("item_name") or rl.get("linked_item_id"),
            explanation=(
                rl.get("item_why")
                or (
                    f"'{desc}' matches stock item '{rl.get('item_name')}' "
                    f"({rl.get('matched_by')})"
                )
            )
            + (f" — Loaded has '{linked_to}'" if linked_to else ""),
            # The item only: its brand is the invoice's, a decision of its own
            # (_brand_suggestion) — the item's stored brand never rides in.
            apply={
                "linked_item_id": rl.get("linked_item_id"),
                "item_name": rl.get("item_name"),
            },
        )
    _brand_suggestion(suggestions, rl, ln, lid, desc)
    if rl.get("sale_tax_rate_set") and _differ(
        ln.get("sale_tax_rate"), rl.get("sale_tax_rate"), 0.0001
    ):
        # The invoice says this line carries no GST (replica_finish.settle_tax_rates).
        _sugg(
            suggestions,
            "line_value",
            field="sale_tax_rate",
            line_id=lid,
            current=ln.get("sale_tax_rate"),
            proposed=rl.get("sale_tax_rate"),
            explanation=rl.get("sale_tax_rate_why")
            or f"the invoice taxes '{desc}' at {rl.get('sale_tax_rate')}",
            apply={
                "sale_tax_rate": rl.get("sale_tax_rate"),
                "tax_amount": rl.get("tax_amount"),
            },
        )
    elif rl.get("sale_tax_rate") is not None and ln.get("sale_tax_rate") is None:
        _sugg(
            suggestions,
            "line_value",
            field="sale_tax_rate",
            line_id=lid,
            current=None,
            proposed=rl.get("sale_tax_rate"),
            explanation=f"'{desc}' has no tax rate in Loaded — the catalogue says "
            f"{rl.get('sale_tax_rate')}",
            apply={
                "sale_tax_rate": rl.get("sale_tax_rate"),
                "tax_amount": rl.get("tax_amount"),
            },
        )
    if not ln.get("linked_item_id"):
        _create_item_suggestion(suggestions, rl, lid, desc)


def _unit_suggestion(
    suggestions: list[dict], rl: dict, ln: dict | None, lid: str, desc: str
) -> None:
    """The line's unit, when the Replica's differs from Loaded's: a unit
    Loaded has (a swap), or one it doesn't yet (create it). ``ln`` None is a
    line still to be added — its unit is a decision of its own only when the
    Replica guessed it (a sure unit rides in with the line)."""
    if rl.get("unit_create_name"):
        _sugg(
            suggestions,
            "create_unit",
            field="unit",
            line_id=lid,
            current=(ln or {}).get("unit"),
            proposed=rl.get("unit_create_name"),
            explanation=(
                f"the invoice's delivered unit '{rl.get('unit_create_name')}' "
                "isn't in Loaded — create it"
            ),
            payload={"unit_name": rl.get("unit_create_name")},
            gate=_VA.AUTO_CREATE_UNITS,
        )
        return
    if not rl.get("linked_unit_id"):
        return
    if ln is None and not rl.get("unit_guess"):
        return
    if ln is not None and rl["linked_unit_id"] == ln.get("linked_unit_id"):
        return
    _sugg(
        suggestions,
        "line_value",
        field="unit",
        line_id=lid,
        current=(ln or {}).get("unit"),
        proposed=rl.get("unit"),
        explanation=rl.get("unit_why")
        or (
            f"the invoice's delivered unit for '{desc}' is {rl.get('unit')} — "
            f"Loaded has {(ln or {}).get('unit') or 'none'}"
        ),
        apply={
            "unit": rl.get("unit"),
            "linked_unit_id": rl.get("linked_unit_id"),
            "unit_ratio": rl.get("unit_ratio"),
        },
        sure=not rl.get("unit_guess"),
        confidence=(rl.get("unit_resolved") or {}).get("confidence")
        if rl.get("unit_by") == "resolver"
        else None,
    )


def _create_item_suggestion(
    suggestions: list[dict], rl: dict, lid: str, desc: str
) -> None:
    """The invoice's product isn't in the catalogue at all: create it under
    the Replica's proposed name and stock group. Both halves are required —
    Loaded's create needs a stock group, and a name invented without one is
    not actionable."""
    if rl.get("linked_item_id") or not (
        rl.get("suggested_name") and rl.get("suggested_group_id")
    ):
        return
    _sugg(
        suggestions,
        "create_item",
        field="linked_item_id",
        line_id=lid,
        current=None,
        proposed=rl.get("suggested_name"),
        explanation=(
            f"'{desc}' isn't in the Loaded catalogue — create it as "
            f"'{rl.get('suggested_name')}'"
        ),
        payload={
            "name": rl.get("suggested_name"),
            "group_id": rl.get("suggested_group_id"),
        },
        gate=_VA.AUTO_CREATE_ITEMS,
    )


# Header values worth suggesting. subtotal is deliberately ABSENT: it is
# derived from the lines, so the receive always writes the derived value
# (receive_request_from_doc) and a stored/suggested subtotal would only
# drift from edits. So is the discount: the receive never sends Loaded one,
# so accepting it only took it off the card's total — Fresh Connection
# 30049130 (14 Oct 2026) read $41 short after "apply all". A printed discount
# comes off the line costs instead (replica_finish.read_printed_prices).
_HEADER_VALUE_FIELDS = (
    ("tax_amount", "tax"),
    ("total", "total"),
)


#: Loaded's unit kinds, in words for a blocker.
_KIND_WORDS = {"Weight": "a weight", "Volume": "a volume", "Count": "a count"}


def unit_kind_issues(
    data: dict,
    suggestions: list[dict],
    catalogue: list[dict] | None,
    units: list[dict] | None,
    issues: list[dict],
) -> None:
    """A line received in a unit of a different kind (weight, volume or
    count) from what stocktakes count its product in. Loaded can't convert
    one kind into another, so the stock count would be wrong; the owner
    (17 Oct 2026) gave it a switch: receive_unit_kind_mismatch. Checked on
    the units the suggestions leave. In place."""
    by_item = {str(i.get("id")): i for i in catalogue or [] if isinstance(i, dict)}
    by_unit = {str(u.get("id")): u for u in units or [] if isinstance(u, dict)}
    post = {
        str(ln.get("id")): ln
        for ln in _all_applied(data, suggestions).get("lines") or []
    }
    for line in data.get("lines") or []:
        ln = post.get(str(line.get("id"))) or {}
        if not ln or ln.get("struck"):
            continue
        item = by_item.get(str(ln.get("linked_item_id")))
        unit = by_unit.get(str(ln.get("linked_unit_id")))
        counting = by_unit.get(str((item or {}).get("countingUnitId")))
        got = (unit or {}).get("stockUnitType")
        want = (counting or {}).get("stockUnitType")
        if not got or not want or got == want:
            continue
        desc = ln.get("item_name") or ln.get("description") or ln.get("code") or "line"
        issues.append(
            {
                "id": f"unit_kind_mismatch:{ln['id']}",
                "code": "unit_kind_mismatch",
                "blocking": True,
                "line_id": ln["id"],
                "gate": _VA.RECEIVE_UNIT_KIND_MISMATCH,
                "message": (
                    f"'{desc}' is received in '{unit.get('name')}' "
                    f"({_KIND_WORDS.get(got, got)}) but stocktakes count it in "
                    f"'{counting.get('name')}' ({_KIND_WORDS.get(want, want)})"
                ),
            }
        )


def brand_issues(data: dict, issues: list[dict]) -> None:
    """A brand Loaded names but has no record for STOPS the receive.

    `do_receive` refuses such a line — Loaded won't take it — but nothing said
    so before the attempt: there was a create_brand suggestion and no issue, so
    `confidence` read "ready", autopilot called do_receive and collected a 400,
    and the card showed the brand in three places while its "Blocked from auto
    receive" list stayed empty. Bidfood 109944512 sat unreceivable on exactly
    this. Its fix is the invoice's answer for the brand (``link_fixes``): a
    brand to create, the record Loaded has, or no brand at all.
    """
    for ln in data.get("lines") or []:
        if not isinstance(ln, dict) or ln.get("struck"):
            continue
        brand = str(ln.get("brand") or "").strip()
        if not brand or ln.get("linked_brand_id"):
            continue
        lid = str(ln.get("id"))
        name = ln.get("item_name") or ln.get("description") or ln.get("code") or "line"
        issues.append(
            {
                "id": f"brand_unknown:{lid}",
                "code": "brand_unknown",
                "blocking": True,
                "line_id": lid,
                "message": (
                    f"brand '{brand}' on '{name}' isn't in Loaded — Loaded won't "
                    "receive a line naming a brand it doesn't have"
                ),
                "data": {"brand_name": brand},
                # Linking any brand record settles it — a brand a person made
                # or picked included (the static map only reaches the
                # Replica's own blockers, and this one is raised here).
                "clears_when": {
                    "scope": "line",
                    "line_id": lid,
                    "field": "linked_brand_id",
                    "op": "not_null",
                },
            }
        )


def pair_invoice_lines(
    data: dict, replica: dict, *, db=None, pairing_ask=None
) -> tuple[dict[str, str], list[dict], list[dict], list[dict], list[dict]]:
    """Pair the Replica's lines to Loaded's, with the model arbitrating the
    ambiguous tail. Returns ``(pairs, ambiguous, unpaired_replica,
    unpaired_loaded, issues)``.

    The arbitration writes nothing — a decisive pairing only lets the normal
    comparison run on those lines; a line it can't pair is added from the
    copy (never a blocker: Autopilot decides). ``invoice_arbiter`` enforces a
    proper matching: a half-right pairing is worse than none."""
    issues: list[dict] = []
    doc_lines = data.get("lines") or []
    rep_lines = replica.get("lines") or []
    pairs, ambiguous, unpaired_rep, unpaired_loaded = pair_lines(rep_lines, doc_lines)

    if ambiguous and (pairing_ask is not None or db is not None):
        from app.services import invoice_arbiter

        claimed = set(pairs.values())
        candidates = [
            ln for ln in doc_lines if str(ln.get("id")) not in claimed and ln.get("id")
        ]
        slim = lambda ln: {  # noqa: E731 — tiny local shaper
            "id": str(ln.get("id")),
            "description": ln.get("description"),
            "code": ln.get("code"),
            "quantity": ln.get("quantity_received"),
            "unit_price": ln.get("unit_cost"),
            "line_total": ln.get("total_cost"),
        }
        verdict = invoice_arbiter.arbitrate_pairing(
            [slim(rl) for rl in ambiguous],
            [slim(ln) for ln in candidates],
            context_lines=[{"copy": rid, "loaded": lid} for rid, lid in pairs.items()],
            ask_llm=pairing_ask,
            db=db,
        )
        if verdict:
            arbitrated = dict(verdict["pairs"])
            lines_by_id = {str(ln.get("id")) for ln in doc_lines if ln.get("id")}
            still_ambiguous = []
            for rl in ambiguous:
                lid = arbitrated.get(str(rl.get("id")))
                if lid and lid in lines_by_id:
                    pairs[str(rl.get("id"))] = lid
                else:
                    still_ambiguous.append(rl)
            ambiguous = still_ambiguous
            claimed = set(pairs.values())
            unpaired_loaded = [
                ln for ln in unpaired_loaded if str(ln.get("id")) not in claimed
            ]
            issues.append(
                {
                    "id": "pairing_arbitrated",
                    "code": "pairing_arbitrated",
                    "blocking": False,
                    "line_id": None,
                    "message": (
                        "ambiguous lines paired by Norm — "
                        + (verdict.get("why") or "matched on the line details")
                    ),
                }
            )

    # Still contested: the copy has more of this product than Loaded's draft
    # does, so the line is added from the copy. It used to block ("reconcile
    # these lines by hand") and Autopilot could never get past it — five Alsco
    # invoices (16 Oct 2026) whose drafts Loaded had garbled. Loaded's own
    # leftover lines are struck as usual.
    unpaired_rep = unpaired_rep + ambiguous
    return pairs, [], unpaired_rep, unpaired_loaded, issues


#: Replica-only bookkeeping that must not ride into the working line when a
#: line is added.
_REPLICA_ONLY_KEYS = frozenset(
    {
        "matched_by",
        "fixes",
        "item_why",
        "unit_why",
        "unit_by",
        "unit_guess",
        "unit_read",
        "unit_cost_set",
        "quantity_received_set",
        "total_cost_set",
        "unit_cost_why",
        "quantity_received_why",
        "total_cost_why",
        "brand_create_name",
        "brand_read",
        "fixes_confidence",
        "answer_missing",
    }
)


def build_suggestions(
    data: dict, replica: dict, *, db=None, pairing_ask=None, finish=None
) -> tuple[list[dict], list[dict], dict[str, str]]:
    """The Replica compared with Loaded: one suggestion per difference.

    ``data`` holds the working values (Loaded's draft); ``replica`` is what we
    think the invoice says. ``finish(pairs)`` — ``replica_finish`` — runs
    between pairing and comparing, so every rule's answer is IN the replica
    before anything is compared. Returns ``(suggestions, issues, pairs)``; the
    issues are the pairing's and the lines Loaded bills that the invoice
    doesn't (the replica's own are merged by the caller).
    """
    suggestions: list[dict] = []
    pairs, _ambiguous, unpaired_rep, unpaired_loaded, issues = pair_invoice_lines(
        data, replica, db=db, pairing_ask=pairing_ask
    )
    if finish is not None:
        finish(pairs)
    doc_lines = data.get("lines") or []
    lines_by_id = {str(ln.get("id")): ln for ln in doc_lines if ln.get("id")}

    for rl in replica.get("lines") or []:
        lid = pairs.get(str(rl.get("id")))
        if lid and lid in lines_by_id:
            _line_suggestions(suggestions, rl, lines_by_id[lid], lid)

    for rl in unpaired_rep:
        _add_line_suggestions(suggestions, rl)

    for ln in unpaired_loaded:
        lid = str(ln.get("id"))
        total = _f(ln.get("total_cost")) or 0.0
        zero = abs(total) <= _MONEY_TOL
        _sugg(
            suggestions,
            "strike",
            line_id=lid,
            current=ln.get("description"),
            explanation=(
                f"Loaded's draft carries '{ln.get('description')}' but the "
                "invoice doesn't bill it"
                + (" — a $0 leftover; strike it" if zero else " — strike it")
            ),
            apply={"struck": True},
            # A $0 leftover goes on its own; a line with money on it is a
            # judgement the venue's switch must allow.
            gate=None if zero else _VA.AUTO_STRIKE_PHANTOM_LINES,
        )
        if not zero:
            issues.append(
                {
                    "id": f"loaded_line_not_on_copy:{lid}",
                    "code": "loaded_line_not_on_copy",
                    "blocking": True,
                    "line_id": lid,
                    "message": (
                        f"Loaded bills '{ln.get('description')}' for "
                        f"{ln.get('total_cost')} but the invoice doesn't show it"
                    ),
                    "clears_when": {
                        "scope": "line",
                        "line_id": lid,
                        "field": "struck",
                        "op": "truthy",
                    },
                }
            )

    paired = {str(w) for w in pairs.values()}
    for ln in doc_lines:
        if isinstance(ln, dict) and str(ln.get("id")) not in paired:
            _unpaired_brand_suggestion(suggestions, ln)
    _header_suggestions(data, replica, suggestions)
    return suggestions, issues, pairs


def _create_brand_suggestion(
    suggestions: list[dict], lid: str, name: str, *, current, explanation: str
) -> None:
    _sugg(
        suggestions,
        "create_brand",
        field="linked_brand_id",
        line_id=lid,
        current=current,
        proposed=name,
        explanation=explanation,
        payload={"brand_name": name},
        gate=_VA.AUTO_CREATE_BRANDS,
    )


def _brand_suggestion(
    suggestions: list[dict], rl: dict, ln: dict, lid: str, desc: str
) -> None:
    """The line's brand, when the invoice's differs from Loaded's (owner,
    19 Oct 2026: brands come from the invoice, never from Loaded).

    - The invoice prints no brand and Loaded has one: remove it. Nothing is
      created, so no switch: Autopilot applies it.
    - The invoice prints a brand the venue has a record of: link it (no switch).
    - The invoice prints a brand the venue has no record of: create it, behind
      the "create brands" switch.

    Names are compared, not ids: a venue can hold two records of one name. A
    line the reading didn't look at (``brand_read`` False: a feed line, an
    older read) compares nothing — a brand Loaded names with no record is
    still created under its name, or Loaded won't receive the line."""
    if ln.get("struck"):
        return
    loaded = str(ln.get("brand") or "").strip()
    loaded_id = ln.get("linked_brand_id")
    if not rl.get("brand_read"):
        if loaded and not loaded_id:
            _create_brand_suggestion(
                suggestions,
                lid,
                loaded,
                current=None,
                explanation=(
                    f"'{loaded}' isn't a brand in Loaded — create it (Loaded "
                    "won't receive a line naming a brand it doesn't have)"
                ),
            )
        return
    printed = str(rl.get("brand_create_name") or rl.get("brand") or "").strip()
    if not printed:
        if loaded or loaded_id:
            _sugg(
                suggestions,
                "line_value",
                field="linked_brand_id",
                line_id=lid,
                current=loaded or None,
                proposed=None,
                explanation=f"the invoice prints no brand for '{desc}' — "
                + (
                    f"remove Loaded's '{loaded}'"
                    if loaded
                    else "remove the brand Loaded links"
                ),
                apply={"brand": None, "linked_brand_id": None, "brand_set": True},
            )
        return
    if loaded_id and _norm(loaded) == _norm(printed):
        return  # the same brand, perhaps the venue's other record of that name
    rec_id = rl.get("linked_brand_id")
    if rec_id:
        if rec_id == loaded_id:
            return
        _sugg(
            suggestions,
            "line_value",
            field="linked_brand_id",
            line_id=lid,
            current=loaded or None,
            proposed=rl.get("brand"),
            explanation=f"the invoice prints the brand '{printed}' for '{desc}'"
            + (f" — Loaded has '{loaded}'" if loaded else ""),
            apply={
                "brand": rl.get("brand"),
                "linked_brand_id": rec_id,
                "brand_set": True,
            },
        )
        return
    _create_brand_suggestion(
        suggestions,
        lid,
        printed,
        current=loaded or None,
        explanation=(
            f"the invoice prints the brand '{printed}' for '{desc}' and Loaded "
            "has no brand of that name — create it"
            + (f" (Loaded has '{loaded}')" if loaded else "")
        ),
    )


def _unpaired_brand_suggestion(suggestions: list[dict], ln: dict) -> None:
    """A Loaded line the invoice doesn't bill, naming a brand Loaded has no
    record for. The invoice prints no brand for it, so: remove it. Whether or
    not the line is struck, Loaded must be able to take the draft (do_receive
    refuses a brand it has no record of: Bidfood 109944512)."""
    if ln.get("struck"):
        return
    loaded = str(ln.get("brand") or "").strip()
    if not loaded or ln.get("linked_brand_id"):
        return
    desc = ln.get("item_name") or ln.get("description") or ln.get("code") or "line"
    _sugg(
        suggestions,
        "line_value",
        field="linked_brand_id",
        line_id=str(ln.get("id")),
        current=loaded,
        proposed=None,
        explanation=(
            f"'{loaded}' on '{desc}' isn't a brand in Loaded, and the invoice "
            "doesn't bill this line — remove the brand"
        ),
        apply={"brand": None, "linked_brand_id": None, "brand_set": True},
    )


def _supplier_details_text(details: dict) -> str:
    """The details a new supplier takes, as one line for the card."""
    place = ", ".join(
        [*(details.get("address_lines") or []), details.get("city") or ""]
    ).strip(", ")
    if details.get("postcode"):
        place = f"{place} {details['postcode']}".strip()
    parts = [
        place,
        details.get("phone"),
        details.get("email"),
        details.get("contact_name"),
        details.get("account_number") and f"account {details['account_number']}",
    ]
    return " · ".join(p for p in parts if p)


def _add_line_suggestions(
    suggestions: list[dict],
    rl: dict,
    *,
    why: str | None = None,
    fixes: str | None = None,
) -> None:
    """A Replica line Loaded's draft lacks: add it, plus what it still needs
    (its unit, its stock item) as decisions of its own on that line. ``why``
    replaces the explanation when something other than the read found the
    line (the totals work-out, which also names it a ``fixes``)."""
    rid = str(rl.get("id"))
    desc = rl.get("description") or rl.get("code") or "line"
    payload = {k: v for k, v in rl.items() if k not in _REPLICA_ONLY_KEYS}
    # The brand rides in only as a record Loaded has (it won't take a line
    # naming one it doesn't); a brand to create is the line's own decision.
    if rl.get("linked_brand_id"):
        payload["brand_set"] = True
    else:
        payload["brand"], payload["linked_brand_id"] = None, None
    explanation = why or (
        f"the invoice bills '{rl.get('description')}' "
        f"(qty {rl.get('quantity_received')} at {rl.get('unit_cost')}) "
        "but Loaded's draft has no such line"
    )
    if rl.get("unit_guess"):
        # Adding a line is applied unattended; a guessed unit is not, so
        # it is the added line's own decision (below).
        payload.update(
            rl.get("unit_read")
            or {"unit": None, "linked_unit_id": None, "unit_ratio": None}
        )
    elif rl.get("unit_by") == "resolver" and rl.get("linked_unit_id"):
        # A copy-only line never met a Loaded unit, so a decisive resolver
        # pick rides in with it — accepting the line is accepting the unit
        # (Aitkens 173670 freight, 19 Aug 2026).
        explanation += f" — delivered as '{rl.get('unit')}' ({rl.get('unit_why')})"
    _sugg(
        suggestions,
        "add_line",
        line_id=rid,
        proposed=rl.get("description"),
        explanation=explanation,
        payload=payload,
        fixes=fixes,
    )
    _unit_suggestion(suggestions, rl, None, rid, desc)
    _create_item_suggestion(suggestions, rl, rid, desc)
    if rl.get("brand_create_name"):
        _create_brand_suggestion(
            suggestions,
            rid,
            str(rl["brand_create_name"]),
            current=None,
            explanation=(
                f"the invoice prints the brand '{rl['brand_create_name']}' for "
                f"'{desc}' and Loaded has no brand of that name — create it"
            ),
        )


def _due_date_suggestion(data: dict, replica: dict, suggestions: list[dict]) -> None:
    """The due date the invoice prints, where Loaded's draft lacks it or has
    another. Loaded's import misses some (Sydney Direct Fresh prints "Due
    Date: 29th October 2026"; Loaded had none, so the Xero bill went without —
    The Lott, 23 Oct 2026). Only a readable date, and never one before the
    invoice's own date (that is a misread, not a due date). A receiving rule
    that covers every invoice still wins (receiving_rules.apply)."""
    printed = _date_part(str(replica.get("due_at") or ""))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", printed):
        return
    current = _date_part(data.get("due_at")) or None
    if printed == current:
        return
    issued = _date_part(str(replica.get("issued_at") or data.get("issued_at") or ""))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", issued) and printed < issued:
        return
    when = datetime.date.fromisoformat(printed)
    _sugg(
        suggestions,
        "header_value",
        field="due_at",
        current=current,
        proposed=printed,
        explanation=(
            f"the invoice says it is due {when:%a %-d %b %Y} — Loaded has "
            + (current or "no due date")
        ),
        apply={"due_at": printed},
    )


def _header_suggestions(data: dict, replica: dict, suggestions: list[dict]) -> None:
    fixes = replica.get("fixes") or {}
    if replica.get("reference_number") and _norm(
        replica.get("reference_number")
    ) != _norm(data.get("reference_number")):
        _sugg(
            suggestions,
            "header_value",
            field="reference_number",
            current=data.get("reference_number"),
            proposed=replica.get("reference_number"),
            explanation=(
                f"the invoice's number is {replica.get('reference_number')} — "
                f"Loaded recorded {data.get('reference_number')}"
            ),
            apply={"reference_number": replica.get("reference_number")},
        )
    # Only a PARSEABLE invoice date is worth proposing: _iso_date keeps an
    # unparseable print verbatim (honest for the dojo diff), but a verbatim
    # string is never a value to write into a date field.
    rep_date = str(replica.get("issued_at") or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", _date_part(rep_date)) and _date_part(
        rep_date
    ) != _date_part(data.get("issued_at")):
        _sugg(
            suggestions,
            "header_value",
            field="issued_at",
            current=_date_part(data.get("issued_at")) or None,
            proposed=replica.get("issued_at"),
            explanation=(
                f"the invoice is dated {replica.get('issued_at')} — Loaded has "
                f"{_date_part(data.get('issued_at')) or 'no date'}"
            ),
            apply={"issued_at": replica.get("issued_at")},
        )
    _due_date_suggestion(data, replica, suggestions)
    money = list(_HEADER_VALUE_FIELDS)
    # A totals fix may correct the subtotal too — derived, so otherwise never
    # proposed, but the fix has to be visible to be accepted.
    for field in fixes:
        if field not in dict(money):
            money.append((field, field.replace("_", " ")))
    for field, label in money:
        if replica.get(field) is None:
            continue
        if _differ(data.get(field), replica.get(field), _MONEY_TOL):
            _sugg(
                suggestions,
                "header_value",
                field=field,
                current=data.get(field),
                proposed=replica.get(field),
                explanation=replica.get(f"{field}_why")
                or (
                    f"the invoice's {label} is {replica.get(field)} — Loaded has "
                    f"{data.get(field)}"
                ),
                apply={field: replica.get(field)},
                fixes=fixes.get(field),
                confidence=replica.get("fixes_confidence") if field in fixes else None,
            )
    if replica.get("supplier_create_name") and not replica.get("linked_supplier_id"):
        name = replica["supplier_create_name"]
        explanation = (
            replica.get("supplier_why")
            or f"no Loaded supplier is called '{name}' — create it"
        )
        payload = {"supplier_name": name}
        if replica.get("supplier_printed_name"):
            # The name the close records were checked against: autopilot's
            # duplicate guard asks with it as well as the trading name.
            payload["printed_name"] = replica["supplier_printed_name"]
        if replica.get("supplier_not_these"):
            # Close names Norm checked and ruled out: autopilot's duplicate
            # guard lets the create through past them.
            payload["not_these"] = replica["supplier_not_these"]
        details = replica.get("supplier_details") or {}
        if details:
            payload["details"] = details
            explanation += (
                ", with the details its invoice prints: "
                + _supplier_details_text(details)
            )
        _sugg(
            suggestions,
            "create_supplier",
            field="linked_supplier_id",
            current=data.get("supplier_name")
            if data.get("linked_supplier_id")
            else None,
            proposed=name,
            explanation=explanation,
            payload=payload,
            gate=_VA.AUTO_CREATE_SUPPLIERS,
        )
    elif replica.get("linked_supplier_id") and replica.get(
        "linked_supplier_id"
    ) != data.get("linked_supplier_id"):
        _sugg(
            suggestions,
            "supplier",
            field="linked_supplier_id",
            current=data.get("supplier_name"),
            proposed=replica.get("supplier_name"),
            explanation=replica.get("supplier_why")
            or (
                f"the invoice names '{replica.get('supplier_name')}' — Loaded has "
                f"'{data.get('supplier_name') or 'no supplier'}'"
            ),
            apply={
                "linked_supplier_id": replica.get("linked_supplier_id"),
                "supplier_name": replica.get("supplier_name"),
            },
            sure=not replica.get("supplier_guess"),
        )
    if replica.get("linked_purchase_order_id") and replica.get(
        "linked_purchase_order_id"
    ) != data.get("linked_purchase_order_id"):
        _sugg(
            suggestions,
            "link_po",
            field="linked_purchase_order_id",
            current=data.get("purchase_order_number"),
            proposed=replica.get("purchase_order_number"),
            explanation=replica.get("po_why")
            or (
                f"the invoice references order {replica.get('purchase_order_number')} "
                "and a matching open Loaded purchase order exists — link it"
            ),
            apply={
                "linked_purchase_order_id": replica.get("linked_purchase_order_id"),
                "purchase_order_number": replica.get("purchase_order_number"),
            },
            # A likely order is a guess: never linked unattended.
            sure=not replica.get("po_guess"),
        )

    # ---- The order's and the document's own stories ----
    rep_issues = {i.get("code"): i for i in replica.get("issues") or []}
    split = rep_issues.get("po_split_order")
    if split:
        d = split.get("data") or {}
        _sugg(
            suggestions,
            "split_reference",
            field="purchase_order_number",
            current=data.get("purchase_order_number"),
            proposed=d.get("order_number"),
            explanation=(
                f"order {d.get('order_number')} was split across deliveries — "
                f"{d.get('sibling_reference')} carries the link; keep the "
                "reference here without linking"
            ),
            apply={
                "purchase_order_number": d.get("order_number"),
                "split_po_id": d.get("po_id"),
                "split_sibling_invoice_id": d.get("sibling_invoice_id"),
            },
        )
    # No unlink for a supplier mismatch. An order owned by another Loaded
    # supplier is usually a through-supplier arrangement (Soho ordered, Procure
    # delivers), so detaching a correct link was the wrong remedy for a
    # question that only deserved a warning. See po_supplier_mismatch in
    # invoice_replica.
    dup = rep_issues.get("duplicate_invoice")
    if dup:
        _delete_suggestion(
            suggestions,
            data,
            dup.get("message") or "duplicate — delete this draft",
            _VA.AUTO_DELETE_DUPLICATES,
            reason="duplicate",
            extra=dup.get("data") or {},
        )
    not_invoice = rep_issues.get("not_an_invoice")
    if not_invoice:
        doc_type = (not_invoice.get("data") or {}).get("document_type") or "document"
        _delete_suggestion(
            suggestions,
            data,
            f"{not_invoice.get('message')} — delete the draft",
            _VA.AUTO_DELETE_NON_INVOICES,
            reason="statement" if doc_type == "statement" else "not_an_invoice",
            summary=f"a {doc_type}, not an invoice — draft deleted",
        )


#: Why a draft was deleted, as the run's summary counts it.
DELETE_REASONS = ("duplicate", "statement", "not_an_invoice", "no_copy", "unreadable")


def _delete_suggestion(
    suggestions: list[dict],
    data: dict,
    explanation: str,
    gate: str,
    *,
    reason: str,
    extra: dict | None = None,
    summary: str | None = None,
) -> None:
    """The Replica's answer for a draft that shouldn't be received at all:
    delete it from Loaded (a duplicate, a statement or letter, no invoice
    attached, an unreadable one). Each answers to its own venue switch.
    ``reason`` is one of ``DELETE_REASONS`` — what the run's summary counts."""
    _sugg(
        suggestions,
        "delete_invoice",
        explanation=explanation,
        payload={
            "type": "delete_invoice",
            "invoice_id": data.get("invoice_id"),
            "reference": data.get("reference_number"),
            **(extra or {}),
            "reason": reason,
            "summary": summary or explanation,
        },
        gate=gate,
    )


# ---------------------------------------------------------------------------
# Issue post-processing
# ---------------------------------------------------------------------------

_LINE_ISSUE_CLEARS = {
    "item_unmatched": ("linked_item_id", "not_null"),
    "brand_unknown": ("linked_brand_id", "not_null"),
    # Give the credited line a quantity and the blocker stands down — the
    # remedy is the quantity field itself (one-button doctrine: a blocker
    # clears by actually fixing it, and editing IS the fix here).
    "credit_zero_quantity": ("quantity_received", "truthy"),
    # unit_missing / unit_unconfirmed deliberately have NO clears_when: a unit
    # value already sitting on Loaded's line is Loaded's OCR of the same paper
    # — the very thing we don't trust. Only an explicit confirm/dismiss
    # (recorded in suggestion_actions) clears them.
}

#: Which venue toggle would let autopilot get past this blocker on its own.
#: ONE mapping, stamped onto the issue as `gate`, so the card can say "this
#: needs a brand created, and auto-create brands is off" without keeping its
#: own copy of the rules to drift out of step with these.
#: An issue with no entry here needs no toggle: Norm's own answer settles it
#: (tests/test_every_blocker_has_a_fix.py lists each blocker's decision).
_ISSUE_GATES = {
    "item_unmatched": _VA.AUTO_CREATE_ITEMS,
    "unit_missing": _VA.AUTO_CREATE_UNITS,
    "unit_would_be_created": _VA.AUTO_CREATE_UNITS,
    "brand_unknown": _VA.AUTO_CREATE_BRANDS,
    "supplier_unresolved": _VA.AUTO_CREATE_SUPPLIERS,
    # A unit Loaded supplied but the copy never confirmed. name_the_unit_in_use
    # re-points unit_missing here too when the working line already has one.
    "unit_unconfirmed": _VA.RECEIVE_WITH_UNCONFIRMED_UNIT,
    "unit_kind_mismatch": _VA.RECEIVE_UNIT_KIND_MISMATCH,
    "po_missing": _VA.RECEIVE_WITHOUT_PO,
    "po_unresolved": _VA.RECEIVE_WITHOUT_PO,
    "loaded_line_not_on_copy": _VA.AUTO_STRIKE_PHANTOM_LINES,
    # The delete gates — autopilot's only destructive writes, three separate
    # toggles on purpose (different risk profiles; see venue_autopilot).
    "duplicate_invoice": _VA.AUTO_DELETE_DUPLICATES,
    "not_an_invoice": _VA.AUTO_DELETE_NON_INVOICES,
    "no_copy_attached": _VA.AUTO_DELETE_UNREADABLE,
    "copy_unreadable": _VA.AUTO_DELETE_UNREADABLE,
}


def totals_balance(data: dict) -> bool:
    """Do these working values come to the invoice's total, as Loaded adds
    them up (within its rounding)? The ONE totals check: the totals blocker
    clears on it, so nothing is received — by a person's Accept, autopilot or
    a switch — that Loaded would show (or refuse) as not adding up."""
    _, _, derived, declared = _totals_view(data)
    return declared is None or abs(derived - declared) <= LOADED_ROUNDING + 1e-9


def _all_applied(data: dict, suggestions: list[dict]) -> dict:
    """The working values once every suggestion is accepted — the card's
    "accept all", on a copy."""
    post = copy.deepcopy({k: v for k, v in data.items() if k != "loaded_snapshot"})
    for s in suggestions:
        try:
            apply_suggestion(post, s)
        except Exception as exc:  # noqa: BLE001 — a check, never an error
            logger.info("accept-all check skipped %s: %s", s.get("id"), exc)
    return post


def _moves_money(data: dict, s: dict) -> bool:
    """Does accepting this suggestion alone change how far the invoice is off
    its total? A cost, a quantity, a line, a rate or the total itself."""
    _, _, came_to, declared = _totals_view(data)
    _, _, after, after_total = _totals_view(_all_applied(data, [s]))
    return abs((after - (after_total or 0.0)) - (came_to - (declared or 0.0))) >= 0.005


def _work_out_the_lines(
    data: dict,
    suggestions: list[dict],
    issue_id: str,
    replica: dict,
    pairs: dict[str, str],
    ask,
    db,
    document=None,
    *,
    issues: list[dict] | None = None,
    resolve_lines=None,
    on_unresolved=None,
) -> None:
    """With every suggestion applied the invoice still doesn't come to its
    total, so Norm works out which lines are wrong
    (``invoice_arbiter.work_out_totals``): the model looks at the invoice
    itself (``document()``, the copy as a content block), sees each line as
    it would be received beside its printed reading, and names what's wrong.
    An answer counts only when the invoice then adds up, Loaded's way
    (``totals_balance``), with every other suggestion applied — then its
    changes are sure fixes. One that doesn't goes back with what it came to,
    for another try. Runs only here, so an invoice that adds up costs
    nothing extra.

    Beyond a line's figures the answer may add a line the invoice prints
    that no line covers, remove one it doesn't print, or keep one Norm was
    going to strike — so any gap visible on the copy can be closed (the
    owner, 17 Oct 2026). An added line is matched like any line the read
    found (``resolve_lines``: the Replica built for just those lines). When
    no answer adds up, ``on_unresolved`` is told, so the cause gets fixed."""
    if ask is None and db is None:
        return
    from app.services import invoice_arbiter

    post = _all_applied(data, suggestions)
    _, _, came_to, declared = _totals_view(post)
    printed = {wid: _line(replica, rid) or {} for rid, wid in pairs.items()}
    rows = [ln for ln in post.get("lines") or [] if isinstance(ln, dict)]
    live = [ln for ln in rows if not ln.get("struck")]
    # GST "charged" is the rate the invoice's lines already carry.
    gst = max((_f(ln.get("sale_tax_rate")) or 0.0 for ln in live), default=0.0)
    lines = []
    for ln in rows:
        lid = str(ln.get("id"))
        held = bool(ln.get("struck"))
        # A strike someone already decided stays decided; only the ones
        # Norm is suggesting are the model's to keep.
        if held and (_line(data, lid) or {}).get("struck"):
            continue
        rl = printed.get(lid) or _line(replica, lid) or {}
        lines.append(
            {
                "id": lid,
                "description": ln.get("description"),
                "code": ln.get("code"),
                # What the line is received against: a price put on a merged
                # line of Loaded's ('5x3 Welcome Mat\nLaundry Bag Stand…')
                # lands on that line's item, not the product its text names.
                "item": (ln.get("item_name") or rl.get("item_name"))
                if ln.get("linked_item_id")
                else None,
                "quantity": _f(ln.get("quantity_received")),
                "unit_price": _f(ln.get("unit_cost")),
                "gst_rate": _f(ln.get("sale_tax_rate")),
                "receive": not held,
                "printed": {
                    "quantity": rl.get("quantity_received"),
                    "unit_price": rl.get("unit_cost"),
                    "line_total": rl.get("total_cost"),
                }
                if rl
                else None,
            }
        )
    added_by_read = {
        str(s.get("line_id")) for s in suggestions if s.get("kind") == "add_line"
    }

    def rate_for(gst_word: object) -> float | None:
        return {"charged": gst or None, "free": 0.0}.get(gst_word)  # type: ignore[arg-type]

    def why_for(answer: dict) -> str:
        return (
            f"with every other suggestion applied the invoice comes to "
            f"{came_to:.2f}, not its total {declared:.2f} — {answer.get('why')}"
        )

    def with_answer(answer: dict) -> list[dict]:
        trial = copy.deepcopy(suggestions)
        why = why_for(answer)
        # A line Norm's own read added that the invoice doesn't print goes
        # with everything hanging off it; a line of Loaded's draft is struck.
        dropped = {lid for lid in answer.get("remove") or [] if lid in added_by_read}
        trial[:] = [s for s in trial if str(s.get("line_id")) not in dropped]
        for lid in answer.get("remove") or []:
            if lid in dropped:
                continue
            ln = _line(post, lid) or {}
            zero = abs(_f(ln.get("total_cost")) or 0.0) <= _MONEY_TOL
            _sugg(
                trial,
                "strike",
                line_id=lid,
                current=ln.get("description"),
                explanation=why,
                apply={"struck": True},
                gate=None if zero else _VA.AUTO_STRIKE_PHANTOM_LINES,
                fixes=issue_id,
            )
        kept = set(answer.get("keep") or [])
        trial[:] = [
            s
            for s in trial
            if not (s.get("kind") == "strike" and str(s.get("line_id")) in kept)
        ]
        for i, new in enumerate(answer.get("add") or []):
            rid = f"totals-{i}"
            qty, price = new["quantity"], new["unit_price"]
            rate = rate_for(new.get("gst"))
            _sugg(
                trial,
                "add_line",
                line_id=rid,
                proposed=new["description"],
                explanation=why,
                payload={
                    "id": rid,
                    "code": new.get("code"),
                    "description": new["description"],
                    "quantity_received": qty,
                    "unit_cost": price,
                    "total_cost": round(qty * price, 4),
                    "sale_tax_rate": gst if rate is None else rate,
                    "linked_item_id": None,
                },
                fixes=issue_id,
            )
        by_id = {str(s.get("id")): s for s in trial}
        changes = [
            (str(c["line_id"]), field, value)
            for c in answer.get("lines") or []
            for field, value in (
                ("quantity_received", c.get("quantity")),
                ("unit_cost", c.get("unit_price")),
                ("sale_tax_rate", rate_for(c.get("gst"))),
            )
            if value is not None and str(c["line_id"]) not in dropped
        ]
        if answer.get("total") is not None:
            changes.append((None, "total", answer["total"]))
        for lid, field, value in changes:
            if lid is None:
                sid, before = "header_value:total", data.get("total")
            else:
                sid = f"line_value:{field}:{lid}"
                before = (_line(post, lid) or {}).get(field)
            if not _differ(before, value, _QTY_TOL):
                continue
            s = by_id.get(sid)
            if s is None:
                _sugg(
                    trial,
                    "header_value" if lid is None else "line_value",
                    field=field,
                    line_id=lid,
                    current=(data if lid is None else _line(data, lid) or {}).get(
                        field
                    ),
                    proposed=value,
                    explanation=why,
                    apply={field: value},
                    fixes=issue_id,
                )
            else:
                s.update(
                    proposed=value,
                    apply={field: value},
                    explanation=why,
                    sure=True,
                    fixes=issue_id,
                )
        return trial

    def check(answer: dict) -> float | None:
        after = _all_applied(data, with_answer(answer))
        return None if totals_balance(after) else _totals_view(after)[2]

    try:
        block = document() if document else None
    except Exception as exc:  # noqa: BLE001 — the figures alone, then
        logger.info("invoice copy unavailable for the totals: %s", exc)
        block = None
    tried: list[dict] = []
    answer = invoice_arbiter.work_out_totals(
        lines,
        {
            "subtotal": replica.get("subtotal"),
            "tax": replica.get("tax_amount"),
            "total": declared,
            "came_to": came_to,
        },
        check,
        document=block,
        ask_llm=ask,
        db=db,
        tried=tried,
    )
    if not answer:
        logger.warning(
            "totals don't balance and Norm couldn't work out the lines on %s",
            data.get("invoice_id"),
        )
        if on_unresolved is not None:
            try:
                on_unresolved(
                    {
                        "invoice_id": data.get("invoice_id"),
                        "reference_number": data.get("reference_number"),
                        "supplier_name": data.get("supplier_name"),
                        "came_to": came_to,
                        "total": declared,
                        "tried": tried,
                    }
                )
            except Exception:  # noqa: BLE001 — an alert never fails a review
                logger.exception("totals alert failed for %s", data.get("invoice_id"))
        return
    suggestions[:] = with_answer(answer)
    if issues is not None:
        # What the answer settled stops standing in the way: a line kept is
        # on the invoice after all; a line of Norm's that went is gone.
        gone = set(answer.get("keep") or []) | (
            set(answer.get("remove") or []) & added_by_read
        )
        issues[:] = [
            i
            for i in issues
            if not (
                str(i.get("line_id")) in gone
                and (
                    i.get("code") == "loaded_line_not_on_copy"
                    or str(i.get("line_id")) in added_by_read
                )
            )
        ]
    if answer.get("add"):
        _match_added_lines(
            answer, suggestions, issues, issue_id, why_for(answer), resolve_lines
        )


def _match_added_lines(
    answer: dict,
    suggestions: list[dict],
    issues: list[dict] | None,
    issue_id: str,
    why: str,
    resolve_lines,
) -> None:
    """The lines the totals answer adds, matched like any line the read
    found: ``resolve_lines`` builds the Replica for just those lines, and
    each comes back with its stock item, unit and GST — or a create — in
    place of the bare figures the check ran on. Without a match the line
    stands blocked rather than going to Loaded unlinked: when the match
    itself didn't come through, it says so and the next review asks again."""
    added = answer.get("add") or []
    resolved: dict = {}
    if resolve_lines is not None:
        try:
            resolved = (
                resolve_lines(
                    [
                        {
                            "code": new.get("code"),
                            "description": new["description"],
                            "quantity": new["quantity"],
                            "unit_price_ex_tax": new["unit_price"],
                            "line_total_ex_tax": round(
                                new["quantity"] * new["unit_price"], 4
                            ),
                            "gst": new.get("gst"),
                        }
                        for new in added
                    ]
                )
                or {}
            )
        except Exception as exc:  # noqa: BLE001 — the bare line stays, blocked
            logger.info("matching the added lines failed: %s", exc)
            resolved = {}
    rep_lines = resolved.get("lines") or []
    for i, _new in enumerate(added):
        rid = f"totals-{i}"
        rl = rep_lines[i] if i < len(rep_lines) else None
        if rl is not None:
            suggestions[:] = [s for s in suggestions if s.get("line_id") != rid]
            _add_line_suggestions(
                suggestions, {**rl, "id": rid}, why=why, fixes=issue_id
            )
        if issues is None:
            continue
        line_issues = [
            {**ri, "line_id": rid, "id": f"{ri.get('code')}:{rid}"}
            for ri in resolved.get("issues") or []
            if ri.get("line_id") == f"rep-{i}"
        ]
        if rl is None:
            # Nothing came back for the line: Norm's answer didn't come
            # through (owner, 19 Oct 2026), not "no item matches".
            line_issues = [
                ri
                for ri in line_issues
                if ri.get("code") not in ("item_unmatched", "item_answer_missing")
            ]
            line_issues.append(
                {
                    "id": f"item_answer_missing:{rid}",
                    "code": "item_answer_missing",
                    "blocking": True,
                    "line_id": rid,
                    "message": (
                        f"'{_new['description']}': Norm's answer for the stock "
                        "item didn't come through — the next review asks again"
                    ),
                    "data": {"retry": True},
                }
            )
        elif not rl.get("linked_item_id"):
            if not any(
                ri.get("code") in ("item_unmatched", "item_answer_missing")
                for ri in line_issues
            ):
                line_issues.append(
                    {
                        "id": f"item_unmatched:{rid}",
                        "code": "item_unmatched",
                        "blocking": True,
                        "line_id": rid,
                        "message": (
                            f"'{_new['description']}' isn't linked to a stock item"
                        ),
                    }
                )
        issues.extend(_finalise_issues(line_issues, [], {}, require_valid_po=False))


def balance_after_suggestions(
    data: dict,
    suggestions: list[dict],
    issues: list[dict],
    *,
    replica: dict | None = None,
    pairs: dict[str, str] | None = None,
    ask=None,
    db=None,
    document=None,
    resolve_lines=None,
    on_unresolved=None,
) -> None:
    """The totals check, on the values the suggestions leave — not the copy's
    own printed figures (the user, 14 Oct 2026: "the goal is autoreceive so we
    need to make sure that after all suggestions are applied the totals
    always match, that should be our check"). In place.

    - Accepting every suggestion must come to the total. If it wouldn't,
      Norm works out which lines are wrong (``_work_out_the_lines``) — never
      an adjustment line, never the difference spread over every price (the
      owner, 16 Oct 2026: "it should work out the lines"; the spread cut 64
      Applegum invoices' costs to force a GST mismatch). Loaded's draft is
      never the fallback, however well it adds up as it stands: of four
      drafts that "added up" (Oct 2026), three had no total at all and the
      fourth hit its total with three prices wrong and a line missing — the
      owner, 17 Oct 2026: "the point of the replica is to replace what
      loaded is doing".
    - The totals blocker stands whenever the WORKING values don't come to the
      total, and clears only when they do (``clears_when`` ``balances``) —
      however they got there. Its fix is every suggestion that moves the
      money. Autopilot applies only sure suggestions (and guesses whose switch
      is on), so a check on "everything applied" alone would let it receive
      an invoice that doesn't add up; and Fresh Connection 30049130 read
      "Ready to receive" at $138.32 on a $179.40 invoice because accepting the
      fixes used to be enough to clear the blocker. It is raised, too, when
      the working values add up but Norm's own figures don't — the receive
      would apply those figures."""
    if any(s.get("kind") == "delete_invoice" for s in suggestions):
        return  # nothing will be received
    existing = next((i for i in issues if i.get("code") == TOTALS_ISSUE), None)
    issue_id = (existing or {}).get("id") or TOTALS_ISSUE

    if not totals_balance(_all_applied(data, suggestions)):
        _work_out_the_lines(
            data,
            suggestions,
            issue_id,
            replica or {},
            pairs or {},
            ask,
            db,
            document,
            issues=issues,
            resolve_lines=resolve_lines,
            on_unresolved=on_unresolved,
        )

    applied = _all_applied(data, suggestions)
    if totals_balance(data) and totals_balance(applied):
        # It adds up as it stands and with every suggestion accepted:
        # nothing to stop the receive.
        if existing is not None:
            issues.remove(existing)
        for s in suggestions:
            if s.get("fixes") == issue_id:
                s["fixes"] = None
        return

    _, _, came_to, declared = _totals_view(
        data if not totals_balance(data) else applied
    )
    issue = existing or {"id": issue_id, "code": TOTALS_ISSUE, "line_id": None}
    # No switch: Loaded won't receive an invoice that doesn't add up, so
    # there is nothing for one to allow (the owner, 16 Oct 2026).
    issue.update(
        blocking=True,
        message=f"the invoice comes to {came_to:.2f}, not its total {declared:.2f}",
        clears_when={"scope": "header", "op": "balances"},
    )
    if existing is None:
        issues.append(issue)
    # Its fix: every suggestion that moves the money.
    for s in suggestions:
        if s.get("fixes") not in (None, issue_id):
            continue
        if s.get("fixes") is None and not _moves_money(data, s):
            continue
        s["fixes"] = issue_id


def _finalise_issues(
    replica_issues: list[dict],
    extra_issues: list[dict],
    pairs: dict[str, str],
    *,
    require_valid_po: bool = True,
) -> list[dict]:
    """Remap replica line-issues onto the paired working lines, attach
    clears_when predicates, and apply PO policy."""
    out: list[dict] = []
    for i in replica_issues:
        i = dict(i)
        lid = i.get("line_id")
        if lid and lid in pairs:
            i["line_id"] = pairs[lid]
            i["id"] = f"{i['code']}:{pairs[lid]}"
            # Norm's own line behind the blocker (_confirm_for names its unit).
            i["replica_line_id"] = lid
        clears = _LINE_ISSUE_CLEARS.get(i.get("code"))
        if clears and i.get("line_id"):
            i["clears_when"] = {
                "scope": "line",
                "line_id": i["line_id"],
                "field": clears[0],
                "op": clears[1],
            }
        if i.get("code") == "supplier_unresolved" and not (i.get("data") or {}).get(
            "draft_supplier"
        ):
            # A supplier on Loaded's draft that the copy contradicts is not an
            # answer, so "any supplier linked" can't clear it — its fix must
            # be accepted (a create, or the right record).
            i["clears_when"] = {
                "scope": "header",
                "field": "linked_supplier_id",
                "op": "not_null",
            }
        if i.get("code") == "po_split_order":
            # Its fix is the split_reference suggestion: keep the order's
            # NUMBER without taking its link, because Loaded's PO↔invoice is
            # 1:1 and the sibling delivery holds it. Accepting that sets
            # split_po_id, and the reconciliation then runs against the order.
            i["clears_when"] = {
                "scope": "header",
                "field": "split_po_id",
                "op": "not_null",
            }
        if i.get("code") in ("po_unresolved",) and not require_valid_po:
            i["blocking"] = False
        out.append(i)
    out.extend(extra_issues)
    for i in out:
        gate = _ISSUE_GATES.get(i.get("code"))
        if gate:
            i["gate"] = gate
    return out


# ---------------------------------------------------------------------------
# Blockers point at their fix
# ---------------------------------------------------------------------------
#
# A suggestion CHANGES A VALUE; a blocker STOPS A RECEIVE. Every blocker is
# cleared by accepting one or more suggestions (its `fix`) — the card shows
# those suggestions, never the blocker as a decision of its own, so one thing
# Norm noticed is one row with one Accept. Where Norm agrees with the value
# already there but can't be sure of it (a unit the invoice doesn't confirm,
# no order to link, totals the invoice prints wrongly), the fix is a
# `confirm` suggestion: accepting it records the call and changes nothing.

#: Blocker codes about a line's unit — one decision, whichever words raised it.
_UNIT_ISSUES = (
    "unit_missing",
    "unit_unconfirmed",
    "unit_unknown",
    "unit_would_be_created",
)

#: Kinds that write to Loaded, in the order they must run: the supplier
#: before anything that hangs off it, then the unit and the brand a new item
#: is created with, then the item.
LOADED_WRITE_ORDER = ("create_supplier", "create_unit", "create_brand", "create_item")

#: Blockers a venue switch waves through on its own: the switch IS the answer
#: ("receive without an order"), so nothing has to be applied for it to stand
#: down.
_SWITCH_SETTLES = (
    "po_missing",
    "po_unresolved",
    "unit_unconfirmed",
    "unit_kind_mismatch",
)
# Not the totals: the blocker clears only because the invoice balances — never
# by decree.

_DELETE_CODES = (
    "duplicate_invoice",
    "not_an_invoice",
    "no_copy_attached",
    "copy_unreadable",
)


def _answers(s: dict, issue: dict) -> bool:
    """Is suggestion ``s`` (part of) the fix for ``issue``?"""
    if s.get("fixes") and s.get("fixes") == issue.get("id"):
        return True
    code, kind, field = issue.get("code"), s.get("kind"), s.get("field")
    same_line = bool(issue.get("line_id")) and str(s.get("line_id")) == str(
        issue.get("line_id")
    )
    if code in _UNIT_ISSUES:
        return same_line and field == "unit" and kind in ("line_value", "create_unit")
    if code == "item_unmatched":
        return (
            same_line
            and field == "linked_item_id"
            and kind in ("line_value", "create_item")
        )
    if code == "brand_unknown":
        # The invoice's answer: create the brand, link the one Loaded has, or
        # remove it when the invoice prints none.
        return same_line and (
            kind == "create_brand"
            or (kind == "line_value" and field == "linked_brand_id")
        )
    if code == "loaded_line_not_on_copy":
        return same_line and kind == "strike"
    if code == "credit_zero_quantity":
        return same_line and field == "quantity_received"
    if code == "supplier_unresolved":
        return kind in ("supplier", "create_supplier")
    if code in ("po_missing", "po_unresolved"):
        return kind == "link_po"
    if code == "po_split_order":
        return kind == "split_reference"
    if code in _DELETE_CODES:
        return kind == "delete_invoice"
    return False


def _line(data: dict, line_id: object) -> dict | None:
    return next(
        (
            ln
            for ln in data.get("lines") or []
            if isinstance(ln, dict) and str(ln.get("id")) == str(line_id)
        ),
        None,
    )


def _confirm_for(data: dict, replica: dict | None, issue: dict) -> dict | None:
    """The fix where Norm agrees with what is already there: accepting it
    records the decision and changes nothing."""
    code = issue.get("code")
    explanation = None
    field = None
    proposed = None
    if code in ("unit_missing", "unit_unconfirmed"):
        ln = _line(data, issue.get("line_id"))
        if not ln or not ln.get("linked_unit_id"):
            return None
        # Only a unit Norm chose itself is kept: the Replica's line must hold
        # the unit the working line shows. A unit only Loaded holds is never
        # confirmed (owner, 18 Oct 2026: "i've been working hard to tell agents
        # not to rely on loaded at all").
        rid = issue.get("replica_line_id") or issue.get("line_id")
        rl = next(
            (
                r
                for r in (replica or {}).get("lines") or []
                if str(r.get("id")) == str(rid)
            ),
            None,
        )
        if not rl or rl.get("linked_unit_id") != ln.get("linked_unit_id"):
            return None
        desc = ln.get("item_name") or ln.get("description") or ln.get("code") or "line"
        field, proposed = "unit", ln.get("unit")
        unit = ln.get("unit")
        source = (issue.get("data") or {}).get("unit_chosen_by")
        if rl.get("unit_by") == "resolver":
            source = rl.get("unit_by")
        source = source or rl.get("unit_source")
        said = {
            "copy": f"the invoice says '{unit}'; keep it",
            "catalogue": f"Norm's catalogue says '{unit}'; keep it",
            "estimate": f"Norm works it out as '{unit}'; keep it",
            "resolver": f"Norm works it out as '{unit}'; keep it",
            "printed": f"the printed column says '{unit}'; keep it",
        }.get(source, f"Norm's answer is '{unit}'; keep it")
        explanation = f"the invoice doesn't confirm the unit for '{desc}' — {said}"
    elif code == "unit_kind_mismatch":
        ln = _line(data, issue.get("line_id"))
        if not ln:
            return None
        field, proposed = "unit", ln.get("unit")
        explanation = f"{issue.get('message')} — receive it anyway"
    elif code == "po_missing":
        field = "linked_purchase_order_id"
        explanation = (
            "no order is linked and the invoice references none that resolves — "
            "receive this delivery without an order"
        )
    elif code == "po_unresolved":
        field = "linked_purchase_order_id"
        message = str(issue.get("message") or "")
        explanation = (
            message
            if "receive without" in message
            else f"{message} — receive without it"
        )
    else:
        # Not the totals: "receive on the values as entered" was an Accept
        # that cleared the blocker while Loaded would refuse the receive. The
        # totals stand down only when they match (balance_after_suggestions).
        return None
    return {
        "id": f"confirm:{issue.get('id')}",
        "kind": "confirm",
        "field": field,
        "line_id": issue.get("line_id") if field == "unit" else None,
        "current": proposed,
        "proposed": proposed,
        "explanation": explanation,
        "sure": False,
    }


def link_fixes(
    data: dict, replica: dict | None, issues: list[dict], suggestions: list[dict]
) -> None:
    """Give every blocker ``fix`` — the ids of the suggestions that clear it —
    adding a ``confirm`` where Norm agrees with what is there. In place.

    A unit decision takes the blocker's switch: creating the unit the invoice
    shows needs "auto-create units", and using Norm's answer where the
    invoice's unit can't be read needs "receive with an unconfirmed unit".
    Norm's own answer for a line with no unit needs no switch: it is Norm's
    decision (owner, 19 Oct 2026, retiring "receive without a unit")."""
    for issue in issues:
        fix = [s for s in suggestions if _answers(s, issue)]
        if not fix:
            confirm = _confirm_for(data, replica, issue)
            if confirm is not None:
                suggestions.append(confirm)
                fix = [confirm]
        if issue.get("code") in _UNIT_ISSUES:
            if any(s.get("kind") == "create_unit" for s in fix):
                issue["gate"] = _VA.AUTO_CREATE_UNITS
            elif issue.get("code") in ("unit_missing", "unit_unknown"):
                # Norm's answer is the decision: no switch, so its fix applies
                # on Autopilot like any other sure answer.
                issue["gate"] = None
            for s in fix:
                if s.get("kind") in ("line_value", "confirm") and not s.get("gate"):
                    s["gate"] = issue.get("gate")
        elif issue.get("code") in _SWITCH_SETTLES:
            for s in fix:
                if s.get("kind") == "confirm":
                    s["gate"] = issue.get("gate")
        elif issue.get("code") == "brand_unknown" and not any(
            s.get("kind") == "create_brand" for s in fix
        ):
            # The invoice's answer is a brand Loaded has, or none: nothing is
            # created, so no switch (owner, 19 Oct 2026).
            issue["gate"] = None
        elif issue.get("code") == "supplier_unresolved" and not any(
            s.get("kind") == "create_supplier" for s in fix
        ):
            # Its answer is an existing record that may be the same business —
            # a person's call. "Auto-create suppliers" can't get past it:
            # autopilot never creates a supplier something plausibly matches.
            issue["gate"] = None
        issue["fix"] = [s["id"] for s in fix]


#: Blockers the next review clears by trying again: a service was down, or
#: Norm's answer didn't come through after a retry (owner, 19 Oct 2026: never
#: a guess in its place). There is nothing for Accept to do on them.
RETRY_NEXT_REVIEW = frozenset(
    {"copy_not_checked", "unit_answer_missing", "item_answer_missing"}
)


#: How soon a card whose review stopped on one of those reviews again when it
#: is opened: soon enough that the answer is asked for again, but not on every
#: open while a service is down (the 14 Oct 2026 credit outage lasted hours).
RETRY_AFTER_MINUTES = 10


def review_is_current(data: dict | None, *, now=None) -> bool:
    """The draft's cached review can be shown as it is: it is a current
    (DOC_SCHEMA) review, and it isn't waiting to try again. A blocker the next
    review retries (RETRY_NEXT_REVIEW) only stands down by reviewing again, so
    a review carrying one counts as current for RETRY_AFTER_MINUTES only. The
    web card, the Claude card and the draft open all ask this."""
    data = data if isinstance(data, dict) else {}
    if data.get("doc_schema") != DOC_SCHEMA or not data.get("reviewed_at"):
        return False
    # One a person has answered (a unit or item picked by hand) has nothing
    # left to ask, so it doesn't send every open back through the review.
    if not any(
        isinstance(i, dict)
        and i.get("blocking")
        and i.get("code") in RETRY_NEXT_REVIEW
        and not is_settled(data, i)
        for i in data.get("issues") or []
    ):
        return True
    try:
        at = datetime.datetime.fromisoformat(str(data["reviewed_at"]))
    except (TypeError, ValueError):
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=datetime.timezone.utc)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now - at < datetime.timedelta(minutes=RETRY_AFTER_MINUTES)


def blockers_without_a_fix(data: dict) -> list[dict]:
    """Blocking issues with nothing for Accept to do. While a "delete this
    draft" stands the rest are moot — the delete is the answer. Logged on
    every review; empty is the contract (blockers the next review retries
    are expected to have none)."""
    if any(
        isinstance(s, dict) and s.get("kind") == "delete_invoice"
        for s in data.get("suggestions") or []
    ):
        return []
    return [
        i
        for i in data.get("issues") or []
        if isinstance(i, dict)
        and i.get("blocking")
        and not i.get("fix")
        and i.get("code") not in RETRY_NEXT_REVIEW
    ]


def _clears(data: dict, issue: dict) -> bool:
    """Server-side evaluation of an issue's clears_when against working values
    — the same dumb predicate the component evaluates client-side."""
    cw = issue.get("clears_when")
    if not isinstance(cw, dict):
        return False
    if cw.get("scope") == "line":
        v = (_line(data, cw.get("line_id")) or {}).get(cw.get("field"))
    else:
        v = data.get(cw.get("field"))
    if cw.get("op") == "balances":
        return totals_balance(data)
    if cw.get("op") == "not_null":
        return v is not None
    if cw.get("op") == "truthy":
        return bool(v)
    if cw.get("op") == "equals":
        return v == cw.get("value")
    return False


def _last_actions(data: dict) -> dict[str, dict]:
    """The last action per suggestion/issue id — an ``undone`` cancels."""
    last: dict[str, dict] = {}
    for a in data.get("suggestion_actions") or []:
        if isinstance(a, dict) and a.get("suggestion_id"):
            last[str(a["suggestion_id"])] = a
    return last


def is_settled(
    data: dict,
    issue: dict,
    last: dict[str, dict] | None = None,
    hand: set | None = None,
) -> bool:
    """A blocker stands down when its predicate holds against the working
    values, when a person's own value on the line answers it
    (invoice_edits.hand_settles), or when every suggestion that is its fix
    was accepted and still holds. Nothing else: there is no "I've checked
    this" (owner, 20 Oct 2026) — a stop is answered by its fix or by a
    person's value, or it stands. The one rule; the card shows the server's
    answer (invoice_decisions.annotate)."""
    if _clears(data, issue):
        return True
    from app.services.invoice_edits import hand_settles

    if hand_settles(data, issue, hand):
        return True
    if issue.get("code") in RETRY_NEXT_REVIEW:
        # Norm's answer (or the copy check) didn't come through: only asking
        # again, or a person's own record for the line, stands it down.
        return False
    last = _last_actions(data) if last is None else last
    if (issue.get("clears_when") or {}).get("op") == "balances":
        return False  # accepted fixes count only if the totals now match
    fix = issue.get("fix") or []
    by_id = {
        str(s.get("id")): s
        for s in data.get("suggestions") or []
        if isinstance(s, dict) and s.get("id")
    }
    return bool(fix) and all(
        (last.get(str(f)) or {}).get("action") == "accepted"
        and _fix_holds(data, by_id.get(str(f)))
        for f in fix
    )


def _fix_holds(data: dict, s: dict | None) -> bool:
    """An accepted fix still in force on the card: a line's value it set is
    still on the line. A decision carried while its line wasn't on the card
    (a line Norm adds, not added yet) set nothing, so it settles nothing."""
    if not s or not s.get("line_id") or s.get("kind") not in ("line_value", "strike"):
        return True
    ln = _line(data, s.get("line_id"))
    if ln is None:
        return False
    for k, v in (s.get("apply") or {}).items():
        have = ln.get(k)
        if (bool(have) != bool(v)) if k == "struck" else (have != v):
            return False
    return True


def compute_confidence(data: dict) -> str:
    """``ready`` iff no blocking issue stands."""
    last = _last_actions(data)
    for i in data.get("issues") or []:
        if i.get("blocking") and not is_settled(data, i, last):
            return "needs_review"
    return "ready"


# ---------------------------------------------------------------------------
# Applying suggestions (the autopilot's accept + the shared record shape)
# ---------------------------------------------------------------------------


def apply_suggestion(data: dict, s: dict) -> dict | None:
    """Apply one suggestion's ``apply``/``payload`` to the working values.
    Returns the before-values (the undo payload), or None when nothing applies
    locally (a create or a delete — Loaded writes, see autopilot_accept). A
    ``confirm`` applies nothing and returns ``{}``: the record is the act."""
    if s.get("kind") == "confirm":
        return {}
    if s.get("kind") == "delete_invoice" or s.get("kind") in LOADED_WRITE_ORDER:
        return None
    if s.get("kind") == "add_line":
        payload = dict(s.get("payload") or {})
        if not payload:
            return None
        lines = data.setdefault("lines", [])
        # Once only: a card saved again after Autopilot added the line, or a
        # decision carried onto a payload that already applied it, must not
        # add it a second time (do_receive would send it twice).
        if not any(
            isinstance(ln, dict) and payload.get("id") and ln.get("id") == payload["id"]
            for ln in lines
        ):
            lines.append(payload)
        return {"added_line_id": payload.get("id")}
    apply = s.get("apply") or {}
    if not apply:
        return None
    if s.get("line_id"):
        ln = _line(data, s["line_id"])
        if ln is None:
            return None
        before = {k: ln.get(k) for k in apply}
        ln.update(apply)
        # total follows qty × cost (receive recomputes anyway; keep display
        # honest).
        if "quantity_received" in apply or "unit_cost" in apply:
            q, c = _f(ln.get("quantity_received")), _f(ln.get("unit_cost"))
            if q is not None and c is not None:
                before.setdefault("total_cost", ln.get("total_cost"))
                ln["total_cost"] = round(q * c, 4)
        return before
    before = {k: data.get(k) for k in apply}
    data.update(apply)
    return before


#: The link a create writes, by kind: what makes a later "link this record"
#: the same answer.
_CREATE_LINKS = {
    "create_unit": "linked_unit_id",
    "create_item": "linked_item_id",
    "create_brand": "linked_brand_id",
    "create_supplier": "linked_supplier_id",
}
#: What an added line IS: the invoice's line. Its links are Norm's answers.
_ADD_IDENTITY = (
    "id",
    "code",
    "description",
    "quantity_received",
    "unit_cost",
    "total_cost",
)
_ADD_LINKS = ("linked_item_id", "linked_unit_id", "linked_brand_id")
#: Fields that are one value to a person: setting any is setting the group.
_HAND_GROUPS = (
    ("unit", "linked_unit_id", "unit_ratio"),
    ("linked_item_id", "item_name"),
    ("brand", "linked_brand_id", "brand_set"),
    ("linked_supplier_id", "supplier_name"),
    ("linked_purchase_order_id", "purchase_order_number"),
)
#: Line fields a create's recorded values may set when it is carried.
_LINE_KEYS = frozenset(
    {
        "linked_unit_id",
        "unit",
        "unit_ratio",
        "linked_item_id",
        "item_name",
        "linked_brand_id",
        "brand",
        "brand_set",
    }
)


def carry_forward_decisions(previous: dict, fresh: dict) -> int:
    """Re-apply the decisions a person already made, where the question is
    unchanged. Returns how many were carried.

    A review REBUILDS the payload from Loaded and clears ``suggestions``,
    ``issues`` and ``suggestion_actions``. Without this, accepting a
    suggestion and then re-reviewing silently threw the accept away: the
    working value reverted to Loaded's, the suggestion came back, and the
    receive was recorded as "ignored" (SI03448887, 26 Aug 2026).

    Carried only when the SAME suggestion id is regenerated with the SAME
    proposal — a changed proposal is a different question. A decision a v1
    review recorded against a BLOCKER (whose Accept applied the blocker's own
    value) carries onto the suggestion that is now that blocker's fix, when it
    proposes the same value.

    A record created on Norm's suggestion (a unit, a stock item, a brand) is in
    Loaded afterwards, so the next review proposes LINKING it instead of
    creating it. That is the same question with the same answer: the accept
    carries onto the link, and the line keeps the record (19 Oct 2026 — the
    link used to be lost on every re-review).
    """
    if not isinstance(previous, dict) or not isinstance(fresh, dict):
        return 0
    prior_actions = _last_actions(previous)
    if not prior_actions:
        return 0
    # What the fresh payload already decided (Autopilot, on this run) is newer
    # than anything on the old card: never re-applied, never overridden.
    fresh_decided = set(_last_actions(fresh))

    prior_suggestions = {
        str(s.get("id")): s
        for s in previous.get("suggestions") or []
        if isinstance(s, dict) and s.get("id")
    }
    actions = fresh.setdefault("suggestion_actions", [])
    carried_ids: set[str] = set()

    # The records an accepted create made, per line (the line from the
    # suggestion, or from the id when the suggestion has been cleared).
    created_after: dict[str, dict] = {}
    for sid, action in prior_actions.items():
        before = prior_suggestions.get(sid) or {}
        kind = before.get("kind") or sid.split(":", 1)[0]
        if action.get("action") == "accepted" and kind in _CREATE_LINKS:
            line = before.get("line_id") or sid.split(":", 2)[-1]
            created_after.setdefault(str(line), {}).update(action.get("after") or {})

    def _same_question(before: dict, now: dict) -> bool:
        if before.get("kind") == now.get("kind") == "add_line":
            # Adding the line is the decision about the invoice's line: the
            # same line at the same money is the same question. Its links are
            # Norm's answers; one may differ only by being the record a create
            # on that line made (a unit, item or brand made for it, now linked)
            # — any other link change is a new question.
            b = before.get("payload") if isinstance(before.get("payload"), dict) else {}
            n = now.get("payload") if isinstance(now.get("payload"), dict) else {}
            if any(b.get(k) != n.get(k) for k in _ADD_IDENTITY):
                return False
            made = created_after.get(str(now.get("line_id")), {})
            return all(
                b.get(k) == n.get(k) or (n.get(k) and n.get(k) == made.get(k))
                for k in _ADD_LINKS
            )
        return (before.get("apply") or {}) == (now.get("apply") or {}) and (
            before.get("payload") or {}
        ) == (now.get("payload") or {})

    def _carry(s: dict, action: dict) -> None:
        if action.get("action") == "accepted":
            if apply_suggestion(fresh, s) is None:
                # A create: its record's id is what the accept recorded.
                after = action.get("after") or {}
                if not isinstance(after, dict):
                    after = {}
                if s.get("line_id"):
                    ln = _line(fresh, s.get("line_id"))
                    if ln is not None:
                        ln.update({k: v for k, v in after.items() if k in _LINE_KEYS})
                elif after.get("linked_supplier_id"):
                    fresh["linked_supplier_id"] = after["linked_supplier_id"]
                    if after.get("supplier_name"):
                        fresh["supplier_name"] = after["supplier_name"]
        carried = {**action, "suggestion_id": s.get("id")}
        if str(action.get("suggestion_id")) != str(s.get("id")):
            # Carried under another id (a create, now the link to what it
            # made): an Undo still finds the edits it replaced by the first.
            carried["carried_from"] = action.get("carried_from") or action.get(
                "suggestion_id"
            )
        actions.append(carried)
        carried_ids.add(str(s.get("id")))

    fresh_suggestions = [
        s for s in fresh.get("suggestions") or [] if isinstance(s, dict)
    ]
    for s in fresh_suggestions:
        sid = str(s.get("id") or "")
        action = prior_actions.get(sid)
        before = prior_suggestions.get(sid)
        if not action or action.get("action") == "undone" or sid in fresh_decided:
            continue
        if before is None:
            # The old card's suggestions were cleared (a cached review the
            # invoice's change or a sibling's receive invalidated): an accept
            # still carries when the fresh suggestion writes exactly what the
            # accept recorded.
            after = action.get("after")
            apply = s.get("apply") or {}
            if (
                action.get("action") == "accepted"
                and isinstance(after, dict)
                and apply
                and all(k in after and after[k] == v for k, v in apply.items())
            ):
                _carry(s, action)
            continue
        if not _same_question(before, s):
            continue
        _carry(s, action)

    created: dict[tuple, tuple[dict, str]] = {}
    for sid, action in prior_actions.items():
        before = prior_suggestions.get(sid) or {}
        key = _CREATE_LINKS.get(before.get("kind"))
        if action.get("action") == "accepted" and key:
            created[(str(before.get("line_id")), before.get("field"))] = (action, key)
    for s in fresh_suggestions:
        if (
            # A line's link, or the header's supplier link (a created supplier
            # is proposed as kind "supplier" next time).
            s.get("kind") not in ("line_value", "supplier")
            or str(s.get("id")) in carried_ids
            or str(s.get("id")) in fresh_decided
        ):
            continue
        hit = created.get((str(s.get("line_id")), s.get("field")))
        if hit is None:
            continue
        action, key = hit
        made = (action.get("after") or {}).get(key)
        if made and (s.get("apply") or {}).get(key) == made:
            _carry(s, action)

    by_id = {str(s.get("id")): s for s in fresh_suggestions}
    for issue in fresh.get("issues") or []:
        iid = str(issue.get("id") or "")
        action = prior_actions.get(iid)
        if not action or action.get("action") == "undone" or iid in fresh_decided:
            continue
        after = action.get("after") or {}
        if action.get("action") == "accepted":
            for fid in issue.get("fix") or []:
                s = by_id.get(str(fid))
                if s is None or str(fid) in carried_ids or str(fid) in fresh_decided:
                    continue
                if (prior_actions.get(str(fid)) or {}).get("action") == "undone":
                    continue  # the person undid this answer: it stays undone
                apply = s.get("apply") or {}
                shared = [k for k in after if k in apply]
                if s.get("kind") == "confirm" and not after:
                    _carry(s, action)
                elif shared and all(apply[k] == after[k] for k in shared):
                    _carry(s, action)
        # The blocker's own entry is not carried: a stop is answered by its
        # fix or by a person's value, never by a mark of its own.
    return len(carried_ids)


def autopilot_accept(
    db,
    config_db,
    venue_id: str,
    invoice_id: str,
    data: dict,
    settings: dict,
    *,
    lh=None,
) -> list[str]:
    """Autopilot's Accept: what the Replica is sure of, plus what the venue's
    switches allow — each decided exactly as a person's Accept on the card
    (invoice_decisions.decide). Returns what it did, in words, for the run's
    verdict.

    The switches do one thing: say which suggestions Autopilot may accept —
    a suggestion with a ``gate`` only when that switch is on; any other only
    when the Replica is sure of it (a guess — a likely order, the closest
    supplier — always waits for a person). A field a person set by hand on
    the card is theirs: nothing is applied over it (owner, 19 Oct 2026).

    The order of work is the shared one (invoice_decisions.order_key): a
    delete first, then lines to add, then the draft's other values, then the
    Loaded creates in the order they depend on each other. A create that
    fails leaves its blocker standing: the invoice parks instead of
    receiving half-built.
    """
    from app.services import supplier_identity
    from app.services.invoice_decisions import decide, order_decisions
    from app.services.invoice_edits import hand_fields, replay_edits

    done: list[str] = []
    data.setdefault("suggestion_actions", [])
    last = _last_actions(data)

    def _client():
        nonlocal lh
        if lh is None:
            lh = LoadedInvoiceClient(db, config_db, venue_id)
        return lh

    def _allowed(s: dict) -> bool:
        if s.get("gate"):
            return _VA.gate_open(settings, s.get("gate"))
        return s.get("sure", True) is not False

    hand = hand_fields(data)

    def _by_hand(s: dict) -> bool:
        # A confirm ("receive it as it is") writes nothing, so it never goes
        # over a person's value: a unit they picked that clashes in kind with
        # the item is received when that switch is on, parked when it is off
        # (owner, 20 Oct 2026).
        if not hand or s.get("kind") == "confirm":
            return False
        line = s.get("line_id")
        key = str(line) if line else None
        fields = set((s.get("apply") or {}).keys())
        if s.get("field"):
            fields.add(s["field"])
        if _CREATE_LINKS.get(s.get("kind")):
            fields.add(_CREATE_LINKS[s["kind"]])
        for group in _HAND_GROUPS:
            if fields & set(group):
                fields |= set(group)
        return any((key, f) in hand for f in fields)

    def _supplier_plausible(s: dict) -> bool:
        # The riskiest create by a distance: supplier identity picks the
        # extraction layout, so a second 'SERVICE FOODS' sends every later
        # invoice from that business to a prompt written for someone else
        # (10 Aug 2026). Autopilot creates only a supplier nothing plausibly
        # matches — past the close names Norm checked and ruled out.
        payload = s.get("payload") or {}
        name = payload.get("supplier_name") or ""
        # The printed name too: the close records Norm ruled out were found
        # with it, and the record is created under the shorter trading name.
        names = [n for n in (payload.get("printed_name"), name) if n]
        try:
            rows = _client().get("/1.0/stock/internal/suppliers")
        except Exception as exc:  # noqa: BLE001 — then it is not created
            logger.info("autopilot supplier check failed on %s: %s", invoice_id, exc)
            return True
        rows = rows if isinstance(rows, list) else []
        live = [
            r
            for r in rows
            if isinstance(r, dict)
            and not r.get("removedAt")
            and not r.get("datestampDeleted")
        ]
        ruled_out = {str(i) for i in payload.get("not_these") or []}
        if [
            m
            for n in names
            for m in supplier_identity.plausible_matches(n, live)
            if str(m.get("id")) not in ruled_out
        ]:
            logger.info(
                "autopilot will not create supplier '%s' on %s: an existing "
                "supplier is a plausible match",
                name,
                invoice_id,
            )
            return True
        return False

    pending = [
        s
        for s in data.get("suggestions") or []
        if isinstance(s, dict)
        and (last.get(str(s.get("id"))) or {}).get("action")
        not in ("accepted", "dismissed")
        and _allowed(s)
        and not _by_hand(s)
    ]

    def _decide(s: dict) -> dict:
        needs_loaded = (
            s.get("kind") in LOADED_WRITE_ORDER or s.get("kind") == "delete_invoice"
        )
        try:
            client = _client() if needs_loaded else None
        except Exception as exc:  # noqa: BLE001 — the blocker simply stands
            return {"ok": False, "detail": f"no Loaded client: {exc}"}
        return decide(
            data,
            {"accept": s.get("id")},
            actor="norm",
            lh=client,
            db=db,
            config_db=config_db,
            venue_id=venue_id,
            invoice_id=invoice_id,
        )

    for s in pending:
        if s.get("kind") != "delete_invoice":
            continue
        out = _decide(s)
        if not out.get("ok"):
            logger.warning(
                "gated delete failed on %s: %s", invoice_id, out.get("detail")
            )
            continue
        # Why, kept on the draft (the card's Delete does the same) so the run
        # can say what its deletes were — not just how many.
        done.append(out.get("did") or "deleted the draft")
        return done

    decisions = order_decisions([{"accept": s.get("id")} for s in pending], data)
    by_id = {str(s.get("id")): s for s in pending}
    adds = [
        by_id[str(d["accept"])]
        for d in decisions
        if by_id[str(d["accept"])].get("kind") == "add_line"
    ]
    for s in adds:
        _decide(s)
    added = {str(s.get("line_id")) for s in adds}
    for s in data.get("suggestions") or []:
        # A decision already made on a line Norm adds (carried while the line
        # wasn't on the card, so it set nothing) goes on it now, before the
        # person's own edits — which win where they came later.
        if (
            isinstance(s, dict)
            and str(s.get("line_id")) in added
            and s.get("kind") in ("line_value", "strike")
            and (last.get(str(s.get("id"))) or {}).get("action") == "accepted"
        ):
            apply_suggestion(data, s)
    if adds and data.get("edits"):
        # What a person did on a line Norm adds again — struck it, typed its
        # quantity, picked its unit — goes on it now, as the card's own
        # rebuild puts it there; then nothing below writes over it.
        replay_edits(data, data.get("edits"))
        hand = hand_fields(data)
        pending = [s for s in pending if not _by_hand(s)]
        by_id = {str(s.get("id")): s for s in pending}
        decisions = [d for d in decisions if str(d["accept"]) in by_id]

    # What this Accept made NEW in Loaded (a get-or-create that found an
    # existing record made nothing) — the run's summary counts these.
    created: dict[str, list[str]] = {
        "stock_items": [],
        "suppliers": [],
        "units": [],
        "brands": [],
    }
    data["autopilot_created"] = created
    buckets = {
        "supplier": "suppliers",
        "unit": "units",
        "brand": "brands",
        "stock item": "stock_items",
    }
    for d in decisions:
        s = by_id[str(d["accept"])]
        kind = s.get("kind")
        if kind == "add_line":
            continue
        if kind == "create_supplier" and _supplier_plausible(s):
            continue
        out = _decide(s)
        if not out.get("ok"):
            if kind in LOADED_WRITE_ORDER:
                logger.info(
                    "autopilot %s could not run on %s: %s",
                    kind,
                    invoice_id,
                    out.get("detail"),
                )
            continue
        if kind in LOADED_WRITE_ORDER:
            if out.get("created") and out.get("kind") in buckets:
                created[buckets[out["kind"]]].append(out.get("name"))
            done.append(out.get("did") or f"{kind} done")
        elif s.get("gate") and out.get("did"):
            done.append(out["did"])
    return done


# ---------------------------------------------------------------------------
# The review itself
# ---------------------------------------------------------------------------


def review_invoice(
    db: Session,
    config_db: Session,
    venue_id: str,
    invoice_id: str,
    *,
    lh=None,
    detail: dict | None = None,
    extraction: dict | None = None,
    reference: dict | None = None,
    require_valid_po: bool | None = None,
    settings: dict | None = None,
    allow_sensei: bool = False,
    totals_ask=None,
    pairing_ask=None,
    supplier_ask=None,
) -> dict:
    """Review one invoice and return the full replica_v2 doc payload.

    Pure computation + Loaded reads: the caller persists the payload onto
    working documents (and decides about receiving). ``detail`` /
    ``extraction`` / ``reference`` let the batch path inject prefetched work.

    ``require_valid_po`` None means: derive it from the ``receive_without_po``
    switch in ``settings`` — the viewer's receiving policy
    (approvals.receiving_settings) — so the card tells the same story
    autopilot acts on. The interactive card used to force False ("a human is
    looking"), which filed the missing-PO blocker under worth-knowing and hid
    what autopilot would actually do (user report, 18 Aug 2026: it should read
    as blocked-from-auto-receive naming the toggle). No settings — a background
    review — requires an order.
    """
    if require_valid_po is None:
        from app.services import venue_autopilot as _VAmod

        require_valid_po = not _VAmod.settings_from(settings)[_VAmod.RECEIVE_WITHOUT_PO]
    if lh is None:
        lh = LoadedInvoiceClient(db, config_db, venue_id)
    if detail is None:
        detail = lh.invoice(invoice_id)
    data = build_received_invoice_data(detail)
    # Open where Loaded's SCREEN opens, not where its API does: Loaded resolves
    # an unlinked line's stock item and unit in the browser from the supplier
    # code, so those resolved values ARE Loaded's position — resolved once, on
    # the snapshot (the Loaded tab), and the working lines opened on them
    # before a single suggestion is computed, so a suggestion means "the copy
    # disagrees with Loaded". Independent best-effort steps, each guarded on
    # its own: a failed order fetch must not cost us the mirror, and vice versa.
    catalogue = (reference or {}).get("catalogue")
    units = (reference or {}).get("units")
    try:
        if not catalogue:
            catalogue, units = loaded_reference(venue_id, db, lh, config_db=config_db)
    except Exception as exc:  # noqa: BLE001
        logger.info("loaded resolution unavailable for %s: %s", invoice_id, exc)
    try:
        # The order's rows — reference data for the suggestions below (which
        # order row does this line satisfy) and for the mirror's ordered
        # quantities. The router refreshes it after us; running it here keeps
        # the batch/autopilot path from depending on that.
        attach_po_reference(data, lh)
    except Exception as exc:  # noqa: BLE001
        logger.info("order reference unavailable for %s: %s", invoice_id, exc)
    try:
        enrich_loaded_snapshot(data, catalogue=catalogue, units=units)
        start_from_screen(data)
        project_po_reference(data)  # the order rows follow the opened links
    except Exception as exc:  # noqa: BLE001
        logger.info("loaded mirror unavailable for %s: %s", invoice_id, exc)
    data["doc_schema"] = DOC_SCHEMA
    data["suggestions"] = []
    data["issues"] = []
    data["suggestion_actions"] = []

    file_id = data.get("file_id")
    # An invoice the supplier's system sent as data (Ordermentum) has no copy
    # because there is nothing to scan: Loaded's own figures stand in for the
    # read, and everything after it runs as usual.
    feed = is_feed_invoice(detail)
    if feed and extraction is None:
        extraction = extraction_from_loaded(detail)
    # What this invoice could duplicate, reaching back to an older invoice's
    # own date — fetched once, for the Ordermentum check below and the
    # replica's same-number check.
    received = _received_rows(lh, reference, detail.get("issuedAt"))
    if received is not None:
        reference = {**(reference or {}), "received_feed": received}
    # An Ordermentum delivery reaches Loaded twice under different numbers:
    # Ordermentum's copy and the supplier's own invoice (which carries the
    # Ordermentum order number). When the other copy is already received this
    # one is a duplicate — settled here, before any copy is read.
    twin = message = summary = None
    also_remove: list[str] = []
    if feed or ordermentum_order_number(
        detail.get("purchaseOrderNumber"), detail.get("referenceNumber")
    ):
        from app.services import ordermentum_pairs

        dup = ordermentum_pairs.check(
            ordermentum_pairs.Copy.from_detail(detail, invoice_id),
            received,
            venue_id=venue_id,
        )
        if dup:
            twin, also_remove = dup.twin_row, dup.also_remove
            message = ordermentum_twin_message(twin, feed=feed)
            summary = "the other copy of an Ordermentum delivery is already received"
    # The same invoice sent more than once becomes several drafts, each with its
    # own PDF — PNM's arrived six times, and all six were read before five were
    # deleted (16 Oct 2026, a quarter of a night's AI spend). When Loaded's own
    # read of the draft already matches a received invoice on number AND total,
    # it's a duplicate without reading the copy. The total is what makes
    # Loaded's read trustworthy here; anything short of both goes to the read.
    number = detail.get("referenceNumber")
    if (
        twin is None
        and file_id
        and any(ch.isdigit() for ch in str(number or ""))
        and isinstance(detail.get("total"), (int, float))
    ):
        same = received_copy(
            received,
            number,
            detail.get("supplierName"),
            is_credit_note=detail["total"] < 0,
            own_invoice_id=invoice_id,
            total=detail["total"],
        )
        if same and str(same.get("type") or "") != "PurchaseOrder":
            twin = same
            message = (
                f"invoice {number} was already received on "
                f"{str(same.get('receivedAt') or '')[:10]} with the same total — "
                "delete this duplicate"
            )
            summary = "a copy of an invoice already received"
    if twin:
        data["issues"].append(
            {
                "id": "duplicate_invoice",
                "code": "duplicate_invoice",
                "blocking": True,
                "line_id": None,
                "gate": _VA.AUTO_DELETE_DUPLICATES,
                "message": message,
                "data": {"duplicate_of_invoice_id": twin.get("id")},
            }
        )
        _delete_suggestion(
            data["suggestions"],
            data,
            message,
            _VA.AUTO_DELETE_DUPLICATES,
            reason="duplicate",
            extra={
                "duplicate_of_invoice_id": twin.get("id"),
                "duplicate_of_file_id": twin.get("fileId"),
                # Held pairs proved real once the supplier's number moved:
                # their later copies (already received) go with this one.
                **({"also_remove": also_remove} if also_remove else {}),
            },
            summary=summary,
        )
        link_fixes(data, None, data["issues"], data["suggestions"])
    elif not file_id and not feed:
        # A draft with no document behind it can't be reviewed: the Replica's
        # answer is "delete it" (a Loaded write, the verified delete endpoint;
        # the venue's switch decides whether Norm may do it alone). The
        # user's call, 11 Oct 2026 — drafts started from an order included.
        message = (
            "no invoice is attached in Loaded — nothing to review against — "
            "delete the draft (or attach the document in Loaded)"
        )
        data["issues"].append(
            {
                "id": "no_copy_attached",
                "code": "no_copy_attached",
                "blocking": True,
                "line_id": None,
                "gate": _VA.AUTO_DELETE_UNREADABLE,
                "message": message,
            }
        )
        _delete_suggestion(
            data["suggestions"],
            data,
            message,
            _VA.AUTO_DELETE_UNREADABLE,
            reason="no_copy",
            summary="no invoice attached — draft deleted",
        )
        link_fixes(data, None, data["issues"], data["suggestions"])
    else:
        # One alias fetch feeds both the layout lookup and the cache key; the
        # supplier list vets those aliases and is reused by the replica below.
        suppliers = account_suppliers(lh)
        aliases = supplier_aliases(lh, detail, suppliers)
        # No study before the first read: Loaded's supplier name is its own
        # scan of the paper and is sometimes a fragment — "PTY LTD", "LTD" and
        # "PTY." each became a layout's name (16–17 Oct 2026). Loaded's name
        # only finds an existing layout (the instructions below); a supplier
        # with none is studied once the copy says who printed it.
        studied = False
        if extraction is None:
            extraction = extract_invoice_copy(
                db,
                lh,
                file_id,
                instructions=extraction_instructions(config_db, lh, detail, aliases),
                venue_key=venue_id,
            )
        if isinstance(extraction, dict) and extraction.get("transient"):
            # Norm, Loaded or the extraction service was briefly down — that
            # says nothing about the document. It used to be filed as
            # "unreadable" with a delete-the-draft fix (a venue that let Norm
            # clear unreadable copies would have deleted good drafts during an
            # outage). Blocks this run only; nothing to delete; next run retries.
            data["issues"].append(
                {
                    "id": "copy_not_checked",
                    "code": "copy_not_checked",
                    "blocking": True,
                    "line_id": None,
                    "message": (
                        "the invoice copy couldn't be checked right now — a "
                        "service was briefly unavailable ("
                        + str(extraction.get("error"))
                        + "). The copy is probably fine; the next review "
                        "retries it."
                    ),
                }
            )
        elif not isinstance(extraction, dict) or extraction.get("error"):
            err = (
                extraction.get("error")
                if isinstance(extraction, dict)
                else "no extraction"
            )
            message = (
                f"the invoice could not be read ({err}) — delete the draft (or "
                "fix the document in Loaded)"
            )
            data["issues"].append(
                {
                    "id": "copy_unreadable",
                    "code": "copy_unreadable",
                    "blocking": True,
                    "line_id": None,
                    "gate": _VA.AUTO_DELETE_UNREADABLE,
                    "message": message,
                }
            )
            _delete_suggestion(
                data["suggestions"],
                data,
                message,
                _VA.AUTO_DELETE_UNREADABLE,
                reason="unreadable",
                summary=f"unreadable invoice ({err}) — draft deleted",
            )
            link_fixes(data, None, data["issues"], data["suggestions"])
        else:
            # PASS 2 — the copy decides which layout reads it. A feed invoice
            # has no copy to read again.
            spec_used = None
            first_read = extraction
            if file_id:
                extraction, spec_used = reread_under_printed_spec(
                    db, config_db, lh, venue_id, file_id, detail, aliases, extraction
                )
            # The supplier the COPY names may be one Norm has never seen, even
            # when Loaded's name matched something. Ask the roster about the
            # printed name too, or a business filed under someone else's Loaded
            # record is never trained.
            if allow_sensei and file_id and spec_used is None:
                extraction, spec_used = study_printed_supplier(
                    db,
                    config_db,
                    lh,
                    venue_id,
                    invoice_id,
                    file_id,
                    detail,
                    aliases,
                    extraction,
                )
                studied = studied or spec_used is not None
            # Which layout read the copy, for the invoice activity page: a
            # re-read hands back a new extraction, no re-read the same one.
            data["process"] = {
                "layout": getattr(spec_used, "name", None),
                "reread": extraction is not first_read,
                "studied": bool(studied),
            }
            data["extracted_snapshot"] = _extracted_snapshot(extraction)
            # Feed the Norm supplier-product catalogue: every successful
            # extraction's printed sizes are supplier-stated physical facts
            # (provenance 'printed'). Best-effort — never fails the review.
            try:
                from app.services import supplier_catalog

                supplier_catalog.observe_extraction(config_db, extraction)
                config_db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.info("catalogue observe failed: %s", exc)
                try:
                    config_db.rollback()
                except Exception:  # noqa: BLE001
                    pass
            # Loaded's draft date can be weeks off the printed one (a Premier
            # Northpak copy: draft 21 May, printed 16 Apr 2025), so an old
            # invoice's twin is also looked for around the date read off it —
            # without this 4107357 was received four times (17 Oct 2026).
            if received is not None and extraction.get("invoice_date"):
                received = _received_rows(lh, reference, extraction["invoice_date"])
                reference = {**(reference or {}), "received_feed": received}
            replica = build_replica(
                db,
                config_db,
                venue_id,
                extraction,
                lh=lh,
                own_invoice_id=invoice_id,
                # Loaded's own read of the document — its rule for a credit
                # note is simply total < 0, and it is a signal the extraction
                # alone cannot supply.
                loaded_total=data.get("total"),
                # The supplier Loaded records for this invoice. When the
                # invoice was raised from a purchase order this is the
                # supplier a human picked at order time — not OCR — so it is
                # a real identity hint when the printed name resolves to
                # nothing on its own.
                loaded_supplier_name=data.get("supplier_name"),
                **_with_suppliers(reference, suppliers),
            )
            data["replica"] = replica
            # A decisive resolver verdict is an enrichment verdict — record
            # it so the next invoice for this product resolves from the
            # catalogue tier without a model call. Best-effort, like the
            # observe above.
            try:
                from app.services import supplier_catalog

                supplier_catalog.learn_from_resolver(
                    config_db,
                    supplier_catalog.supplier_key_for(
                        extraction.get("supplier_name"), config_db
                    ),
                    replica.get("lines"),
                )
                config_db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.info("catalogue resolver-learn failed: %s", exc)
                try:
                    config_db.rollback()
                except Exception:  # noqa: BLE001
                    pass
            # DERIVED, server-owned: never patched by the client, never sent
            # to Loaded. The replica's verdict wins; Loaded's sign is the
            # fallback for a draft that has not been reviewed yet.
            data["is_credit_note"] = bool(
                replica.get("is_credit_note")
                or (isinstance(data.get("total"), (int, float)) and data["total"] < 0)
            )

            def invoice_copy() -> dict | None:
                # Read only when the totals need it (balance_after_suggestions).
                from app.services.invoice_extraction import _document_block

                return _document_block(lh, str(file_id)) if file_id else None

            # A supplier Norm will create takes the details its invoice prints.
            read_details = None
            if db is not None:
                from app.services.invoice_extraction import read_supplier_details

                def read_details(name: str) -> dict:
                    # No copy (an Ordermentum feed invoice): nothing to read.
                    # str(None) used to send "None" to the download (16 Oct).
                    return read_supplier_details(
                        db, lh, str(file_id or ""), supplier_name=name
                    )

            # An unknown printed name with close Loaded records: Norm decides
            # which, if any, is the business (replica_finish.settle_supplier).
            same_business = None
            if db is not None or supplier_ask is not None:
                from app.services import invoice_arbiter

                def same_business(name: str, details, candidates) -> dict:
                    return invoice_arbiter.same_business(
                        name, details, candidates, ask_llm=supplier_ask, db=db
                    )

            def resolve_added(lines: list[dict]) -> dict:
                # A line the totals work-out found on the copy is matched like
                # any line the read found: the Replica for just those lines.
                mini = build_replica(
                    db,
                    config_db,
                    venue_id,
                    {**extraction, "lines": lines},
                    lh=lh,
                    own_invoice_id=invoice_id,
                    loaded_supplier_name=data.get("supplier_name"),
                    **_with_suppliers(reference, suppliers),
                )
                return {
                    "lines": mini.get("lines") or [],
                    "issues": mini.get("issues") or [],
                }

            totals_unresolved = None
            if db is not None:

                def totals_unresolved(info: dict) -> None:
                    from app.services.totals_alert import alert_totals_unresolved

                    alert_totals_unresolved(venue_id, info)

            def finish(pairs: dict[str, str]) -> None:
                # The order the invoice names is evidence for the rules (which
                # item an ambiguous code is), so its rows are read first.
                if not po_id_for(data) and replica.get("linked_purchase_order_id"):
                    try:
                        fetch_po_reference(
                            data, lh, po_id=replica["linked_purchase_order_id"]
                        )
                    except Exception as exc:  # noqa: BLE001 — evidence, best-effort
                        logger.info("order rows unavailable: %s", exc)
                # Every rule's answer goes INTO the replica before anything is
                # compared, so the card can't contradict the Replica tab.
                finish_replica(
                    data,
                    replica,
                    pairs,
                    catalogue=catalogue,
                    units=units,
                    suppliers=suppliers,
                    lh=lh,
                    totals_ask=totals_ask,
                    db=db,
                    extraction=extraction,
                    read_supplier_details=read_details,
                    same_business=same_business,
                )

            suggestions, extra_issues, pairs = build_suggestions(
                data, replica, db=db, pairing_ask=pairing_ask, finish=finish
            )
            # A brand Loaded names but has no record for stops its own receive.
            brand_issues(data, extra_issues)
            # Cache the rows of the order we're about to SUGGEST, so accepting
            # that suggestion is instant: the projection (order date, per-line
            # quantity ordered, "ordered, not delivered") is pure and recomputes
            # on the accept patch itself. Without this the link landed with an
            # empty Qty Ordered column until the draft was re-opened.
            if not po_id_for(data):
                apply = next(
                    (
                        s.get("apply") or {}
                        for s in suggestions
                        if s.get("kind") in ("link_po", "split_reference")
                    ),
                    {},
                )
                proposed = apply.get("linked_purchase_order_id") or apply.get(
                    "split_po_id"
                )
                if proposed:
                    try:
                        fetch_po_reference(
                            data,
                            lh,
                            po_id=proposed,
                            # A split: the sibling's quantities are what let the
                            # projection say "received on the other delivery"
                            # instead of "never arrived".
                            sibling_invoice_id=apply.get("split_sibling_invoice_id"),
                        )
                    except Exception as exc:  # noqa: BLE001 — a pre-cache only
                        logger.info("suggested order pre-cache failed: %s", exc)
            issues = _finalise_issues(
                replica.get("issues") or [],
                extra_issues,
                pairs,
                require_valid_po=require_valid_po,
            )
            # A unit the invoice names but Loaded lacks, on a line whose Loaded
            # draft holds some unit: without its own blocker autopilot received
            # with Loaded's unit instead of the unit on the paper.
            unit_blocked = {
                str(i.get("line_id")) for i in issues if i.get("code") in _UNIT_ISSUES
            }
            for s in suggestions:
                lid = str(s.get("line_id") or "")
                if (
                    s.get("kind") == "create_unit"
                    and lid not in unit_blocked
                    and _line(data, lid) is not None
                ):
                    issues.append(
                        {
                            "id": f"unit_would_be_created:{lid}",
                            "code": "unit_would_be_created",
                            "blocking": True,
                            "line_id": lid,
                            "gate": _VA.AUTO_CREATE_UNITS,
                            "message": s.get("explanation")
                            or "the invoice's unit isn't in Loaded",
                        }
                    )
            # No purchase order at all — none linked in Loaded, none the
            # invoice names that resolves, no order story (a split, a doubled
            # order) explaining why. Blocking under the require_valid_po policy,
            # which both surfaces derive from the venue's receive_without_po
            # switch, so the card tells the story autopilot acts on. A credit
            # note legitimately has none — the order it prints belongs to the
            # invoice being credited. A likely order is only a guess, so it
            # answers this blocker rather than replacing it.
            if (
                not data.get("linked_purchase_order_id")
                and not data.get("is_credit_note")
                and not any(
                    s.get("kind") in ("link_po", "split_reference")
                    and s.get("sure", True) is not False
                    for s in suggestions
                )
                and not any(
                    i.get("code") in ("po_unresolved", "po_split_order") for i in issues
                )
            ):
                issues.append(
                    {
                        "id": "po_missing",
                        "code": "po_missing",
                        "blocking": require_valid_po,
                        "line_id": None,
                        "gate": _VA.RECEIVE_WITHOUT_PO,
                        "message": (
                            "no purchase order is linked and the invoice "
                            "references none that resolves"
                        ),
                        "clears_when": {
                            "scope": "header",
                            "field": "linked_purchase_order_id",
                            "op": "not_null",
                        },
                    }
                )
            # The totals check runs on what the suggestions leave, last, so it
            # sees every other answer.
            balance_after_suggestions(
                data,
                suggestions,
                issues,
                replica=replica,
                pairs=pairs,
                ask=totals_ask,
                db=db,
                document=invoice_copy,
                resolve_lines=resolve_added,
                on_unresolved=totals_unresolved,
            )
            # The organisation's own receiving rules (a due date; a venue's unit
            # for bunches): its choice, so it wins over the invoice (owner, 22
            # Oct 2026). Before the unit-kind check, which judges the units the
            # rules leave.
            from app.services import receiving_rules

            receiving_rules.apply(
                db,
                venue_id,
                data,
                replica,
                suggestions,
                issues=issues,
                units=units,
                pairs=pairs,
            )
            unit_kind_issues(data, suggestions, catalogue, units, issues)
            link_fixes(data, replica, issues, suggestions)
            data["suggestions"] = suggestions
            data["issues"] = issues

    missing = blockers_without_a_fix(data)
    if missing:
        logger.warning(
            "review %s: %d blocker(s) without a fix: %s",
            invoice_id,
            len(missing),
            sorted({str(i.get("code")) for i in missing}),
        )
    from app.services.invoice_decisions import annotate

    annotate(data)
    data["reviewed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    data["reviewed_invoice_fingerprint"] = data.get("loaded_invoice_fingerprint")
    return data


def account_suppliers(lh) -> list[dict]:
    """The venue's Loaded supplier records. Best-effort — callers fetch this
    ONCE per run and pass it down; it is the account's own answer to "are these
    two different businesses", which every alias is checked against."""
    try:
        rows = lh.get("/1.0/stock/internal/suppliers")
        return rows if isinstance(rows, list) else []
    except Exception as exc:  # noqa: BLE001 — the check degrades, never fails
        logger.info("supplier list unavailable: %s", exc)
        return []


def supplier_aliases(
    lh, detail: dict, suppliers: list[dict] | None = None
) -> list[str]:
    """This account's spellings for the invoice's supplier, sorted and vetted.

    One fetch, one order: aliases decide BOTH which spec matches and what goes
    in the extraction cache key, so the sort is not cosmetic — an unstable
    order would re-extract every invoice on every run.

    Vetted because Loaded's list is what its document scanning has seen, not a
    curated registry: see ``admissible_aliases``.
    """
    from app.services.spec_dojo import loaded_supplier_aliases
    from app.services.supplier_identity import admissible_aliases

    name = detail.get("supplierName")
    if not name:
        return []
    try:
        found = loaded_supplier_aliases(lh, detail)
    except Exception as exc:  # noqa: BLE001 — aliases are hints
        logger.info("supplier aliases unavailable: %s", exc)
        return []
    if suppliers is None:
        suppliers = account_suppliers(lh)
    return sorted(admissible_aliases(name, found, suppliers))


def extraction_instructions(
    config_db: Session, lh, detail: dict, aliases: list[str] | None = None
) -> str:
    """The composed extraction instructions for one invoice (cache-key
    material): main prompt + Loaded's supplier (with its stored aliases,
    sorted for key stability) + the matching supplier spec's notes.

    Pass ``aliases`` when the caller already fetched them — the same list has
    to reach the sensei's guard, and Loaded should be asked only once.
    """
    if aliases is None:
        aliases = supplier_aliases(lh, detail)
    return pdf_instructions_for(
        config_db,
        loaded_supplier=detail.get("supplierName"),
        loaded_aliases=aliases,
    )


def _received_rows(lh, reference: dict | None, invoice_date: object) -> list | None:
    """The received feed around ``invoice_date`` (``received_feed_for``), or
    None when Loaded couldn't be asked — the replica then fetches its own."""
    try:
        return received_feed_for(
            lh, invoice_date, (reference or {}).get("received_feed")
        )
    except Exception as exc:  # noqa: BLE001 — the duplicate checks degrade, never fail
        logger.info("received feed unavailable: %s", exc)
        return (reference or {}).get("received_feed")


def _with_suppliers(reference: dict | None, suppliers: list[dict]) -> dict:
    """The replica's reference bundle, carrying the supplier list we already
    fetched to vet the aliases — so Loaded is asked for it once, not twice. A
    caller that supplied its own list keeps it."""
    ref = dict(reference or {})
    if ref.get("suppliers") is None:
        ref["suppliers"] = suppliers
    return ref


def study_printed_supplier(
    db: Session,
    config_db: Session,
    lh,
    venue_id: str,
    invoice_id: str,
    file_id: object,
    detail: dict,
    aliases: list[str],
    extraction: dict,
) -> tuple[dict, object]:
    """Study the supplier the copy PRINTS when it has no prompt, then read the
    copy again under what the study wrote, before anything is decided.

    This is the only study: a supplier with no layout is read first with the
    main prompt, because only the copy can say who printed it (Loaded's name
    for the draft named layouts "PTY LTD" and "LTD", 16–17 Oct 2026).
    Shelves Central #SC4341 (16 Oct 2026, no Loaded supplier) was studied
    after that read, its notes were applied a minute later, and it was
    received on the first reading anyway: nothing read it again. A study that
    concludes no notes are needed leaves the instructions as they were, so
    the second read is a cache hit and costs nothing."""
    printed = extraction.get("supplier_name")
    if not printed or not _maybe_sensei(
        db, config_db, venue_id, invoice_id, str(printed)
    ):
        return extraction, None
    # The study wrote the supplier's notes in its own session.
    expire = getattr(config_db, "expire_all", None)
    if callable(expire):
        expire()
    return reread_under_printed_spec(
        db, config_db, lh, venue_id, file_id, detail, aliases, extraction
    )


def reread_under_printed_spec(
    db: Session,
    config_db: Session,
    lh,
    venue_id: str,
    file_id: object,
    detail: dict,
    aliases: list[str],
    extraction: dict,
) -> tuple[dict, object]:
    """Re-read the copy when the name PRINTED on it names a different layout.

    Pass 1 has no choice but to pick the layout from Loaded's supplier — the
    printed name does not exist until the copy has been read. Once it does it
    is the higher authority (the ordering ``resolve_supplier`` already states),
    and it is the only thing that catches an invoice filed in Loaded against
    the wrong business: a Neat Meat invoice sitting on the Coca Cola record was
    read with Coca Cola's prompt and stayed that way for good, because the
    extraction cache row is keyed to those very instructions.

    Returns the extraction to use and the spec it was read under. One re-read,
    never a loop, and only when the spec actually changes — measured at ~1% of
    invoices, so the second call costs almost nothing.
    """
    printed = extraction.get("supplier_name")
    if config_db is None:
        # No roster to consult, so nothing to re-select against. Best-effort,
        # like the alias fetch above — a review without a config DB is a
        # harness, not a venue.
        return extraction, None
    was = find_spec_for_supplier(config_db, detail.get("supplierName"), *aliases)
    if not printed:
        return extraction, was
    now = find_spec_for_supplier(config_db, printed)
    if now is None or (was is not None and str(now.id) == str(was.id)):
        return extraction, was
    # Loaded's supplier stays in the prompt's supplier-differs clause: it is
    # still a true statement about what Loaded records, and the clause is what
    # lets the model say the two disagree.
    second = extract_invoice_copy(
        db,
        lh,
        file_id,
        instructions=compose_pdf_instructions(
            config_db,
            loaded_supplier=detail.get("supplierName"),
            loaded_aliases=aliases,
            spec_notes=now.instructions or "",
            spec_name=now.name,
        ),
        venue_key=venue_id,
    )
    if not isinstance(second, dict) or second.get("error"):
        # A failed re-read must not lose the read we already have.
        logger.info(
            "second pass failed for '%s' (spec %s); keeping the first read",
            printed,
            now.name,
        )
        return extraction, was
    logger.info(
        "second pass: copy prints '%s' → re-read under spec '%s' (Loaded says '%s')",
        printed,
        now.name,
        detail.get("supplierName"),
    )
    return second, now


def _maybe_sensei(
    db: Session,
    config_db: Session,
    venue_id: str,
    invoice_id: str,
    supplier: str,
    *aliases: object,
) -> bool:
    """Train the sensei for a spec-less supplier, named as the copy prints it
    — fail-open, the review continues regardless. Returns whether training
    was attempted, so callers spend their budget on suppliers that actually
    needed it rather than on ones already covered. A name that names no
    business ('PTY LTD') is never studied: the layout would be named after it.

    ``aliases`` are not optional decoration: a global spec is filed under ONE
    canonical name and the account's spellings vary, so the bare feed name
    answers the wrong question. 'Kaans Catering' found no spec while
    "Kaan's Catering Supplies" sat there working, and the guard fell through —
    re-staging a dojo sample and re-analysing that spec, auto-apply live, on
    every new invoice. Ask with the same hints extraction uses.
    """
    from app.services.supplier_identity import identity_name

    try:
        if not identity_name(supplier):
            return False
        if find_spec_for_supplier(config_db, supplier, *aliases) is not None:
            return False
        from app.services import spec_dojo

        spec_dojo.train_supplier(
            db, venue_id, invoice_id, supplier, aliases=[str(a) for a in aliases]
        )
        return True
    except Exception as exc:  # noqa: BLE001 — sensei must never sink a review
        logger.warning("sensei training failed for %s: %s", supplier, exc)
        return True


# ---------------------------------------------------------------------------
# Batch — the consolidator's engine
# ---------------------------------------------------------------------------


def read_open_card(db, venue_id: str, invoice_id: str) -> tuple[dict | None, list]:
    """The invoice's open card, as copies of its data and PATCH history —
    (None, []) when it has none. The batch's session lets go of the cards:
    keep_review rewrites them through a session of its own, and this one must
    not write its stale copies back over that later
    (invalidate_conflicting_drafts commits through it)."""
    cards = open_drafts(db, venue_id, invoice_id)
    if not cards:
        return None, []
    previous = copy.deepcopy(cards[0].data or {})
    history = copy.deepcopy(cards[0].pending_ops or [])
    for c in cards:
        try:
            db.expunge(c)
        except Exception:  # noqa: BLE001 — not held by this session
            pass
    return previous, history


def review_invoices(
    db: Session,
    config_db: Session,
    venue_id: str,
    invoice_ids: list[str] | None = None,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    mode: str = "approve_all",
    require_valid_po: bool = True,
    settings: dict | None = None,
    on_invoice=None,
    should_stop=None,
    deadline: float | None = None,
) -> dict:
    """Review a batch of invoices; under ``mode="autopilot"`` auto-accept
    every suggestion (recorded, actor ``norm``) and receive the ones with no
    blocking issues.

    Long batches (14 Oct 2026 — 200 invoices ran past the chat's 25-minute
    cut-off and lost everything): ``on_invoice(invoice_id, record, data)`` is
    called as EACH invoice finishes (``data`` is its doc payload, or None when
    it couldn't be reviewed) so the caller saves it there and then;
    ``should_stop()`` (a cancel) and ``deadline`` (a ``time.monotonic()``
    value) are checked before each invoice, and the ones not reached come back
    in ``remaining`` for the next run.

    Mode policy (mirrors the consolidator's run modes):
    - ``approve_all`` / anything unknown: review + cards only, never receives.
    - ``approve_fixes``: receives only invoices that are READY with NOTHING to
      change (no suggestions at all); anything else becomes a card.
    - ``autopilot``: auto-accept all suggestions, receive when ready.

    Returns ``{"cards", "verdicts", "received", "skipped", "mode"}`` —
    cards are full replica_v2 doc payloads (they ride into ``fix_invoices``
    verbatim for the working-document fan-out).
    """
    from app.services import invoice_activity
    from app.services import venue_autopilot as VA
    from app.services.spec_dojo import prefetch_replica_reference

    # ``settings`` is the PERSON's receiving policy
    # (approvals.receiving_settings); none given is the safest one. The
    # caller's `mode` is a ceiling it can lower but never raise. Reviewing a
    # single invoice passes `approve_all` for exactly this reason — opening one
    # in the card must never write to Loaded — and a chat request cannot talk
    # anyone onto a rung they never chose.
    settings = VA.settings_from(settings)
    mode = VA.at_most(settings["mode"], mode)
    settings["mode"] = mode
    if settings[VA.RECEIVE_WITHOUT_PO]:
        # The person has said a missing order is not a reason to stop. The
        # per-task override stays honoured for anyone who set it.
        require_valid_po = False

    lh = LoadedInvoiceClient(db, config_db, venue_id)

    if invoice_ids is None:
        invoice_ids = [
            r["id"]
            for r in list_window_invoices(lh, from_date=from_date, to_date=to_date)
        ]

    import time as _time

    level = invoice_activity.level_of(settings)

    def _note(iid: str, kind: str, record: dict, data: dict | None) -> None:
        # The invoice activity page's record of an invoice that neither went
        # in nor was deleted: stopped (and why), waiting, or failed.
        invoice_activity.record_stop(
            venue_id=venue_id,
            invoice_id=iid,
            kind=kind,
            data=data,
            mode=mode,
            reasons=record.get("reasons"),
            gates_needed=record.get("gates_needed"),
            level=level,
            record=record,
        )

    def _report(iid: str, record: dict, data: dict | None) -> None:
        if on_invoice is None:
            return
        try:
            on_invoice(iid, record, data)
        except Exception:  # noqa: BLE001 — saving one never sinks the batch
            logger.exception("on_invoice failed for %s", iid)

    cards: list[dict] = []
    verdicts: list[dict] = []
    received: list[dict] = []
    skipped: list[dict] = []
    remaining: list[str] = []
    stopped: str | None = None
    # One invoice after another, each exactly as the receive card reviews one
    # (16 Oct 2026, the owner: "simplicity over efficiency"): fetch it, fetch
    # the venue's reference data fresh — what the last invoice created in
    # Loaded is in it — and review. A supplier with no spec is studied as soon
    # as its first copy is read, so what Norm learns serves the next invoice.
    order = [str(i) for i in invoice_ids]
    for n, iid in enumerate(order):
        if should_stop is not None and should_stop():
            stopped = "cancelled"
        elif deadline is not None and _time.monotonic() > deadline:
            stopped = "out of time"
        if stopped:
            remaining = order[n:]
            break
        try:
            det = lh.invoice(iid)
        except Exception as exc:  # noqa: BLE001 — one bad invoice never sinks the batch
            logger.warning("invoice %s unavailable: %s", iid, exc)
            gone = {
                "invoice_id": iid,
                "outcome": "could not fetch from Loaded",
                "reasons": [f"could not fetch this invoice from Loaded: {exc}"],
            }
            skipped.append(gone)
            _note(iid, "failed", gone, None)
            _report(iid, gone, None)
            continue
        try:
            data = review_invoice(
                db,
                config_db,
                venue_id,
                iid,
                lh=lh,
                detail=det,
                reference=prefetch_replica_reference(db, config_db, venue_id),
                require_valid_po=require_valid_po,
                allow_sensei=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("review failed for %s: %s", iid, exc)
            failed = {
                "invoice_id": iid,
                "reference_number": det.get("referenceNumber"),
                "supplier_name": det.get("supplierName"),
                "outcome": "review failed",
                "reasons": [str(exc)],
            }
            skipped.append(failed)
            _note(iid, "failed", failed, None)
            _report(iid, failed, None)
            continue

        # What a person decided or typed on the invoice's open card is part
        # of what gets received (owner, 19 Oct 2026: Loaded + accepted
        # suggestions + anything a person typed). The fresh review carries it
        # exactly as the card's own rebuild does, and Autopilot then leaves
        # those fields alone.
        try:
            from app.services.invoice_edits import rebuild_from_decisions

            previous, history = read_open_card(db, venue_id, iid)
            if previous is not None:
                rebuild_from_decisions(previous, data, previous_ops=history)
        except Exception as exc:  # noqa: BLE001 — the review stands on its own
            logger.warning("the open card for %s couldn't be read: %s", iid, exc)

        gated: list[str] = []
        if mode == "autopilot":
            # What the Replica is sure of, plus what the person's switches
            # allow; every other blocker stands, so the invoice parks with its
            # reason on it.
            gated = autopilot_accept(
                db, config_db, venue_id, iid, data, settings, lh=lh
            )
            from app.services.invoice_decisions import annotate

            annotate(data)
            if data.get("is_deleted"):
                # A gated delete removed the draft — terminal; there is
                # nothing left to receive or card.
                deleted = {
                    "invoice_id": iid,
                    "reference_number": data.get("reference_number"),
                    "supplier_name": data.get("supplier_name"),
                    "outcome": "deleted",
                    "reasons": gated,
                    "deleted_kind": data.get("deleted_kind"),
                }
                received.append(deleted)
                # Recorded like a receive, with its reason: a delete used to
                # leave no trace but the count, so "were these right to go?"
                # (Little West, 84 of 222, 16 Oct 2026) meant re-reading every
                # document. The report leaves these rows out of its rates.
                invoice_activity.keep_review(
                    venue_id=venue_id, invoice_id=iid, data=data
                )
                record_receive_outcome(
                    db,
                    venue_id=venue_id,
                    invoice_id=iid,
                    data=data,
                    mode=mode,
                    actor="norm",
                    received=False,
                    outcome_override="deleted",
                    level=level,
                    extra_detail={
                        "deleted": {
                            "kind": data.get("deleted_kind"),
                            "reason": data.get("deleted_reason"),
                        }
                    },
                )
                _report(iid, deleted, data)
                continue

        standing = [
            i
            for i in data.get("issues") or []
            if i.get("blocking") and not is_settled(data, i)
        ]
        blocking = [i.get("message") for i in standing]
        # Which toggle would have let each remaining blocker through. This is
        # the answer to "why didn't this receive?", and it is the difference
        # between a venue climbing the ladder on evidence and guessing.
        gates_wanted = sorted({i.get("gate") for i in standing if i.get("gate")})
        verdict = {
            "invoice_id": iid,
            "reference_number": data.get("reference_number"),
            "supplier_name": data.get("supplier_name"),
            "po_number": data.get("purchase_order_number"),
            "total": data.get("total"),
            "confidence": data.get("confidence"),
            "suggestions": len(data.get("suggestions") or []),
            "reasons": blocking,
            "gates_needed": gates_wanted,
            "created": gated,
        }

        should_receive = (
            mode == "autopilot" and data.get("confidence") == "ready"
        ) or (
            mode == "approve_fixes"
            and data.get("confidence") == "ready"
            and not data.get("suggestions")
        )
        if should_receive:
            try:
                out = do_receive(lh, receive_request_from_doc(data, venue_id, iid))
                data["is_received"] = True
                data["status"] = "received"
                received.append({**verdict, "outcome": "received"})
                # The units this receive actually used → the catalogue's
                # practice tier (advisory; the divergence detector).
                from app.services import supplier_catalog

                supplier_catalog.observe_practice_from_doc(config_db, data)
                # The review as received, so its card can be opened later.
                invoice_activity.keep_review(
                    venue_id=venue_id, invoice_id=iid, data=data
                )
                # Norm's own receives are self-fulfilling (auto_accept_all ran
                # a moment ago), so they measure VOLUME, not correctness — the
                # report keeps actor="norm" out of its readiness rates.
                record_receive_outcome(
                    db,
                    venue_id=venue_id,
                    invoice_id=iid,
                    data=data,
                    mode=mode,
                    actor="norm",
                    level=level,
                )
                # A bill in Xero when the person's receiving settings say so
                # (Settings → Preferences). Best effort; the receive stands.
                from app.services.xero_bills import push_bill_after_receive

                push_bill_after_receive(
                    db,
                    config_db,
                    venue_id,
                    iid,
                    data,
                    enabled=bool(settings.get(VA.CREATE_XERO_BILL)),
                )
                invalidate_conflicting_drafts(
                    db,
                    venue_id,
                    iid,
                    reference_number=data.get("reference_number"),
                    po_ids=(
                        data.get("linked_purchase_order_id"),
                        data.get("purchase_order_number"),
                    ),
                )
                logger.info("autopilot received %s: %s", iid, out)
                _report(iid, {**verdict, "outcome": "received"}, data)
            except Exception as exc:  # noqa: BLE001 — a failed write is a skip
                verdict["reasons"] = [f"receive failed: {exc}"]
                skipped.append({**verdict, "outcome": "receive failed"})
                cards.append(data)
                _note(iid, "failed", verdict, data)
                _report(iid, {**verdict, "outcome": "receive failed"}, data)
        else:
            outcome = (
                "ready to receive — awaiting approval"
                if data.get("confidence") == "ready"
                else "needs review"
            )
            skipped.append({**verdict, "outcome": outcome})
            cards.append(data)
            _note(
                iid,
                "waiting" if data.get("confidence") == "ready" else "stopped",
                verdict,
                data,
            )
            _report(iid, {**verdict, "outcome": outcome}, data)
        verdicts.append(verdict)

    return {
        "cards": cards,
        "verdicts": verdicts,
        "received": received,
        "skipped": skipped,
        # The rung actually applied — the venue's setting, lowered by the
        # caller's ceiling — so the report can say what really governed it.
        "mode": mode,
        # Invoices not reached (cancelled, or out of time): the next run's.
        "remaining": remaining,
        "stopped": stopped,
    }


def list_window_invoices(
    lh, *, from_date: str | None = None, to_date: str | None = None
) -> list[dict]:
    """The venue's unreceived invoices in a date window, newest first, as
    ``{id, supplier_name, reference_number, date, total}`` — what a batch
    reviews, and what a background job counts and works through."""
    from app.services.received_invoice import outstanding_invoice_rows

    rows = outstanding_invoice_rows(lh)
    out = [
        {
            "id": str(r["id"]),
            "supplier_name": r.get("supplierName"),
            "reference_number": r.get("referenceNumber"),
            "date": r.get("issuedAt") or r.get("createdAt"),
            "total": r.get("total"),
        }
        for r in rows
        if isinstance(r, dict)
        and r.get("id")
        and not r.get("isReceived")
        and not r.get("deletedAt")
        and _in_window(r.get("createdAt") or r.get("issuedAt"), from_date, to_date)
    ]
    out.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
    return out


def _in_window(created: object, from_date: str | None, to_date: str | None) -> bool:
    d = _date_part(created)
    if from_date and d and d < _date_part(from_date):
        return False
    if to_date and d and d > _date_part(to_date):
        return False
    return True
