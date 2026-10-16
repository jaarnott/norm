"""The invoice review service — the Replica as the single engine.

Four views of one invoice, and the card's tabs are exactly these:

- **Extracted** — what the AI read off the supplier's invoice, untouched.
- **Loaded** — what Loaded's Receive screen shows.
- **Replica** — what we think the invoice says: the extraction resolved
  against the catalogue (``invoice_replica.build_replica``), then finished by
  every rule Norm has (``replica_finish``). Final; nothing overrides it later.
- **Norm** — the working values: they start as Loaded, and every place the
  Replica differs is ONE suggestion to accept. Nothing else makes one.

The pipeline, one for every surface (web editor, embedded card, chat batch):

1. Shape the WORKING VALUES from Loaded's draft, opening where Loaded's
   screen opens (``seed_working_from_loaded``).
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
    pdf_instructions_for,
)
from app.services.autopilot_metrics import record_receive_outcome
from app.services.invoice_po_reference import (
    attach_po_reference,
    enrich_loaded_snapshot,
    fetch_po_reference,
    loaded_reference,
    po_id_for,
    seed_working_from_loaded,
)
from app.services.invoice_replica import build_replica
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
        # code match (seed_working_from_loaded), so this is the only place a
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
            apply={
                "linked_item_id": rl.get("linked_item_id"),
                "item_name": rl.get("item_name"),
                **(
                    {"linked_brand_id": rl.get("linked_brand_id")}
                    if rl.get("linked_brand_id") and not ln.get("linked_brand_id")
                    else {}
                ),
            },
        )
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


def brand_issues(data: dict, issues: list[dict]) -> None:
    """A brand Loaded names but has no record for STOPS the receive.

    `do_receive` refuses such a line — Loaded won't take it — but nothing said
    so before the attempt: there was a create_brand suggestion and no issue, so
    `confidence` read "ready", autopilot called do_receive and collected a 400,
    and the card showed the brand in three places while its "Blocked from auto
    receive" list stayed empty. Bidfood 109944512 sat unreceivable on exactly
    this. The create still happens from here — `fold_remedies_into_blockers`
    hangs the action off this row.
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
        "fixes_confidence",
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
        rid = str(rl.get("id"))
        desc = rl.get("description") or rl.get("code") or "line"
        payload = {k: v for k, v in rl.items() if k not in _REPLICA_ONLY_KEYS}
        explanation = (
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
        )
        # What the added line still needs, as its own decisions on that line.
        _unit_suggestion(suggestions, rl, None, rid, desc)
        _create_item_suggestion(suggestions, rl, rid, desc)

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

    _brand_suggestions(data, suggestions)
    _header_suggestions(data, replica, suggestions)
    return suggestions, issues, pairs


def _brand_suggestions(data: dict, suggestions: list[dict]) -> None:
    """A brand Loaded names on a line but has no record for: create it.
    Over every line, paired or not — an unresolved brand stops the receive
    whatever the invoice says (``replica_finish.settle_brands``)."""
    for ln in data.get("lines") or []:
        if not isinstance(ln, dict) or ln.get("struck"):
            continue
        brand = str(ln.get("brand") or "").strip()
        if not brand or ln.get("linked_brand_id"):
            continue
        _sugg(
            suggestions,
            "create_brand",
            field="linked_brand_id",
            line_id=str(ln.get("id")),
            current=None,
            proposed=brand,
            explanation=(
                f"'{brand}' isn't a brand in Loaded — create it "
                "(Loaded won't receive a line naming a brand it doesn't have)"
            ),
            payload={"brand_name": brand},
            gate=_VA.AUTO_CREATE_BRANDS,
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
    if replica.get("supplier_create_name") and not data.get("linked_supplier_id"):
        name = replica["supplier_create_name"]
        explanation = (
            replica.get("supplier_why")
            or f"no Loaded supplier is called '{name}' — create it"
        )
        payload = {"supplier_name": name}
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
            current=None,
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
    doubled = rep_issues.get("po_doubled_up")
    if doubled:
        d = doubled.get("data") or {}
        _sugg(
            suggestions,
            "unlink_po",
            field="purchase_order_number",
            current=data.get("purchase_order_number"),
            proposed=None,
            explanation=(
                f"order {d.get('order_number')} is already fully invoiced by "
                f"{d.get('sibling_reference')} — remove the reference"
            ),
            apply={
                "purchase_order_number": None,
                "linked_purchase_order_id": None,
                "po_unlinked": bool(data.get("linked_purchase_order_id")),
            },
            # A judgement, not a create: no switch authorises it.
            sure=False,
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
    "unit_unknown": _VA.RECEIVE_WITHOUT_UNIT,
    # A unit Loaded supplied but the copy never confirmed. name_the_unit_in_use
    # re-points unit_missing here too when the working line already has one.
    "unit_unconfirmed": _VA.RECEIVE_WITH_UNCONFIRMED_UNIT,
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
    nothing extra."""
    if ask is None and db is None:
        return
    from app.services import invoice_arbiter

    post = _all_applied(data, suggestions)
    _, _, came_to, declared = _totals_view(post)
    printed = {wid: _line(replica, rid) or {} for rid, wid in pairs.items()}
    live = [
        ln
        for ln in post.get("lines") or []
        if isinstance(ln, dict) and not ln.get("struck")
    ]
    # GST "charged" is the rate the invoice's lines already carry.
    gst = max((_f(ln.get("sale_tax_rate")) or 0.0 for ln in live), default=0.0)
    lines = []
    for ln in live:
        lid = str(ln.get("id"))
        rl = printed.get(lid) or {}
        lines.append(
            {
                "id": lid,
                "description": ln.get("description"),
                "code": ln.get("code"),
                "quantity": _f(ln.get("quantity_received")),
                "unit_price": _f(ln.get("unit_cost")),
                "gst_rate": _f(ln.get("sale_tax_rate")),
                "printed": {
                    "quantity": rl.get("quantity_received"),
                    "unit_price": rl.get("unit_cost"),
                    "line_total": rl.get("total_cost"),
                }
                if rl
                else None,
            }
        )

    def with_answer(answer: dict) -> list[dict]:
        trial = copy.deepcopy(suggestions)
        by_id = {str(s.get("id")): s for s in trial}
        why = (
            f"with every other suggestion applied the invoice comes to "
            f"{came_to:.2f}, not its total {declared:.2f} — {answer.get('why')}"
        )
        changes = [
            (str(c["line_id"]), field, value)
            for c in answer.get("lines") or []
            for field, value in (
                ("quantity_received", c.get("quantity")),
                ("unit_cost", c.get("unit_price")),
                (
                    "sale_tax_rate",
                    {"charged": gst or None, "free": 0.0}.get(c.get("gst")),
                ),
            )
            if value is not None
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
    )
    if answer:
        suggestions[:] = with_answer(answer)
    else:
        logger.warning(
            "totals don't balance and Norm couldn't work out the lines on %s",
            data.get("invoice_id"),
        )


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
) -> None:
    """The totals check, on the values the suggestions leave — not the copy's
    own printed figures (the user, 14 Oct 2026: "the goal is autoreceive so we
    need to make sure that after all suggestions are applied the totals
    always match, that should be our check"). In place.

    - Accepting every suggestion must come to the total. If it wouldn't,
      Norm works out which lines are wrong (``_work_out_the_lines``) — never
      an adjustment line, never the difference spread over every price (the
      owner, 16 Oct 2026: "it should work out the lines"; the spread cut 64
      Applegum invoices' costs to force a GST mismatch).
    - The totals blocker stands whenever the WORKING values don't come to the
      total, and clears only when they do (``clears_when`` ``balances``) —
      however they got there. Its fix is every suggestion that moves the
      money. Autopilot applies only sure suggestions (and guesses whose switch
      is on), so a check on "everything applied" alone would let it receive
      an invoice that doesn't add up; and Fresh Connection 30049130 read
      "Ready to receive" at $138.32 on a $179.40 invoice because accepting the
      fixes used to be enough to clear the blocker."""
    if any(s.get("kind") == "delete_invoice" for s in suggestions):
        return  # nothing will be received
    existing = next((i for i in issues if i.get("code") == TOTALS_ISSUE), None)
    issue_id = (existing or {}).get("id") or TOTALS_ISSUE

    if not totals_balance(_all_applied(data, suggestions)):
        if totals_balance(data):
            # Loaded's figures add up as they stand: the suggestions that
            # would break them go, and Loaded's figures stay.
            suggestions[:] = [s for s in suggestions if not _moves_money(data, s)]
        else:
            _work_out_the_lines(
                data,
                suggestions,
                issue_id,
                replica or {},
                pairs or {},
                ask,
                db,
                document,
            )

    if totals_balance(data):
        # As it stands it adds up: nothing to stop the receive.
        if existing is not None:
            issues.remove(existing)
        for s in suggestions:
            if s.get("fixes") == issue_id:
                s["fixes"] = None
        return

    _, _, came_to, declared = _totals_view(data)
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
        clears = _LINE_ISSUE_CLEARS.get(i.get("code"))
        if clears and i.get("line_id"):
            i["clears_when"] = {
                "scope": "line",
                "line_id": i["line_id"],
                "field": clears[0],
                "op": clears[1],
            }
        if i.get("code") == "supplier_unresolved":
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
#: before anything that hangs off it, a unit before the item that needs it,
#: the brand last.
LOADED_WRITE_ORDER = ("create_supplier", "create_unit", "create_item", "create_brand")

#: Blockers a venue switch waves through on its own: the switch IS the answer
#: ("receive without an order"), so nothing has to be applied for it to stand
#: down.
_SWITCH_SETTLES = ("po_missing", "po_unresolved", "unit_unconfirmed")
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
        return same_line and kind == "create_brand"
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
    if code == "po_doubled_up":
        return kind == "unlink_po"
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
        desc = ln.get("item_name") or ln.get("description") or ln.get("code") or "line"
        field, proposed = "unit", ln.get("unit")
        unit = ln.get("unit")
        # Say where the unit came from. The replica stamps the tier behind its
        # stand-in, but the stamp only holds while the line shows THAT unit —
        # any other is Loaded's own (HIGHLAND PARK 15, 4366904, 19 Aug 2026:
        # "Loaded's 700 mL" was Norm's catalogue answering).
        d = issue.get("data") or {}
        source = d.get("unit_chosen_by")
        if d.get("unit_id") and d.get("unit_id") != ln.get("linked_unit_id"):
            source = None
        said = {
            "copy": f"the invoice says '{unit}'; keep it",
            "catalogue": f"Norm's catalogue says '{unit}'; keep it",
            "printed": f"the printed column says '{unit}'; keep it",
        }.get(source, f"keep Loaded's '{unit}'")
        explanation = f"the invoice doesn't confirm the unit for '{desc}' — {said}"
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

    A unit decision takes the blocker's switch: picking a unit, or keeping
    one the invoice doesn't confirm, is exactly what "receive without a unit"
    / "receive with an unconfirmed unit" authorise Norm to do alone."""
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
                ln = _line(data, issue.get("line_id"))
                issue["gate"] = (
                    _VA.RECEIVE_WITH_UNCONFIRMED_UNIT
                    if (ln or {}).get("linked_unit_id")
                    else _VA.RECEIVE_WITHOUT_UNIT
                )
            for s in fix:
                if s.get("kind") in ("line_value", "confirm") and not s.get("gate"):
                    s["gate"] = issue.get("gate")
        elif issue.get("code") in _SWITCH_SETTLES:
            for s in fix:
                if s.get("kind") == "confirm":
                    s["gate"] = issue.get("gate")
        elif issue.get("code") == "supplier_unresolved" and not any(
            s.get("kind") == "create_supplier" for s in fix
        ):
            # Its answer is an existing record that may be the same business —
            # a person's call. "Auto-create suppliers" can't get past it:
            # autopilot never creates a supplier something plausibly matches.
            issue["gate"] = None
        issue["fix"] = [s["id"] for s in fix]


def blockers_without_a_fix(data: dict) -> list[dict]:
    """Blocking issues with nothing for Accept to do. While a "delete this
    draft" stands the rest are moot — the delete is the answer. Logged on
    every review; empty is the contract."""
    if any(
        isinstance(s, dict) and s.get("kind") == "delete_invoice"
        for s in data.get("suggestions") or []
    ):
        return []
    return [
        i
        for i in data.get("issues") or []
        if isinstance(i, dict) and i.get("blocking") and not i.get("fix")
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


def is_settled(data: dict, issue: dict, last: dict[str, dict] | None = None) -> bool:
    """A blocker stands down when its predicate holds against the working
    values, when a person decided it ("I've checked this"), or when every
    suggestion that is its fix was accepted. The card's ``issueSettled`` is
    the same rule."""
    if _clears(data, issue):
        return True
    last = _last_actions(data) if last is None else last
    if (last.get(str(issue.get("id"))) or {}).get("action") in (
        "accepted",
        "dismissed",
    ):
        return True
    if (issue.get("clears_when") or {}).get("op") == "balances":
        return False  # accepted fixes count only if the totals now match
    fix = issue.get("fix") or []
    return bool(fix) and all(
        (last.get(str(f)) or {}).get("action") == "accepted" for f in fix
    )


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
        data.setdefault("lines", []).append(payload)
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
    """
    if not isinstance(previous, dict) or not isinstance(fresh, dict):
        return 0
    prior_actions = _last_actions(previous)
    if not prior_actions:
        return 0

    prior_suggestions = {
        str(s.get("id")): s
        for s in previous.get("suggestions") or []
        if isinstance(s, dict) and s.get("id")
    }
    actions = fresh.setdefault("suggestion_actions", [])
    carried_ids: set[str] = set()

    def _same_question(before: dict, now: dict) -> bool:
        return (before.get("apply") or {}) == (now.get("apply") or {}) and (
            before.get("payload") or {}
        ) == (now.get("payload") or {})

    def _carry(s: dict, action: dict) -> None:
        if action.get("action") == "accepted":
            apply_suggestion(fresh, s)
        actions.append({**action, "suggestion_id": s.get("id")})
        carried_ids.add(str(s.get("id")))

    fresh_suggestions = [
        s for s in fresh.get("suggestions") or [] if isinstance(s, dict)
    ]
    for s in fresh_suggestions:
        sid = str(s.get("id") or "")
        action = prior_actions.get(sid)
        before = prior_suggestions.get(sid)
        if not action or action.get("action") == "undone":
            continue
        if before is None or not _same_question(before, s):
            continue
        _carry(s, action)

    by_id = {str(s.get("id")): s for s in fresh_suggestions}
    for issue in fresh.get("issues") or []:
        iid = str(issue.get("id") or "")
        action = prior_actions.get(iid)
        if not action or action.get("action") == "undone":
            continue
        after = action.get("after") or {}
        if action.get("action") == "accepted":
            for fid in issue.get("fix") or []:
                s = by_id.get(str(fid))
                if s is None or str(fid) in carried_ids:
                    continue
                apply = s.get("apply") or {}
                shared = [k for k in after if k in apply]
                if s.get("kind") == "confirm" and not after:
                    _carry(s, action)
                elif shared and all(apply[k] == after[k] for k in shared):
                    _carry(s, action)
        # A waved blocker ("I have checked this") is a decision too — carried
        # while the same blocker is still raised.
        if iid not in carried_ids:
            actions.append(dict(action))
            carried_ids.add(iid)
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
    switches allow. Returns what it did, in words, for the run's verdict.

    - a suggestion with a ``gate`` (a create, a delete, a phantom strike, a
      unit pick) only when that switch is on;
    - any other suggestion only when the Replica is sure of it — a guess (a
      likely order, the closest supplier)
      always waits for a person;
    - then any blocker a switch waves through on its own (``_SWITCH_SETTLES``).

    Order: a delete first (nothing else matters once the draft is gone), then
    the draft edits (an added line must exist before anything is created on
    it), then the Loaded creates in ``LOADED_WRITE_ORDER``. The creates run
    through services/invoice_catalog — the same functions as the card's
    buttons, so autopilot and a person make byte-identical Loaded writes. A
    create that fails leaves its blocker standing: the invoice parks instead
    of receiving half-built.
    """
    from app.routers import invoice_fixes as IF
    from app.services import invoice_catalog as catalog
    from app.services import supplier_identity

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    actions = data.setdefault("suggestion_actions", [])
    done: list[str] = []
    last = _last_actions(data)

    def _client():
        nonlocal lh
        if lh is None:
            lh = LoadedInvoiceClient(db, config_db, venue_id)
        return lh

    def _record(sid: object, before: dict, after: dict) -> None:
        actions.append(
            {
                "suggestion_id": sid,
                "action": "accepted",
                "by": "norm",
                "at": now,
                "before": before,
                "after": after,
            }
        )

    def _allowed(s: dict) -> bool:
        if s.get("gate"):
            return _VA.gate_open(settings, s.get("gate"))
        return s.get("sure", True) is not False

    pending = [
        s
        for s in data.get("suggestions") or []
        if isinstance(s, dict)
        and (last.get(str(s.get("id"))) or {}).get("action")
        not in ("accepted", "dismissed")
        and _allowed(s)
    ]

    for s in pending:
        if s.get("kind") != "delete_invoice":
            continue
        payload = s.get("payload") or {}
        try:
            IF._apply_delete_invoice(_client(), payload, db)
        except Exception as exc:  # noqa: BLE001 — parks instead of half-done
            logger.warning("gated delete failed on %s: %s", invoice_id, exc)
            continue
        data["is_deleted"] = True
        data["status"] = "deleted"
        # Why, kept on the draft (the card's Delete does the same) so the run
        # can say what its deletes were — not just how many.
        data["deleted_reason"] = payload.get("summary") or s.get("explanation")
        data["deleted_kind"] = payload.get("reason") or "other"
        _record(s.get("id"), {}, {"deleted": True})
        done.append(f"deleted the draft ({data['deleted_reason']})")
        return done

    # add_line first: a later edit or create may be on the line it adds.
    edits = sorted(
        (
            s
            for s in pending
            if s.get("kind") not in LOADED_WRITE_ORDER
            and s.get("kind") != "delete_invoice"
        ),
        key=lambda s: s.get("kind") != "add_line",
    )
    for s in edits:
        before = apply_suggestion(data, s)
        if before is None:
            continue
        _record(
            s.get("id"),
            before,
            s.get("apply") or ({"added": True} if s.get("kind") == "add_line" else {}),
        )
        if s.get("gate"):
            if s.get("kind") == "strike":
                ln = _line(data, s.get("line_id")) or {}
                done.append(
                    f"struck '{ln.get('description') or ln.get('code') or '?'}' "
                    "(not billed on the invoice)"
                )
            elif s.get("kind") == "confirm":
                done.append(f"accepted ({str(s.get('gate')).replace('_', ' ')})")
            elif s.get("field") == "unit":
                done.append(f"set unit '{s.get('proposed')}' ({s.get('gate')})")

    # What this Accept made NEW in Loaded (a get-or-create that found an
    # existing record made nothing) — the run's summary counts these.
    created: dict[str, list[str]] = {"stock_items": [], "suppliers": [], "units": []}
    data["autopilot_created"] = created
    for kind in LOADED_WRITE_ORDER:
        for s in pending:
            if s.get("kind") != kind:
                continue
            payload = s.get("payload") or {}
            ln = _line(data, s.get("line_id")) if s.get("line_id") else None
            try:
                if kind == "create_supplier":
                    # The riskiest create by a distance: supplier identity
                    # picks the extraction layout, so a second 'SERVICE FOODS'
                    # sends every later invoice from that business to a prompt
                    # written for someone else (10 Aug 2026). Autopilot creates
                    # only a supplier nothing plausibly matches — past the
                    # close names Norm checked and ruled out (``not_these``).
                    name = payload.get("supplier_name") or ""
                    rows = _client().get("/1.0/stock/internal/suppliers")
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
                        for m in supplier_identity.plausible_matches(name, live)
                        if str(m.get("id")) not in ruled_out
                    ]:
                        logger.info(
                            "autopilot will not create supplier '%s' on %s: an "
                            "existing supplier is a plausible match",
                            name,
                            invoice_id,
                        )
                        continue
                    supplier, new = catalog.get_or_create_supplier(
                        _client(), name, details=payload.get("details")
                    )
                    data["linked_supplier_id"] = supplier["id"]
                    data["supplier_name"] = supplier.get("name") or name
                    _record(s.get("id"), {}, {"linked_supplier_id": supplier["id"]})
                    if new:
                        created["suppliers"].append(supplier.get("name") or name)
                    verb = "created" if new else "linked existing"
                    done.append(f"{verb} supplier '{supplier.get('name')}'")
                elif kind == "create_unit" and ln is not None:
                    unit, new = catalog.get_or_create_unit(
                        _client(), payload.get("unit_name") or "", db
                    )
                    ln["linked_unit_id"] = unit.get("id")
                    ln["unit"] = unit.get("name")
                    if unit.get("ratio") is not None:
                        ln["unit_ratio"] = unit.get("ratio")
                    _record(s.get("id"), {}, {"linked_unit_id": unit.get("id")})
                    if new:
                        created["units"].append(unit.get("name"))
                    verb = "created" if new else "used existing"
                    done.append(f"{verb} unit '{unit.get('name')}'")
                elif kind == "create_item" and ln is not None:
                    out = catalog.create_item(
                        _client(),
                        db,
                        config_db,
                        venue_id=venue_id,
                        invoice_id=invoice_id,
                        line_id=str(s.get("line_id")),
                        name=payload.get("name"),
                        group_id=payload.get("group_id") or "",
                        unit_id=ln.get("linked_unit_id"),
                        brand_id=ln.get("linked_brand_id"),
                        supplier_id=data.get("linked_supplier_id"),
                        doc_data=data,
                    )
                    ln["linked_item_id"] = out["item_id"]
                    ln["item_name"] = out["item_name"]
                    _record(s.get("id"), {}, {"linked_item_id": out["item_id"]})
                    if out["created"]:
                        created["stock_items"].append(out["item_name"])
                    verb = "created" if out["created"] else "linked existing"
                    done.append(f"{verb} stock item '{out['item_name']}'")
                elif kind == "create_brand" and ln is not None:
                    brand, _ = catalog.get_or_create_brand(
                        _client(), payload.get("brand_name") or ""
                    )
                    ln["linked_brand_id"] = brand["id"]
                    _record(s.get("id"), {}, {"linked_brand_id": brand["id"]})
                    done.append(f"created brand '{brand['name']}'")
            except Exception as exc:  # noqa: BLE001 — the blocker simply stands
                logger.info(
                    "autopilot %s could not run on %s: %s", kind, invoice_id, exc
                )

    last = _last_actions(data)
    for issue in data.get("issues") or []:
        if not issue.get("blocking") or issue.get("code") not in _SWITCH_SETTLES:
            continue
        if not _VA.gate_open(settings, issue.get("gate")):
            continue
        if is_settled(data, issue, last):
            continue
        _record(issue.get("id"), {}, {})
        done.append(f"accepted ({str(issue.get('gate')).replace('_', ' ')})")
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
    # code, so those resolved values ARE Loaded's position. Seeding them here —
    # before a single suggestion is computed — is what makes a suggestion mean
    # "the copy disagrees with Loaded" instead of "Loaded's payload is raw".
    # Safe on this path: the review replaces the payload wholesale anyway.
    # Three independent best-effort steps, each guarded on its own: a failed
    # order fetch must not cost us the mirror, and vice versa.
    catalogue = (reference or {}).get("catalogue")
    units = (reference or {}).get("units")
    try:
        if not catalogue:
            catalogue, units = loaded_reference(venue_id, db, lh, config_db=config_db)
        seed_working_from_loaded(data, catalogue=catalogue, units=units)
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
    if not file_id and not feed:
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
        # One alias fetch feeds both the sensei's guard and the cache key; the
        # supplier list vets those aliases and is reused by the replica below.
        suppliers = account_suppliers(lh)
        aliases = supplier_aliases(lh, detail, suppliers)
        if allow_sensei and file_id and detail.get("supplierName"):
            _maybe_sensei(
                db, config_db, venue_id, invoice_id, detail["supplierName"], *aliases
            )
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
            if file_id:
                extraction, spec_used = reread_under_printed_spec(
                    db, config_db, lh, venue_id, file_id, detail, aliases, extraction
                )
            # The supplier the COPY names may be one Norm has never seen, even
            # when Loaded's name matched something. Ask the roster about the
            # printed name too, or a business filed under someone else's Loaded
            # record is never trained.
            printed = extraction.get("supplier_name")
            if allow_sensei and file_id and printed and spec_used is None:
                _maybe_sensei(db, config_db, venue_id, invoice_id, str(printed))
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
            # The venue's unit chooser — the last answer for a line nothing
            # gives a unit (replica_finish.settle_units). Its final pass may
            # not decline: a line with no unit stops autopilot outright.
            choose = None
            if db is not None:
                from app.services.unit_guess import guess_unit, llm_chooser

                ask = llm_chooser(db)
                insist = llm_chooser(db, insist=True)

                def choose(ln: dict) -> dict | None:
                    unit, why = guess_unit(ln, units or [], ask_llm=ask, insist=insist)
                    return {**unit, "why": why} if unit else None

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
                    choose_unit=choose,
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
            # A unit the invoice names but Loaded lacks, on a line that holds a
            # variant default: without its own blocker autopilot received with
            # the default instead of the unit on the paper.
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
                    i.get("code")
                    in ("po_unresolved", "po_split_order", "po_doubled_up")
                    for i in issues
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
            )
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
    data["confidence"] = compute_confidence(data)
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


def _with_suppliers(reference: dict | None, suppliers: list[dict]) -> dict:
    """The replica's reference bundle, carrying the supplier list we already
    fetched to vet the aliases — so Loaded is asked for it once, not twice. A
    caller that supplied its own list keeps it."""
    ref = dict(reference or {})
    if ref.get("suppliers") is None:
        ref["suppliers"] = suppliers
    return ref


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
    """Train the sensei for a spec-less supplier BEFORE extraction (the
    engine's ordering rule) — fail-open, the review continues regardless.
    Returns whether training was attempted, so callers spend their budget on
    suppliers that actually needed it rather than on ones already covered.

    ``aliases`` are not optional decoration: a global spec is filed under ONE
    canonical name and the account's spellings vary, so the bare feed name
    answers the wrong question. 'Kaans Catering' found no spec while
    "Kaan's Catering Supplies" sat there working, and the guard fell through —
    re-staging a dojo sample and re-analysing that spec, auto-apply live, on
    every new invoice. Ask with the same hints extraction uses.
    """
    try:
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
    # Loaded is in it — and review. A supplier with no spec is studied before
    # its invoice is read, so what Norm learns serves the next invoice.
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
            _report(iid, failed, None)
            continue

        gated: list[str] = []
        if mode == "autopilot":
            # What the Replica is sure of, plus what the person's switches
            # allow; every other blocker stands, so the invoice parks with its
            # reason on it.
            gated = autopilot_accept(
                db, config_db, venue_id, iid, data, settings, lh=lh
            )
            data["confidence"] = compute_confidence(data)
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
                record_receive_outcome(
                    db,
                    venue_id=venue_id,
                    invoice_id=iid,
                    data=data,
                    mode=mode,
                    actor="norm",
                    received=False,
                    outcome_override="deleted",
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
                _report(iid, {**verdict, "outcome": "receive failed"}, data)
        else:
            outcome = (
                "ready to receive — awaiting approval"
                if data.get("confidence") == "ready"
                else "needs review"
            )
            skipped.append({**verdict, "outcome": outcome})
            cards.append(data)
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
