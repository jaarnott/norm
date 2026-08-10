"""PO reconciliation for a received-invoice working document.

The working document holds three kinds of data, and this module owns the
third:

1. **Submit values** — what the user edits and what ``do_receive`` writes to
   Loaded (header fields, ``lines[]`` quantities/costs/units/item links).
2. **Reference data** — immutable inputs cached on the doc (``replica``,
   ``loaded_snapshot``, ``extracted_snapshot``, and ``po_reference`` here).
3. **Derived projection** — the PO reconciliation shown beside the lines:
   per-line ``quantity_ordered`` / ``reference_cost`` / ``on_order`` /
   ``display_code`` / ``substitute_for``, plus ``ordered_not_received`` /
   ``ordered_received_elsewhere`` / ``order_date``. Display-only — none of it
   is ever sent to Loaded.

The projection has exactly ONE writer: the server, recomputed from (1) + (2)
on every change. It was previously patched by the editor too, and the two
writers drifted — accepting a line's item suggestion emptied
``ordered_not_received`` client-side, and Undo restored the line but not the
list, leaving "nothing outstanding" next to an unlinked line (INV-958,
10 Aug 2026). Hence the split below: ``fetch_po_reference`` does the network
read, ``project_po_reference`` is PURE so it can run after every document
patch with no client, no config DB and no HTTP.
"""

from __future__ import annotations

import logging

from app.services.received_invoice import _norm

logger = logging.getLogger(__name__)

# Keys the projection owns. Nothing outside this module may write them.
_LINE_KEYS = (
    "quantity_ordered",
    "reference_cost",
    "on_order",
    "display_code",
    "substitute_for",
)
_HEADER_KEYS = ("ordered_not_received", "ordered_received_elsewhere", "order_date")


def po_id_for(data: dict) -> str | None:
    """The order this invoice reconciles against.

    Normally the linked PO. Split order: the referenced PO is linked to a
    SIBLING invoice so this draft carries no Loaded link — but the user still
    needs the order's reference data for this delivery, so reconcile against
    ``split_po_id`` WITHOUT touching the link fields.
    """
    return data.get("linked_purchase_order_id") or data.get("split_po_id")


def fetch_po_reference(data: dict, lh) -> None:
    """Cache the linked order's raw rows on the doc (the ONLY network part).

    Stores ``data["po_reference"] = {po_id, order_date, lines, sibling_qty}``.
    Best-effort: a bad fetch leaves the last-known-good reference in place.
    """
    po_id = po_id_for(data)
    if not po_id:
        data.pop("po_reference", None)
        return

    po = lh.get(f"/1.0/stock/internal/purchase-orders/{po_id}")
    if not isinstance(po, dict):
        return  # keep last-known-good reference data on a bad fetch

    # Split order: rows missing from THIS invoice may have been received on
    # the SIBLING delivery. Fetch its quantities so the projection can
    # partition them without a network call of its own.
    sibling_qty: dict[str, object] = {}
    if (
        data.get("split_po_id")
        and not data.get("linked_purchase_order_id")
        and data.get("split_sibling_invoice_id")
    ):
        try:
            sib = lh.invoice(data["split_sibling_invoice_id"])
            for sl in (sib or {}).get("lines") or []:
                if not isinstance(sl, dict) or sl.get("deletedAt"):
                    continue
                for k in (_norm(sl.get("code")), str(sl.get("linkedItemId") or "")):
                    if k:
                        sibling_qty[k] = sl.get("quantityReceived")
        except Exception as exc:  # noqa: BLE001 — reference data is enhancement
            logger.info("split sibling lines unavailable: %s", exc)

    data["po_reference"] = {
        "po_id": po_id,
        "order_date": po.get("createdAt"),
        "lines": [pl for pl in po.get("lines") or [] if isinstance(pl, dict)],
        "sibling_qty": sibling_qty,
    }


def _clear(data: dict) -> None:
    for k in _HEADER_KEYS:
        data.pop(k, None)
    for ln in data.get("lines") or []:
        ln["quantity_ordered"] = None
        ln["reference_cost"] = None
        ln["on_order"] = None
        ln["substitute_for"] = None
        ln["display_code"] = ln.get("code")


def project_po_reference(data: dict) -> None:
    """Recompute the derived projection from the cached order + the CURRENT
    working lines. Pure: no network, no database, safe after every patch.

    Loaded reconciles an invoice line to its purchase-order line by the
    supplier CODE (the exact ordered variant) — NOT the stock item. A
    delivered line whose code isn't on the PO shows no ordered qty even when
    the SAME item was ordered under a different code, and that ordered line
    stays "ordered, not delivered" (verified live: BROCCOLI delivered
    ``VEGF0223`` vs ordered ``165618``). Only a CODELESS invoice line falls
    back to ``itemId`` (so a line Loaded left un-coded still picks up its
    ordered qty, e.g. PORK RACK), and its display code then borrows the PO
    line's ``itemCode``.

    ``ordered_not_received`` lists only the PO items GENUINELY not delivered;
    an item delivered under another code is represented by its substitute
    line, not repeated here.
    """
    lines = data.get("lines") or []
    po_id = po_id_for(data)
    ref = data.get("po_reference")
    ref = ref if isinstance(ref, dict) else None

    if not po_id:
        # No order to reconcile against: clear any stale reference data.
        _clear(data)
        return
    if ref is None:
        # An order, but its rows were never cached (a legacy draft, or the
        # fetch failed). Recomputing is impossible — leave whatever the last
        # successful attach wrote rather than destroying it; the next open
        # re-fetches.
        return
    if ref.get("po_id") != po_id:
        # The invoice was pointed at a DIFFERENT order since the rows were
        # cached: showing another order's numbers would be a lie.
        _clear(data)
        return

    data["order_date"] = ref.get("order_date")
    po_lines = [pl for pl in ref.get("lines") or [] if isinstance(pl, dict)]
    po_by_item: dict[str, dict] = {}
    po_by_code: dict[str, dict] = {}
    for pl in po_lines:
        if pl.get("itemId"):
            po_by_item.setdefault(pl.get("itemId"), pl)
        if pl.get("itemCode"):
            po_by_code.setdefault(_norm(pl.get("itemCode")), pl)

    consumed: set[int] = set()  # id() of PO lines matched to an invoice line
    for ln in lines:
        ln["substitute_for"] = None
        code = _norm(ln.get("code")) if ln.get("code") else ""
        item_id = ln.get("linked_item_id")
        pl = None
        is_sub = False
        if code:
            pl = po_by_code.get(code)
            if not pl and item_id:
                pl = po_by_item.get(item_id)
                is_sub = pl is not None
        elif item_id:
            pl = po_by_item.get(item_id)
        if pl:
            consumed.add(id(pl))
            ln["quantity_ordered"] = pl.get("quantityOrdered")
            ln["reference_cost"] = pl.get("unitCost")
            ln["on_order"] = True
            ln["display_code"] = ln.get("code") or pl.get("itemCode")
            if is_sub:
                # The original ordered line this delivery stands in for —
                # shown as an expandable row under the substitute; NOT also
                # listed as "ordered, not delivered" (it WAS delivered).
                ln["substitute_for"] = {
                    "code": pl.get("itemCode"),
                    "description": pl.get("itemName"),
                    "unit": pl.get("unitName"),
                    "quantity_ordered": pl.get("quantityOrdered"),
                    "unit_cost": pl.get("unitCost"),
                }
        else:
            ln["quantity_ordered"] = None
            ln["reference_cost"] = None
            ln["on_order"] = False
            ln["display_code"] = ln.get("code")

    # PO items with no matching invoice line. Loaded shows these as receivable
    # rows (ordered qty, received 0) regardless of the PO's cumulative
    # received, so mirror that: the full ordered qty, deduped by item.
    ordered_not_received = []
    seen: set = set()
    for pl in po_lines:
        if id(pl) in consumed:
            continue
        key = pl.get("itemId") or _norm(pl.get("itemCode"))
        if key in seen:
            continue
        seen.add(key)
        ordered_not_received.append(
            {
                "code": pl.get("itemCode"),
                "description": pl.get("itemName"),
                "unit": pl.get("unitName"),
                "quantity_ordered": pl.get("quantityOrdered"),
                "unit_cost": pl.get("unitCost"),
                # The PO line's stock item: lets the projection reconcile a
                # line the user has just linked, on the very next patch.
                "item_id": pl.get("itemId"),
            }
        )

    # Split order: partition the rows the SIBLING delivery received.
    ordered_received_elsewhere = []
    sib_qty = ref.get("sibling_qty") or {}
    if ordered_not_received and sib_qty:
        still_missing = []
        for o in ordered_not_received:
            k_code = _norm(o.get("code"))
            k_item = str(o.get("item_id") or "")
            hit = (
                k_code if k_code in sib_qty else (k_item if k_item in sib_qty else None)
            )
            if hit is not None:
                ordered_received_elsewhere.append(
                    {**o, "quantity_received": sib_qty[hit]}
                )
            else:
                still_missing.append(o)
        ordered_not_received = still_missing

    data["ordered_not_received"] = ordered_not_received
    data["ordered_received_elsewhere"] = ordered_received_elsewhere


def enrich_loaded_snapshot(data: dict) -> None:
    """Fill in what Loaded's own Receive Invoice SCREEN shows, on the snapshot.

    Loaded's API returns none of it: its invoice lines carry no ``itemName``
    or ``unitName``, and ``quantityOrdered`` is null on every line (verified
    live on Angus Meats 1010821). The screen resolves those against the
    **linked purchase order** — PO line ``BONES`` → "BONES (STOCK)" / "Kilo" /
    10 — which is why our mirror showed the supplier's raw "Beef Bones" / "KG"
    / "—" instead. The PO rows are already cached by ``fetch_po_reference``,
    so this is pure: no network, no database.

    Resolution is deliberately from the SNAPSHOT's own ids and codes, never
    from the working values — the mirror must keep showing Loaded's truth
    after the user edits or accepts a suggestion. ``item_is_new`` /
    ``unit_is_new`` mark what stayed unresolved, so the view can label it NEW
    exactly as Loaded does. Idempotent.
    """
    snap = data.get("loaded_snapshot")
    if not isinstance(snap, dict):
        return
    snap_lines = [ln for ln in snap.get("lines") or [] if isinstance(ln, dict)]
    if not snap_lines:
        return

    # The cached rows only describe ONE order — use them only when they
    # describe the order this snapshot is actually linked to.
    snap_po = (snap.get("header") or {}).get("linked_purchase_order_id") or po_id_for(
        data
    )
    ref = data.get("po_reference")
    ref = ref if isinstance(ref, dict) and ref.get("po_id") == snap_po else None
    po_by_code: dict[str, dict] = {}
    po_by_item: dict[str, dict] = {}
    for pl in (ref or {}).get("lines") or []:
        if not isinstance(pl, dict):
            continue
        if pl.get("itemCode"):
            po_by_code.setdefault(_norm(pl.get("itemCode")), pl)
        if pl.get("itemId"):
            po_by_item.setdefault(pl.get("itemId"), pl)

    # Working lines are consulted for ONE thing: a stock item's name that
    # attach_item_names already fetched, and only when the working line still
    # carries the same link the snapshot has (so a re-link can't rename the
    # mirror's row).
    working = {
        str(w.get("id")): w
        for w in data.get("lines") or []
        if isinstance(w, dict) and w.get("id")
    }

    for ln in snap_lines:
        pl = None
        if ln.get("linked_item_id"):
            pl = po_by_item.get(ln.get("linked_item_id"))
        if pl is None and ln.get("code"):
            pl = po_by_code.get(_norm(ln.get("code")))

        name = None
        if ln.get("linked_item_id"):
            w = working.get(str(ln.get("id")))
            if w and w.get("linked_item_id") == ln.get("linked_item_id"):
                name = w.get("item_name")
        if not name and pl:
            name = pl.get("itemName")
        ln["item_name"] = name
        ln["item_is_new"] = not (name or ln.get("linked_item_id"))

        if ln.get("linked_unit_id"):
            # Loaded's own `unit` string IS the linked unit's label here.
            ln["unit_name"] = None
            ln["unit_is_new"] = False
        elif pl and pl.get("unitName"):
            ln["unit_name"] = pl.get("unitName")
            ln["unit_is_new"] = False
        else:
            ln["unit_name"] = None
            # A printed unit with no Loaded unit behind it is what Loaded
            # flags NEW; a line with no unit at all has nothing to mark.
            ln["unit_is_new"] = bool(ln.get("unit"))

        ln["quantity_ordered"] = pl.get("quantityOrdered") if pl else None


def attach_po_reference(data: dict, lh) -> None:
    """Fetch the order's rows and project them onto the doc — the open/review
    path. Idempotent; safe to run on every open."""
    fetch_po_reference(data, lh)
    project_po_reference(data)
