"""The invoice review service — the replica as the single suggestion engine.

One pipeline for every surface (web editor, embedded card, chat batch):

1. Fetch the live Loaded draft and shape the WORKING VALUES from it
   (``build_received_invoice_data`` — exactly what the user sees and edits).
2. Extract the invoice copy (cached, ``invoice_extraction``).
3. Build the replica (``invoice_replica.build_replica``) — Norm's own full
   resolution of the same paper, stored sidecar as ``data["replica"]``.
4. Pair replica lines to Loaded lines ONCE, here, and turn every
   replica↔Loaded difference into ONE structured suggestion with a short
   explanation. Confidence findings ride as ``issues`` (blocking = "a human
   must look"); suggestions never block.
5. Autopilot (``review_invoices(..., mode="autopilot")``) runs the SAME
   pipeline, auto-accepts every suggestion (recorded in
   ``suggestion_actions`` with actor ``norm``), and receives through
   ``do_receive`` when there are no blocking issues.

The working document contract this emits (``doc_schema: "replica_v1"``):
working values at the top level (Loaded-populated, then edited), plus
``replica`` / ``loaded_snapshot`` / ``extracted_snapshot`` sidecars,
``suggestions``, ``issues``, ``suggestion_actions``, ``confidence``,
``reviewed_at`` and the fingerprint pair.
"""

from __future__ import annotations

import datetime
import logging
import re

from sqlalchemy.orm import Session

from app.services import invoice_line_match as line_match
from app.services import venue_autopilot as _VA
from app.services.invoice_extraction import (
    extract_invoice_copy,
    extract_invoice_copies_parallel,
    find_spec_for_supplier,
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
from app.services.received_invoice import (
    LoadedInvoiceClient,
    _norm,
    build_received_invoice_data,
    do_receive,
    invalidate_conflicting_drafts,
    receive_request_from_doc,
)

logger = logging.getLogger(__name__)

DOC_SCHEMA = "replica_v1"

_QTY_TOL = 0.001
_MONEY_TOL = 0.011

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

    Tiers per replica line, unique-hit-only among still-unclaimed lines:
    linked_item_id → normalized code → plain description match (the
    parity-tested ``invoice_line_match`` rules). A replica line whose only
    hits are already claimed (or never unique) is AMBIGUOUS — flagged, never
    guessed, and never double-added.

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

    for rl in replica_lines:
        rl_id = str(rl.get("id"))
        contested = False  # a tier matched only lines another rep line claimed

        item = rl.get("linked_item_id")
        if item:
            hits = [ln for ln in unclaimed.values() if ln.get("linked_item_id") == item]
            if len(hits) == 1:
                _claim(rl_id, hits[0])
                continue
            if not hits and any(ln.get("linked_item_id") == item for ln in doc_lines):
                contested = True

        code = _norm(rl.get("code")) if rl.get("code") else ""
        if code:
            hits = [ln for ln in unclaimed.values() if _norm(ln.get("code")) == code]
            if len(hits) == 1:
                _claim(rl_id, hits[0])
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
) -> None:
    parts = [kind] + [p for p in (field, line_id) if p]
    entry: dict = {
        "id": sid or ":".join(parts),
        "kind": kind,
        "field": field,
        "line_id": line_id,
        "current": current,
        "proposed": proposed,
        "explanation": explanation,
    }
    if apply is not None:
        entry["apply"] = apply
    if payload is not None:
        entry["payload"] = payload
    # Model-produced suggestions carry their confidence (high|medium) so
    # autopilot's gates can apply only decisive answers and the UI can show
    # how sure the resolver was. Deterministic suggestions omit it.
    if confidence is not None:
        entry["confidence"] = confidence
    out.append(entry)


def _line_suggestions(suggestions: list[dict], rl: dict, ln: dict, lid: str) -> None:
    desc = ln.get("description") or rl.get("description") or ln.get("code") or "line"
    if _differ(ln.get("quantity_received"), rl.get("quantity_received"), _QTY_TOL) and (
        rl.get("quantity_received") is not None
    ):
        _sugg(
            suggestions,
            "line_value",
            field="quantity_received",
            line_id=lid,
            current=ln.get("quantity_received"),
            proposed=rl.get("quantity_received"),
            explanation=(
                f"the copy bills quantity {rl.get('quantity_received')} for "
                f"'{desc}' — Loaded read {ln.get('quantity_received')}"
            ),
            apply={"quantity_received": rl.get("quantity_received")},
        )
    if _differ(ln.get("unit_cost"), rl.get("unit_cost"), _MONEY_TOL) and (
        rl.get("unit_cost") is not None
    ):
        _sugg(
            suggestions,
            "line_value",
            field="unit_cost",
            line_id=lid,
            current=ln.get("unit_cost"),
            proposed=rl.get("unit_cost"),
            explanation=(
                f"the copy prices '{desc}' at {rl.get('unit_cost')} — "
                f"Loaded read {ln.get('unit_cost')}"
            ),
            apply={"unit_cost": rl.get("unit_cost")},
        )
    # The batched unit resolver's answer (sibling lines, catalogue/item
    # context, product knowledge — see unit_resolver.py) OVERRIDES the
    # deterministic unit suggestion when it disagrees with the replica's own
    # resolution: two competing unit suggestions on one line would be noise.
    # Two shapes share this branch:
    #   - nothing anywhere said the size (unit_missing stands beside it);
    #   - the line resolved only via a bare charge word (EA → Each) and the
    #     resolver names the real pack — the upgrade the user asked for.
    # High and medium confidence suggest; low stays silent (a person
    # decides). Never against a unit Loaded already holds, and create_name
    # rides the existing unit_create_name → create_unit path instead.
    ur = rl.get("unit_resolved")
    override = (
        isinstance(ur, dict)
        and ur.get("unit_id")
        and ur.get("confidence") in ("high", "medium")
        and ur.get("unit_id") != rl.get("linked_unit_id")
        and not ln.get("linked_unit_id")
    )
    if (
        not override
        and rl.get("linked_unit_id")
        and rl.get("linked_unit_id") != ln.get("linked_unit_id")
    ):
        _sugg(
            suggestions,
            "line_value",
            field="unit",
            line_id=lid,
            current=ln.get("unit"),
            proposed=rl.get("unit"),
            explanation=(
                f"the copy's delivered unit for '{desc}' is "
                f"{rl.get('unit')} — Loaded has {ln.get('unit') or 'none'}"
            ),
            apply={
                "unit": rl.get("unit"),
                "linked_unit_id": rl.get("linked_unit_id"),
                "unit_ratio": rl.get("unit_ratio"),
            },
        )
    if override:
        _sugg(
            suggestions,
            "line_value",
            field="unit",
            line_id=lid,
            current=ln.get("unit"),
            proposed=ur.get("unit_name"),
            explanation=(
                f"no size is printed for '{desc}' — {ur.get('why')}"
                if not rl.get("linked_unit_id")
                else (
                    f"'{rl.get('unit')}' is how '{desc}' is charged, not the "
                    f"pack size — {ur.get('why')}"
                )
            ),
            apply={
                "unit": ur.get("unit_name"),
                "linked_unit_id": ur.get("unit_id"),
                "unit_ratio": ur.get("unit_ratio"),
            },
            confidence=ur.get("confidence"),
        )
    if rl.get("linked_item_id") and rl["linked_item_id"] != ln.get("linked_item_id"):
        # Both halves matter. Unlinked → the plain "link this item". LINKED to
        # something else → a real disagreement: the draft now opens on Loaded's
        # own code match (seed_working_from_loaded), so this is the only place
        # a wrong code match can be challenged. Silence here would let Loaded's
        # catalogue-order pick stand unchallenged on every line.
        linked_to = ln.get("item_name") if ln.get("linked_item_id") else None
        _sugg(
            suggestions,
            "line_value",
            field="linked_item_id",
            line_id=lid,
            current=linked_to,
            proposed=rl.get("item_name") or rl.get("linked_item_id"),
            explanation=(
                f"'{desc}' matches catalogue item "
                f"'{rl.get('item_name')}' ({rl.get('matched_by')})"
                + (f" — Loaded has '{linked_to}'" if linked_to else "")
            ),
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
    if rl.get("sale_tax_rate") is not None and ln.get("sale_tax_rate") is None:
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
    # The copy's delivered unit isn't in Loaded yet. It has no directly-applyable
    # value (the unit must be created first), so it is a `create_unit` suggestion
    # carrying the name in `payload` — accepting it creates the unit in Loaded and
    # links it. Not a note: the user actions it from Suggested Changes, or leaves
    # the current variant default.
    if rl.get("unit_create_name"):
        _sugg(
            suggestions,
            "create_unit",
            field="unit",
            line_id=lid,
            current=ln.get("unit"),
            proposed=rl.get("unit_create_name"),
            explanation=(
                f"the copy's delivered unit '{rl.get('unit_create_name')}' doesn't "
                f"exist in Loaded — create it"
            ),
            payload={"unit_name": rl.get("unit_create_name")},
        )
    # The copy's product isn't in the catalogue AT ALL. Exactly the unit case
    # one level up: no applyable value (the item must be CREATED first), so it
    # rides as a `create_item` suggestion carrying the replica's proposed name
    # and stock group. Without it the only prompt was a blocking ISSUE and a
    # "link or create" button on the line — every other proposal Norm makes is
    # a suggestion you can accept, dismiss and have recorded.
    #
    # Both halves are required: Loaded's create needs a stock group, and a
    # name we invented without one is not actionable.
    if (
        rl.get("suggested_name")
        and rl.get("suggested_group_id")
        and not ln.get("linked_item_id")
    ):
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
        )


# Header values worth suggesting. subtotal is deliberately ABSENT: it is
# derived from the lines, so the receive always writes the derived value
# (receive_request_from_doc) and a stored/suggested subtotal would only
# drift from edits.
_HEADER_VALUE_FIELDS = (
    ("tax_amount", "tax"),
    ("discount_amount", "discount"),
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


def brand_suggestions(data: dict, suggestions: list[dict]) -> None:
    """Offer to create a brand Loaded names on a line but has no record for.

    Loaded refuses to receive such a line — its own client blocks on it and
    ``do_receive`` guards the same way — so without this the invoice simply
    failed at submit with nothing to click (Bidfood 109945346: BIOZYME on
    CLEANER INDUSTRIAL ENZYME).

    The name comes from LOADED's line, never from the copy: extracting brands
    would generate a suggestion on nearly every line for no gain.

    Over ALL lines, not just those the replica paired — an unresolved brand
    blocks the receive whether or not the copy had anything to say about it.
    """
    for ln in data.get("lines") or []:
        if not isinstance(ln, dict) or ln.get("struck"):
            continue
        brand = str(ln.get("brand") or "").strip()
        if not brand or ln.get("linked_brand_id"):
            continue
        lid = str(ln.get("id"))
        _sugg(
            suggestions,
            "create_brand",
            field="linked_brand_id",
            line_id=lid,
            current=None,
            proposed=brand,
            explanation=(
                f"'{brand}' isn't a brand in Loaded — create it "
                "(Loaded won't receive a line naming a brand it doesn't have)"
            ),
            payload={"brand_name": brand},
        )


def order_item_suggestions(
    data: dict, suggestions: list[dict], catalogue: list[dict] | None
) -> None:
    """Let the purchase order decide, when the supplier's code cannot.

    Angus Meats sells canoe-cut marrow, 1-inch marrow and plain bones under
    ONE code (BONES) and prints "Beef Bones" on every line. Loaded's matcher
    takes the first of the three in catalogue order — BONES (STOCK) — so a
    delivery against an order for 6 kg canoe cut and 14 kg 1-inch booked
    20.58 kg to a third item (1010951, received 10 Aug 2026). The copy cannot
    break the tie either: it says the same words on both lines. The ORDER is
    the only evidence of which cut was bought.

    So: only where the code is genuinely AMBIGUOUS (more than one catalogue
    item carries it for this supplier), and only when the linked order names
    one of those candidates. An unambiguous code is never second-guessed, and
    the Loaded mirror is untouched — the X-ray keeps showing Loaded's own
    match, which is the whole point of it.

    Where several order rows qualify, each line takes the unclaimed one whose
    ordered quantity is nearest what arrived (6.43 → the 6, 14.15 → the 14),
    and a row is claimed once. This REPLACES any item suggestion the replica
    made for that line: one proposal per line, and against an ambiguous code
    the order beats a description-based match.
    """
    from app.services.invoice_po_reference import candidates_for_code, order_rows_for

    rows = order_rows_for(data)
    if not rows or not catalogue:
        return
    supplier_id = data.get("linked_supplier_id")
    claimed: set[int] = set()

    for ln in data.get("lines") or []:
        if not isinstance(ln, dict) or ln.get("struck"):
            continue
        candidates = candidates_for_code(catalogue, supplier_id, ln.get("code"))
        if len(candidates) < 2:
            continue
        ids = {str(c.get("id")): c for c in candidates}
        qty = _f(ln.get("quantity_received"))
        options = [
            pl for pl in rows if id(pl) not in claimed and str(pl.get("itemId")) in ids
        ]
        if not options:
            continue
        if qty is not None:
            options.sort(
                key=lambda pl: abs((_f(pl.get("quantityOrdered")) or 0.0) - qty)
            )
        row = options[0]
        claimed.add(id(row))
        item = ids[str(row.get("itemId"))]
        if str(item.get("id")) == str(ln.get("linked_item_id") or ""):
            continue

        lid = str(ln.get("id"))
        # One linked_item_id proposal per line — the order supersedes the
        # replica's, which read the same ambiguous words we did.
        suggestions[:] = [
            s
            for s in suggestions
            if not (s.get("field") == "linked_item_id" and s.get("line_id") == lid)
        ]
        _sugg(
            suggestions,
            "line_value",
            field="linked_item_id",
            line_id=lid,
            current=ln.get("item_name"),
            proposed=item.get("name"),
            explanation=(
                f"code '{ln.get('code')}' covers {len(candidates)} catalogue "
                f"items — the order for this delivery is "
                f"'{row.get('itemName')}' ({row.get('quantityOrdered')} "
                f"{row.get('unitName') or ''}".rstrip()
                + f"), Loaded matched '{ln.get('item_name')}'"
            ),
            apply={
                "linked_item_id": item.get("id"),
                "item_name": item.get("name"),
            },
        )


def build_suggestions(
    data: dict, replica: dict
) -> tuple[list[dict], list[dict], dict[str, str]]:
    """All suggestions + pairing/coverage issues for one reviewed invoice.

    ``data`` holds the freshly-shaped working values (Loaded's draft);
    ``replica`` is the sidecar. Returns ``(suggestions, extra_issues,
    pairs)`` — the extra issues cover pairing ambiguity and Loaded lines
    missing from the copy; the replica's own issues are merged by the caller
    (remapped onto working lines via ``pairs``).
    """
    suggestions: list[dict] = []
    issues: list[dict] = []
    doc_lines = data.get("lines") or []
    rep_lines = replica.get("lines") or []
    pairs, ambiguous, unpaired_rep, unpaired_loaded = pair_lines(rep_lines, doc_lines)
    lines_by_id = {str(ln.get("id")): ln for ln in doc_lines if ln.get("id")}

    for rl in rep_lines:
        lid = pairs.get(str(rl.get("id")))
        if lid:
            _line_suggestions(suggestions, rl, lines_by_id[lid], lid)

    for rl in ambiguous:
        issues.append(
            {
                "id": f"ambiguous_pairing:{rl.get('id')}",
                "code": "ambiguous_pairing",
                "blocking": True,
                "line_id": rl.get("id"),
                "message": (
                    f"copy line '{rl.get('description')}' matches a Loaded line "
                    "another copy line already claimed — reconcile these lines "
                    "by hand"
                ),
            }
        )

    for rl in unpaired_rep:
        _sugg(
            suggestions,
            "add_line",
            line_id=str(rl.get("id")),
            proposed=rl.get("description"),
            explanation=(
                f"the copy bills '{rl.get('description')}' "
                f"(qty {rl.get('quantity_received')} at {rl.get('unit_cost')}) "
                "but Loaded's draft has no such line"
            ),
            payload={k: v for k, v in rl.items() if k not in ("matched_by",)},
        )

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
                "copy doesn't bill it" + (" — a $0 artifact; strike it" if zero else "")
            ),
            apply={"struck": True},
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
                        f"{ln.get('total_cost')} but the copy doesn't show it — "
                        "either Loaded is wrong (strike it) or the extraction "
                        "missed a line (check the copy)"
                    ),
                    "clears_when": {
                        "scope": "line",
                        "line_id": lid,
                        "field": "struck",
                        "op": "truthy",
                    },
                }
            )

    # ---- Header ----
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
                f"the copy's invoice number is {replica.get('reference_number')} — "
                f"Loaded recorded {data.get('reference_number')}"
            ),
            apply={"reference_number": replica.get("reference_number")},
        )
    # Only a PARSEABLE copy date is worth proposing: _iso_date keeps an
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
                f"the copy is dated {replica.get('issued_at')} — Loaded has "
                f"{_date_part(data.get('issued_at')) or 'no date'}"
            ),
            apply={"issued_at": replica.get("issued_at")},
        )
    for field, label in _HEADER_VALUE_FIELDS:
        if replica.get(field) is None:
            continue
        if _differ(data.get(field), replica.get(field), _MONEY_TOL):
            _sugg(
                suggestions,
                "header_value",
                field=field,
                current=data.get(field),
                proposed=replica.get(field),
                explanation=(
                    f"the copy's {label} is {replica.get(field)} — Loaded has "
                    f"{data.get(field)}"
                ),
                apply={field: replica.get(field)},
            )
    if replica.get("linked_supplier_id") and replica.get(
        "linked_supplier_id"
    ) != data.get("linked_supplier_id"):
        _sugg(
            suggestions,
            "supplier",
            field="linked_supplier_id",
            current=data.get("supplier_name"),
            proposed=replica.get("supplier_name"),
            explanation=(
                f"the copy names '{replica.get('supplier_name')}' — Loaded has "
                f"'{data.get('supplier_name') or 'no supplier'}'"
            ),
            apply={
                "linked_supplier_id": replica.get("linked_supplier_id"),
                "supplier_name": replica.get("supplier_name"),
            },
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
            explanation=(
                f"the copy references order {replica.get('purchase_order_number')} "
                "and a matching open Loaded purchase order exists — link it"
            ),
            apply={
                "linked_purchase_order_id": replica.get("linked_purchase_order_id"),
                "purchase_order_number": replica.get("purchase_order_number"),
            },
        )

    # ---- PO issues → their remedies ----
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
        )
    # No unlink for a supplier mismatch. An order owned by another Loaded
    # supplier is usually a through-supplier arrangement (Soho ordered, Procure
    # delivers), so detaching a correct link was the wrong remedy for a
    # question that only deserved a warning — and the warning itself now says
    # so. See po_supplier_mismatch in invoice_replica.
    dup = rep_issues.get("duplicate_invoice")
    if dup:
        d = dup.get("data") or {}
        _sugg(
            suggestions,
            "delete_invoice",
            explanation=dup.get("message") or "duplicate — delete this draft",
            payload={
                "type": "delete_invoice",
                "invoice_id": data.get("invoice_id"),
                "reference": data.get("reference_number"),
                **d,
                "summary": dup.get("message"),
            },
        )

    return suggestions, issues, pairs


# ---------------------------------------------------------------------------
# Issue post-processing
# ---------------------------------------------------------------------------

_LINE_ISSUE_CLEARS = {
    "item_unmatched": ("linked_item_id", "not_null"),
    "brand_unknown": ("linked_brand_id", "not_null"),
    # unit_missing / unit_unconfirmed deliberately have NO clears_when: a unit
    # value already sitting on Loaded's line is Loaded's OCR of the same paper
    # — the very thing we don't trust. Only an explicit confirm/dismiss
    # (recorded in suggestion_actions) clears them.
}

#: Which venue toggle would let autopilot get past this blocker on its own.
#: ONE mapping, stamped onto the issue as `gate`, so the card can say "this
#: needs a brand created, and auto-create brands is off" without keeping its
#: own copy of the rules to drift out of step with these.
#: An issue with no entry here is a judgement no toggle can authorise
#: (a duplicate invoice, inconsistent totals) — a person has to look.
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
}


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
            # Its own remedy is the split_reference suggestion: keep the
            # order's NUMBER without taking its link, because Loaded's
            # PO↔invoice is 1:1 and the sibling delivery holds it. Accepting
            # that sets split_po_id, and the reconciliation then runs against
            # the order — which is exactly what "a split validates and
            # receives without re-linking" means. Without this predicate the
            # blocker outlived its own remedy and the only way through was to
            # wave it by hand.
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


#: remedy suggestion kind -> the blocker it belongs to, and the gate that
#: authorises Norm to apply it unattended (None = no toggle can; a person
#: decides).
#:
#: A suggestion CHANGES A VALUE. A blocker STOPS A RECEIVE. A suggestion that
#: exists only to clear a blocker is neither — it is that blocker's remedy, and
#: it belongs on the same row. The creates were the obvious case; `unlink_po`
#: is the one that proved the rule, showing up as "unlink it" in Suggested
#: changes beside "order 1520559 belongs to Soho" in the blocked list (Soho
#: 00162798, 17 Aug 2026).
_REMEDY_BLOCKERS = {
    "create_item": ("item_unmatched", _VA.AUTO_CREATE_ITEMS),
    "create_unit": ("unit_missing", _VA.AUTO_CREATE_UNITS),
    "create_brand": ("brand_unknown", _VA.AUTO_CREATE_BRANDS),
    # No gate: an order already fully invoiced by a sibling is a judgement,
    # not a create, so no toggle authorises it.
    "unlink_po": ("po_doubled_up", None),
}


def fold_remedies_into_blockers(
    suggestions: list[dict], issues: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Move every "create this in Loaded" out of the suggestion list and onto
    the blocker it belongs to. Returns ``(suggestions, issues)``.

    A suggestion CHANGES A VALUE; a blocker STOPS A RECEIVE. Creates were both
    at once and it showed: an un-catalogued line rendered a create_item
    suggestion, an `item_unmatched` blocking issue, a NEW badge and a disabled
    button — four things for one decision — while an unknown brand rendered
    three of those and nothing in the blocked list at all, so `confidence` read
    "ready" and the receive 400'd at Loaded's own guard.

    After this every create appears exactly once, on a row that says what it
    needs, carries the button that does it, and names the venue toggle that
    would let Norm do it alone.
    """
    kept: list[dict] = []
    # The supplier blocker has no suggestion behind it (there is no
    # create_supplier kind), but it is the same shape of decision, so it
    # carries its action from here too.
    for i in issues:
        if i.get("code") == "supplier_unresolved":
            name = (i.get("data") or {}).get("supplier_name")
            if name:
                i["action"] = {
                    "kind": "create_supplier",
                    "payload": {"supplier_name": name},
                }
    # `or ""` on BOTH sides: a header-level blocker (po_doubled_up) has no
    # line_id, and keying it "None" here while the suggestion side keyed it ""
    # meant the remedy never found its blocker and stayed a suggestion —
    # exactly the duplicate this function exists to remove.
    by_line: dict[tuple[str, str], dict] = {
        (str(i.get("code")), str(i.get("line_id") or "")): i for i in issues
    }
    # A unit blocker with no create name behind it is the "no unit could be
    # found" case: nothing to create, so the only way past is to CHOOSE one
    # Loaded already has — a different toggle, and a different action. When
    # the batched resolver already answered with HIGH confidence, its pick
    # rides in `apply` so the gate applies exactly what the suggestion shows
    # (and skips a second model call); medium stays suggestion-only — the
    # gate must not act on a "likely".
    resolver_by_line = {
        str(s.get("line_id") or ""): s
        for s in suggestions
        if s.get("field") == "unit" and s.get("confidence") == "high"
    }
    for i in issues:
        if i.get("code") == "unit_missing" and not i.get("action"):
            i["gate"] = _VA.RECEIVE_WITHOUT_UNIT
            i["action"] = {"kind": "guess_unit", "payload": {}}
            picked = resolver_by_line.get(str(i.get("line_id") or ""))
            if picked and isinstance(picked.get("apply"), dict):
                i["action"]["apply"] = dict(picked["apply"])
    for s in suggestions:
        pair = _REMEDY_BLOCKERS.get(str(s.get("kind")))
        if not pair:
            kept.append(s)
            continue
        code, gate = pair
        lid = str(s.get("line_id") or "")
        # A create carries its arguments in `payload`; a remedy that is just a
        # value change (unlink_po) carries them in `apply`, and the card needs
        # whichever one the kind actually uses.
        payload = dict(s.get("payload") or {})
        apply_now = s.get("apply") if isinstance(s.get("apply"), dict) else None
        target = by_line.get((code, lid))
        if target is None and code == "unit_missing":
            # The copy names a unit Loaded lacks while the line still holds a
            # variant DEFAULT, so no unit_missing was raised — and autopilot
            # therefore received with the variant's unit instead of the one on
            # the paper. Its own blocker, so it stops rather than guesses.
            target = {
                "id": f"unit_would_be_created:{lid}",
                "code": "unit_would_be_created",
                "blocking": True,
                "line_id": lid or None,
                "message": s.get("explanation") or "the copy's unit isn't in Loaded",
            }
            issues.append(target)
        if target is None:
            target = {
                "id": f"{code}:{lid}",
                "code": code,
                "blocking": True,
                "line_id": lid or None,
                "message": s.get("explanation") or "needs creating in Loaded",
            }
            issues.append(target)
        if gate:
            target["gate"] = gate
        target["action"] = {"kind": s.get("kind"), "payload": payload}
        if apply_now is not None:
            target["action"]["apply"] = apply_now
        # Carry the wording the suggestion had: it is written for a person
        # ("the copy's delivered unit '6x1000mL' doesn't exist in Loaded"),
        # where the replica's issue message is written for triage.
        if s.get("explanation"):
            target["message"] = s["explanation"]
    return kept, issues


def apply_open_gates(
    db, config_db, venue_id: str, invoice_id: str, data: dict, settings: dict
) -> list[str]:
    """Do the creates this venue has authorised, and only those.

    Every blocker carries the gate that governs it; a ticked gate means Norm
    may clear that blocker on its own. Anything unticked stays blocking, which
    is what parks the invoice for a person — and the blocker still names the
    toggle that would have let it through, so the answer to "why didn't this
    receive?" is on the row itself.

    The creates run through the SAME endpoint functions the buttons call, so
    autopilot and a human produce byte-identical Loaded writes; there is no
    second implementation to drift. A create that fails leaves its blocker
    standing — the invoice parks instead of receiving half-built.
    """
    from app.routers import invoice_fixes as IF
    from app.services import supplier_identity

    lines = {str(ln.get("id")): ln for ln in data.get("lines") or [] if ln.get("id")}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    actions = data.setdefault("suggestion_actions", [])
    done: list[str] = []

    def _cleared(issue: dict, after: dict) -> None:
        """Record it against the ISSUE id — the same log the card shows, so
        "Norm created this" is visible next to what a person did, and
        compute_confidence stops treating the blocker as open."""
        actions.append(
            {
                "suggestion_id": issue.get("id"),
                "action": "accepted",
                "by": "norm",
                "at": now,
                "before": {},
                "after": after,
            }
        )

    for issue in data.get("issues") or []:
        action = issue.get("action")
        if not isinstance(action, dict) or not issue.get("blocking"):
            continue
        if not _VA.gate_open(settings, issue.get("gate")):
            continue
        kind = action.get("kind")
        payload = action.get("payload") or {}
        ln = lines.get(str(issue.get("line_id") or ""))
        try:
            if kind == "create_brand" and ln is not None:
                out = IF.create_stock_brand(
                    IF.CreateBrandRequest(
                        venue_id=venue_id, name=payload.get("brand_name") or ""
                    ),
                    db,
                    config_db,
                    None,
                )
                ln["linked_brand_id"] = out.get("brand_id")
                _cleared(issue, {"linked_brand_id": out.get("brand_id")})
                done.append(f"created brand '{out.get('brand_name')}'")
            elif kind == "create_unit" and ln is not None:
                out = IF.create_stock_unit(
                    IF.CreateUnitRequest(
                        venue_id=venue_id, name=payload.get("unit_name") or ""
                    ),
                    db,
                    config_db,
                    None,
                )
                ln["linked_unit_id"] = out.get("unit_id")
                ln["unit"] = out.get("unit_name")
                if out.get("unit_ratio") is not None:
                    ln["unit_ratio"] = out.get("unit_ratio")
                _cleared(issue, {"linked_unit_id": out.get("unit_id")})
                done.append(f"created unit '{out.get('unit_name')}'")
            elif kind == "guess_unit" and ln is not None:
                # The batched resolver's HIGH-confidence pick rides in the
                # action's `apply` — use it verbatim so autopilot applies
                # exactly what the suggestion showed. Fall back to the
                # single-line chooser only when no resolver answer exists.
                pre = action.get("apply")
                if isinstance(pre, dict) and pre.get("linked_unit_id"):
                    ln["linked_unit_id"] = pre.get("linked_unit_id")
                    ln["unit"] = pre.get("unit")
                    if pre.get("unit_ratio") is not None:
                        ln["unit_ratio"] = pre.get("unit_ratio")
                    _cleared(issue, {"linked_unit_id": pre.get("linked_unit_id")})
                    done.append(f"set unit '{pre.get('unit')}' (unit resolver)")
                    continue
                from app.services.unit_guess import guess_unit, llm_chooser

                lh = LoadedInvoiceClient(db, config_db, venue_id)
                chosen, why = guess_unit(
                    ln,
                    lh.get("/1.0/stock/internal/units") or [],
                    ask_llm=llm_chooser(db),
                )
                if chosen is None:
                    logger.info("no unit could be chosen on %s: %s", invoice_id, why)
                    continue
                ln["linked_unit_id"] = chosen.get("id")
                ln["unit"] = chosen.get("name")
                if chosen.get("ratio") is not None:
                    ln["unit_ratio"] = chosen.get("ratio")
                _cleared(issue, {"linked_unit_id": chosen.get("id")})
                done.append(f"matched unit '{chosen.get('name')}' ({why})")
            elif kind == "create_supplier":
                # The riskiest of the four by a distance. A duplicate supplier
                # row does not just clutter Loaded: supplier identity is what
                # picks the extraction spec, so a second 'SERVICE FOODS' sends
                # every future invoice from that business to a prompt written
                # for someone else — which is precisely what happened on 10 Aug
                # 2026 and took a day to unpick. So autopilot may only create a
                # supplier nothing plausibly matches; a near miss is a human
                # decision, and the invoice parks with the blocker on it.
                name = payload.get("supplier_name") or ""
                lh = LoadedInvoiceClient(db, config_db, venue_id)
                rows = lh.get("/1.0/stock/internal/suppliers")
                rows = rows if isinstance(rows, list) else []
                live = [
                    s
                    for s in rows
                    if isinstance(s, dict)
                    and not s.get("removedAt")
                    and not s.get("datestampDeleted")
                ]
                if supplier_identity.alias_candidates([name], live):
                    logger.info(
                        "autopilot will not create supplier '%s' on %s: an "
                        "existing supplier is a plausible match",
                        name,
                        invoice_id,
                    )
                    continue
                out = IF.create_supplier(
                    IF.CreateSupplierRequest(venue_id=venue_id, name=name),
                    db,
                    config_db,
                    None,
                )
                data["linked_supplier_id"] = out.get("supplier_id")
                _cleared(issue, {"linked_supplier_id": out.get("supplier_id")})
                done.append(f"created supplier '{out.get('supplier_name')}'")
            elif kind == "create_item" and ln is not None:
                out = IF.create_stock_item(
                    IF.CreateItemRequest(
                        venue_id=venue_id,
                        invoice_id=invoice_id,
                        line_id=str(issue.get("line_id")),
                        group_id=payload.get("group_id") or "",
                        name=payload.get("name"),
                        unit_id=ln.get("linked_unit_id"),
                        brand_id=ln.get("linked_brand_id"),
                    ),
                    db,
                    config_db,
                    None,
                )
                ln["linked_item_id"] = out.get("item_id") or out.get("linked_item_id")
                _cleared(issue, {"linked_item_id": ln.get("linked_item_id")})
                done.append(f"created stock item '{payload.get('name')}'")
        except Exception as exc:  # noqa: BLE001 — the blocker simply stands
            logger.info(
                "autopilot gate %s could not be cleared on %s: %s",
                issue.get("gate"),
                invoice_id,
                exc,
            )
    return done


_UNIT_ISSUES = ("unit_missing", "unit_unconfirmed")


def name_the_unit_in_use(data: dict, issues: list[dict]) -> None:
    """Finish a unit blocker's sentence with the unit that will be used.

    The replica raises these; it can only see its own resolution, so it says
    "no unit could be read from the copy" and stops. The WORKING line usually
    has one anyway — Loaded's own `linkedUnitId`, or the supplier variant
    filled in by `seed_working_from_loaded` — and that is what the dropdown
    shows and what the receive will send.

    Saying nothing about it produced the contradiction this exists to fix: a
    blocker reading "no unit could be determined ... set the unit before
    receiving" above a dropdown displaying 750 mL (Soho 00162798). Report the
    outcome instead, and let the venue decide whether a unit Loaded supplied
    rather than the paper is good enough to receive on.
    """
    lines = {str(ln.get("id")): ln for ln in data.get("lines") or [] if ln.get("id")}
    for issue in issues:
        if issue.get("code") not in _UNIT_ISSUES:
            continue
        ln = lines.get(str(issue.get("line_id") or ""))
        unit_name = (ln or {}).get("unit")
        if not (ln or {}).get("linked_unit_id"):
            # Nothing anywhere — now the imperative is the honest ending.
            issue["message"] = f"{issue['message']} — set one before receiving"
            continue
        chosen = (issue.get("data") or {}).get("unit_chosen_by")
        how = {
            "guess": "matched",
            "created": "created",
        }.get(chosen, "using Loaded's")
        issue["message"] = f"{issue['message']} — {how} '{unit_name}'"
        # A unit Loaded supplied is not the same claim as a unit the paper
        # confirmed, so it is its own decision rather than folded into
        # "receive without a unit", which means something else entirely.
        issue["gate"] = _VA.RECEIVE_WITH_UNCONFIRMED_UNIT


def _clears(data: dict, issue: dict) -> bool:
    """Server-side evaluation of an issue's clears_when against working values
    — the same dumb predicate the component evaluates client-side."""
    cw = issue.get("clears_when")
    if not isinstance(cw, dict):
        return False
    if cw.get("scope") == "line":
        ln = next(
            (
                ln
                for ln in data.get("lines") or []
                if str(ln.get("id")) == str(cw.get("line_id"))
            ),
            None,
        )
        v = (ln or {}).get(cw.get("field"))
    else:
        v = data.get(cw.get("field"))
    if cw.get("op") == "not_null":
        return v is not None
    if cw.get("op") == "truthy":
        return bool(v)
    if cw.get("op") == "equals":
        return v == cw.get("value")
    return False


def compute_confidence(data: dict) -> str:
    """``ready`` iff no blocking issue remains: an issue counts as cleared when
    its clears_when predicate holds against the working values, or when a
    recorded action (accept/dismiss, human or norm) resolved it."""
    actioned = {
        a.get("suggestion_id")
        for a in data.get("suggestion_actions") or []
        if a.get("action") in ("accepted", "dismissed")
    }
    for i in data.get("issues") or []:
        if not i.get("blocking"):
            continue
        if i.get("id") in actioned:
            continue
        if _clears(data, i):
            continue
        return "needs_review"
    return "ready"


# ---------------------------------------------------------------------------
# Applying suggestions (the autopilot's accept + the shared record shape)
# ---------------------------------------------------------------------------


def apply_suggestion(data: dict, s: dict) -> dict | None:
    """Apply one suggestion's ``apply``/``payload`` to the working values.
    Returns the before-values (the undo payload), or None when nothing
    applied (e.g. delete_invoice — a Loaded write, never applied locally)."""
    if s.get("kind") == "delete_invoice":
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
        ln = next(
            (
                ln
                for ln in data.get("lines") or []
                if str(ln.get("id")) == str(s["line_id"])
            ),
            None,
        )
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


def auto_accept_all(data: dict, *, actor: str = "norm") -> int:
    """Autopilot's accept: apply every suggestion and record each action.
    Returns the number applied."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    actions = data.setdefault("suggestion_actions", [])
    applied = 0
    for s in data.get("suggestions") or []:
        if s.get("kind") == "delete_invoice":
            continue  # a Loaded write — never automatic
        before = apply_suggestion(data, s)
        if before is None:
            continue
        actions.append(
            {
                "suggestion_id": s.get("id"),
                "action": "accepted",
                "by": actor,
                "at": now,
                "before": before,
                "after": s.get("apply") or {"added": True},
            }
        )
        applied += 1
    return applied


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
    require_valid_po: bool = True,
    allow_sensei: bool = False,
) -> dict:
    """Review one invoice and return the full replica_v1 doc payload.

    Pure computation + Loaded reads: the caller persists the payload onto
    working documents (and decides about receiving). ``detail`` /
    ``extraction`` / ``reference`` let the batch path inject prefetched work.
    """
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
    if not file_id:
        data["issues"].append(
            {
                "id": "no_copy_attached",
                "code": "no_copy_attached",
                "blocking": True,
                "line_id": None,
                "message": (
                    "no invoice copy is attached in Loaded — nothing to review "
                    "against; attach the document or receive by hand"
                ),
            }
        )
    else:
        if allow_sensei and detail.get("supplierName"):
            _maybe_sensei(db, config_db, venue_id, invoice_id, detail["supplierName"])
        if extraction is None:
            extraction = extract_invoice_copy(
                db,
                lh,
                file_id,
                instructions=extraction_instructions(config_db, lh, detail),
                venue_key=venue_id,
            )
        if not isinstance(extraction, dict) or extraction.get("error"):
            err = (
                extraction.get("error")
                if isinstance(extraction, dict)
                else "no extraction"
            )
            data["issues"].append(
                {
                    "id": "copy_unreadable",
                    "code": "copy_unreadable",
                    "blocking": True,
                    "line_id": None,
                    "message": (
                        f"the invoice copy could not be read ({err}) — review "
                        "the document by hand"
                    ),
                }
            )
        else:
            data["extracted_snapshot"] = _extracted_snapshot(extraction)
            # Feed the Norm supplier-product catalogue: every successful
            # extraction's printed sizes are supplier-stated physical facts
            # (provenance 'printed'). Best-effort — never fails the review.
            try:
                from app.services import supplier_catalog

                supplier_catalog.observe_extraction(
                    config_db, extraction, provenance="printed"
                )
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
                **(reference or {}),
            )
            data["replica"] = replica
            # DERIVED, server-owned: never patched by the client, never sent
            # to Loaded. The replica's verdict wins; Loaded's sign is the
            # fallback for a draft that has not been reviewed yet.
            data["is_credit_note"] = bool(
                replica.get("is_credit_note")
                or (isinstance(data.get("total"), (int, float)) and data["total"] < 0)
            )
            suggestions, extra_issues, pairs = build_suggestions(data, replica)
            # Last word on an ambiguous supplier code: the order.
            order_item_suggestions(data, suggestions, catalogue)
            # A brand Loaded names but has no record for blocks its own
            # receive — a blocker, listed with the other gates. The
            # create_brand suggestion still supplies the action; the fold below
            # moves it onto this row so it appears exactly once.
            brand_issues(data, extra_issues)
            brand_suggestions(data, suggestions)
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
            # Each remedy belongs in ONE list — the blocked one.
            suggestions, issues = fold_remedies_into_blockers(suggestions, issues)
            # ...and a unit blocker says which unit will be used, now that the
            # working lines are in hand.
            name_the_unit_in_use(data, issues)
            data["suggestions"] = suggestions
            data["issues"] = issues
            # The old po_linked gate: an invoice with no purchase order at
            # all (none linked in Loaded, none resolvable from the copy).
            # Blocking only under the require_valid_po policy — the batch
            # default; the interactive review passes False (the human is
            # looking at the card) and gets a note instead.
            # A credit note legitimately has no purchase order — the one it
            # prints belongs to the invoice being credited (see the replica's
            # PO skip), so demanding a link would block every credit.
            if (
                not data.get("linked_purchase_order_id")
                and not data.get("is_credit_note")
                and not any(
                    s.get("kind") in ("link_po", "split_reference") for s in suggestions
                )
            ):
                data["issues"].append(
                    {
                        "id": "po_missing",
                        "code": "po_missing",
                        "blocking": require_valid_po,
                        "line_id": None,
                        "message": (
                            "no purchase order is linked and the copy "
                            "references none that resolves — link an order or "
                            "confirm this delivery had no PO"
                        ),
                        "clears_when": {
                            "scope": "header",
                            "field": "linked_purchase_order_id",
                            "op": "not_null",
                        },
                    }
                )

    data["confidence"] = compute_confidence(data)
    data["reviewed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    data["reviewed_invoice_fingerprint"] = data.get("loaded_invoice_fingerprint")
    return data


def extraction_instructions(config_db: Session, lh, detail: dict) -> str:
    """The composed extraction instructions for one invoice (cache-key
    material): main prompt + Loaded's supplier (with its stored aliases,
    sorted for key stability) + the matching supplier spec's notes."""
    aliases: list[str] = []
    sup_id = detail.get("linkedSupplierId")
    if detail.get("supplierName") and sup_id:
        try:
            rows = lh.get(f"/1.0/stock/internal/suppliers/{sup_id}/aliases")
            aliases = sorted(
                str(a.get("name"))
                for a in (rows if isinstance(rows, list) else [])
                if isinstance(a, dict) and a.get("name")
            )
        except Exception as exc:  # noqa: BLE001 — aliases are hints
            logger.info("supplier aliases unavailable: %s", exc)
    return pdf_instructions_for(
        config_db,
        loaded_supplier=detail.get("supplierName"),
        loaded_aliases=aliases,
    )


def _maybe_sensei(
    db: Session, config_db: Session, venue_id: str, invoice_id: str, supplier: str
) -> None:
    """Train the sensei for a spec-less supplier BEFORE extraction (the
    engine's ordering rule) — fail-open, the review continues regardless."""
    try:
        if find_spec_for_supplier(config_db, supplier) is not None:
            return
        from app.agents.internal_tools import _sensei_train_supplier

        _sensei_train_supplier(
            {
                "venue_id": venue_id,
                "invoice_id": invoice_id,
                "supplier_name": supplier,
            },
            db,
            None,
        )
    except Exception as exc:  # noqa: BLE001 — sensei must never sink a review
        logger.warning("sensei training failed for %s: %s", supplier, exc)


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
    max_sensei: int = 0,
    require_valid_po: bool = True,
) -> dict:
    """Review a batch of invoices; under ``mode="autopilot"`` auto-accept
    every suggestion (recorded, actor ``norm``) and receive the ones with no
    blocking issues.

    Mode policy (mirrors the consolidator's run modes):
    - ``approve_all`` / anything unknown: review + cards only, never receives.
    - ``approve_fixes``: receives only invoices that are READY with NOTHING to
      change (no suggestions at all); anything else becomes a card.
    - ``autopilot``: auto-accept all suggestions, receive when ready.

    Returns ``{"cards", "verdicts", "received", "skipped", "sensei"}`` —
    cards are full replica_v1 doc payloads (they ride into ``fix_invoices``
    verbatim for the working-document fan-out).
    """
    from app.services import venue_autopilot as VA
    from app.services.spec_dojo import prefetch_replica_reference

    # The VENUE decides how far Norm may go; the caller's `mode` is a ceiling
    # it can lower but never raise. Reviewing a single invoice passes
    # `approve_all` for exactly this reason — opening one in the card must
    # never write to Loaded — and a chat request cannot talk a venue onto a
    # rung it was never put on.
    from app.db.models import Venue

    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    settings = VA.settings_for(venue)
    mode = VA.at_most(settings["mode"], mode)
    settings["mode"] = mode
    if settings[VA.RECEIVE_WITHOUT_PO]:
        # The venue has said a missing order is not a reason to stop. The
        # per-task override stays honoured for anyone who set it.
        require_valid_po = False

    lh = LoadedInvoiceClient(db, config_db, venue_id)

    if invoice_ids is None:
        rows = lh.get(
            "/1.0/stock/internal/invoices"
            "?from=1901-01-01&to=9999-12-31&status=NotReceived&page=0&pageSize=200"
        )
        rows = rows if isinstance(rows, list) else (rows or {}).get("data") or []
        invoice_ids = [
            str(r["id"])
            for r in rows
            if isinstance(r, dict)
            and r.get("id")
            and not r.get("isReceived")
            and not r.get("deletedAt")
            and _in_window(r.get("createdAt") or r.get("issuedAt"), from_date, to_date)
        ]

    details: dict[str, dict] = {}
    for iid in invoice_ids:
        try:
            details[iid] = lh.invoice(iid)
        except Exception as exc:  # noqa: BLE001 — one bad invoice never sinks the batch
            logger.warning("invoice %s unavailable: %s", iid, exc)

    # Sensei first, budgeted, one per supplier — a fresh spec must exist
    # BEFORE its supplier's extraction runs (the instructions are cache-key
    # material).
    sensei_runs: list[dict] = []
    if max_sensei > 0:
        seen: set[str] = set()
        budget = max_sensei
        for iid, det in details.items():
            if budget <= 0:
                break
            name = str(det.get("supplierName") or "").strip()
            key = _norm(name)
            if not name or not det.get("fileId") or key in seen:
                continue
            seen.add(key)
            if find_spec_for_supplier(config_db, name) is not None:
                continue
            budget -= 1
            _maybe_sensei(db, config_db, venue_id, iid, name)
            sensei_runs.append({"invoice_id": iid, "supplier_name": name})

    # Parallel extraction warm-up (cache-first; misses fan out).
    order = [iid for iid in invoice_ids if iid in details]
    requests = []
    for iid in order:
        det = details[iid]
        requests.append(
            {
                "file_id": det.get("fileId"),
                "instructions": extraction_instructions(config_db, lh, det),
                "venue_key": venue_id,
            }
            if det.get("fileId")
            else None
        )
    to_extract = [r for r in requests if r]
    extracted = extract_invoice_copies_parallel(db, lh, to_extract)
    it = iter(extracted)
    extractions = {iid: (next(it) if r else None) for iid, r in zip(order, requests)}

    reference = None
    try:
        reference = prefetch_replica_reference(db, config_db, venue_id)
    except Exception as exc:  # noqa: BLE001 — per-invoice fetches still work
        logger.info("replica reference prefetch failed: %s", exc)

    cards: list[dict] = []
    verdicts: list[dict] = []
    received: list[dict] = []
    skipped: list[dict] = []
    for iid in order:
        det = details[iid]
        try:
            data = review_invoice(
                db,
                config_db,
                venue_id,
                iid,
                lh=lh,
                detail=det,
                extraction=extractions.get(iid),
                reference=reference,
                require_valid_po=require_valid_po,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("review failed for %s: %s", iid, exc)
            skipped.append(
                {
                    "invoice_id": iid,
                    "reference_number": det.get("referenceNumber"),
                    "outcome": "review failed",
                    "reasons": [str(exc)],
                }
            )
            continue

        gated: list[str] = []
        if mode == "autopilot":
            auto_accept_all(data, actor="norm")
            # Then the gates: create the units/items/brands this venue has
            # authorised, and leave every other blocker standing so the
            # invoice parks with its reason on it.
            gated = apply_open_gates(db, config_db, venue_id, iid, data, settings)
            data["confidence"] = compute_confidence(data)

        blocking = [
            i.get("message")
            for i in data.get("issues") or []
            if i.get("blocking") and not _clears(data, i)
        ]
        # Which toggle would have let each remaining blocker through. This is
        # the answer to "why didn't this receive?", and it is the difference
        # between a venue climbing the ladder on evidence and guessing.
        gates_wanted = sorted(
            {
                i.get("gate")
                for i in data.get("issues") or []
                if i.get("blocking") and not _clears(data, i) and i.get("gate")
            }
        )
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
            except Exception as exc:  # noqa: BLE001 — a failed write is a skip
                verdict["reasons"] = [f"receive failed: {exc}"]
                skipped.append({**verdict, "outcome": "receive failed"})
                cards.append(data)
        else:
            outcome = (
                "ready to receive — awaiting approval"
                if data.get("confidence") == "ready"
                else "needs review"
            )
            skipped.append({**verdict, "outcome": outcome})
            cards.append(data)
        verdicts.append(verdict)

    return {
        "cards": cards,
        "verdicts": verdicts,
        "received": received,
        "skipped": skipped,
        "sensei": sensei_runs,
    }


def _in_window(created: object, from_date: str | None, to_date: str | None) -> bool:
    d = _date_part(created)
    if from_date and d and d < _date_part(from_date):
        return False
    if to_date and d and d > _date_part(to_date):
        return False
    return True
