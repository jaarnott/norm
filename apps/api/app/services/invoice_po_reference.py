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


def fetch_po_reference(
    data: dict,
    lh,
    po_id: str | None = None,
    sibling_invoice_id: str | None = None,
) -> None:
    """Cache an order's raw rows on the doc (the ONLY network part).

    Stores ``data["po_reference"] = {po_id, order_date, lines, sibling_qty}``.
    Best-effort: a bad fetch leaves the last-known-good reference in place.

    ``po_id`` defaults to the order this invoice is reconciled against. Pass it
    explicitly to cache an order the doc is NOT linked to yet — the one the
    review is about to SUGGEST linking. The projection stays dark while the
    link is absent (it refuses to reconcile against an order the doc doesn't
    point at), and lights up the instant the user accepts, with no round trip
    and nothing to re-analyse.
    """
    po_id = po_id or po_id_for(data)
    if not po_id:
        data.pop("po_reference", None)
        return

    po = lh.get(f"/1.0/stock/internal/purchase-orders/{po_id}")
    if not isinstance(po, dict):
        return  # keep last-known-good reference data on a bad fetch

    # Split order: rows missing from THIS invoice may have been received on
    # the SIBLING delivery. Fetch its quantities so the projection can
    # partition them without a network call of its own.
    #
    # ``sibling_invoice_id`` is passed when pre-caching a SUGGESTED split: the
    # doc carries no split fields until the reference is accepted, and without
    # the sibling's quantities every row it delivered would read "ordered, not
    # delivered" — claiming three items never arrived when they arrived on the
    # other invoice.
    sib_id = sibling_invoice_id or (
        data.get("split_sibling_invoice_id")
        if data.get("split_po_id") and not data.get("linked_purchase_order_id")
        else None
    )
    sibling_qty: dict[str, object] = {}
    if sib_id:
        try:
            sib = lh.invoice(sib_id)
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
        # One order row can only be delivered once. Two invoice lines sharing a
        # code (a split delivery of BONES: 6.43 then 14.15) must not each claim
        # the same ordered quantity — Loaded shows 14 on the first and 0 on the
        # second, and double-counting it overstated what was on order.
        if pl is not None and id(pl) in consumed:
            pl = None
            is_sub = False
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


def resolve_loaded_line(
    catalogue: list[dict] | None, supplier_id: object, code: object
) -> tuple[dict | None, dict | None]:
    """Loaded's own supplier-code → stock item resolution, ported verbatim.

    From the mercury React bundle::

        function i9e(e, t, r) {          // items map, stockCode, supplierId
          for (const n in e) {           // iterate items IN CATALOGUE ORDER
            const i = e[n];
            if (!i.suppliers) continue;
            const s = i.suppliers.filter(
              a => a.supplierId === r && a.stockCode === t);
            if (s.length > 0)
              return s.find(l => l.defaultForSupplier === true) || s[0];
          }
        }

    So: the FIRST item in catalogue order carrying a variant for that
    (supplier, code) wins, and the search stops there. It needs no purchase
    order — matching is supplier-code → variant, nothing else.

    Two things here look like mistakes and are not; do not "fix" them:

    - ``defaultForSupplier`` is an ORDERING flag, not a receiving one. Loaded
      consults it only to break a tie *within the item it already chose*; it
      never competes across items.
    - Consequently an EARLIER item without the flag beats a later item that
      has it. Live proof (Angus Meats 1010951): three items carry code BONES
      for that supplier — BONES (STOCK) first (default False), BONE MARROW
      1 INCH (default True), BONE MARROW - CANOE CUT. Loaded's screen shows
      **BONES (STOCK)**. Resolving via the purchase order instead gives BONE
      MARROW 1 INCH, which is the wrong answer we used to show.

    Returns ``(item, variant)``, or ``(None, None)``.
    """
    want = str(code or "")
    if not want or not supplier_id:
        return None, None
    for item in catalogue or []:
        if not isinstance(item, dict):
            continue
        variants = [
            v
            for v in item.get("suppliers") or []
            if isinstance(v, dict)
            and v.get("supplierId") == supplier_id
            and str(v.get("stockCode") or "") == want
        ]
        if variants:
            default = next(
                (v for v in variants if v.get("defaultForSupplier") is True), None
            )
            return item, (default or variants[0])
    return None, None


def candidates_for_code(
    catalogue: list[dict] | None, supplier_id: object, code: object
) -> list[dict]:
    """EVERY catalogue item carrying a variant for that (supplier, code).

    ``resolve_loaded_line`` answers "which one does Loaded show" — the first.
    This answers "how many could it be", which is a different question and the
    only one that licenses second-guessing Loaded: Angus Meats sells canoe-cut
    marrow, 1-inch marrow and plain bones all under code BONES, prints "Beef
    Bones" on every line, and Loaded's screen calls all of them BONES (STOCK).
    When a code is ambiguous like that the purchase order is the only evidence
    of which was actually bought.

    Catalogue order, so the caller can keep Loaded's own precedence.
    """
    want = str(code or "")
    if not want or not supplier_id:
        return []
    return [
        item
        for item in catalogue or []
        if isinstance(item, dict)
        and any(
            isinstance(v, dict)
            and v.get("supplierId") == supplier_id
            and str(v.get("stockCode") or "") == want
            for v in item.get("suppliers") or []
        )
    ]


def order_rows_for(data: dict) -> list[dict]:
    """The cached order rows for the CURRENTLY linked order, or []."""
    ref = data.get("po_reference")
    if not isinstance(ref, dict) or ref.get("po_id") != po_id_for(data):
        return []
    return [pl for pl in ref.get("lines") or [] if isinstance(pl, dict)]


def claim_row_by_item(
    rows: list[dict], claimed: set[int], item_id: object
) -> dict | None:
    """The first unclaimed order row for that stock item, claimed on the way
    out. Loaded pairs an invoice line to an order row by linked stock item —
    verified against seven human-received invoices at Bessie & Royals, where
    every paired line and row shared an ``itemId`` and the line carried the
    row's ordered quantity. One row can only be delivered once.
    """
    if not item_id:
        return None
    for pl in rows:
        if id(pl) in claimed:
            continue
        if pl.get("itemId") == item_id:
            claimed.add(id(pl))
            return pl
    return None


def _variant_for(item: dict | None, supplier_id: object, code: object) -> dict | None:
    """The supplier variant of an ALREADY-linked item that carries this line's
    code — where its unit (and brand) come from."""
    return next(
        (
            v
            for v in (item or {}).get("suppliers") or []
            if isinstance(v, dict)
            and v.get("supplierId") == supplier_id
            and _norm(v.get("stockCode")) == _norm(code)
        ),
        None,
    )


def enrich_loaded_snapshot(
    data: dict,
    catalogue: list[dict] | None = None,
    units: list[dict] | None = None,
) -> None:
    """Fill in what Loaded's own Receive Invoice SCREEN shows, on the snapshot.

    Loaded's API returns none of it — its invoice lines carry no ``itemName``
    or ``unitName``, and ``quantityOrdered`` is null on every line, including
    from ``/invoices/{id}/initial``, which is what the screen itself loads.
    The screen resolves the stock item **in the browser**, from the catalogue,
    by supplier code (see ``resolve_loaded_line``). The ordered quantity is
    the one thing that does come from the linked order.

    Resolution reads the SNAPSHOT's own ids and codes, never the working
    values — the mirror must keep showing Loaded's truth after the user edits
    or accepts a suggestion. ``item_is_new`` / ``unit_is_new`` mark what
    stayed unresolved, so the view can label it NEW as Loaded does.

    Without a catalogue nothing is guessed: the raw supplier text stands.
    Idempotent.
    """
    snap = data.get("loaded_snapshot")
    if not isinstance(snap, dict):
        return
    snap_lines = [ln for ln in snap.get("lines") or [] if isinstance(ln, dict)]
    if not snap_lines:
        return

    supplier_id = (snap.get("header") or {}).get("linked_supplier_id") or data.get(
        "linked_supplier_id"
    )
    by_item = {
        str(i.get("id")): i
        for i in catalogue or []
        if isinstance(i, dict) and i.get("id")
    }
    unit_names = {
        str(u.get("id")): u.get("name")
        for u in units or []
        if isinstance(u, dict) and u.get("id")
    }

    # The ordered quantity comes from the linked order, and each order row is
    # claimed by the FIRST invoice line that matches it — Loaded shows 14 on
    # the first BONES line and 0 on the second, never 14 twice.
    snap_po = (snap.get("header") or {}).get("linked_purchase_order_id") or po_id_for(
        data
    )
    ref = data.get("po_reference")
    ref = ref if isinstance(ref, dict) and ref.get("po_id") == snap_po else None
    po_rows = [pl for pl in (ref or {}).get("lines") or [] if isinstance(pl, dict)]
    claimed: set[int] = set()

    def claim_po_row(ln: dict) -> dict | None:
        code = _norm(ln.get("code"))
        for pl in po_rows:
            if id(pl) in claimed:
                continue
            if (
                ln.get("linked_item_id") and pl.get("itemId") == ln["linked_item_id"]
            ) or (code and _norm(pl.get("itemCode")) == code):
                claimed.add(id(pl))
                return pl
        return None

    for ln in snap_lines:
        item = variant = None
        if ln.get("linked_item_id"):
            # Already linked in Loaded: the screen shows that item.
            item = by_item.get(str(ln["linked_item_id"]))
            variant = _variant_for(item, supplier_id, ln.get("code"))
        else:
            item, variant = resolve_loaded_line(catalogue, supplier_id, ln.get("code"))

        name = (item or {}).get("name")
        ln["item_name"] = name
        ln["item_is_new"] = bool(catalogue) and not name

        unit_name = unit_names.get(str((variant or {}).get("unitId") or ""))
        if ln.get("linked_unit_id"):
            # Loaded's own `unit` string IS the linked unit's label here.
            ln["unit_name"] = None
            ln["unit_is_new"] = False
        else:
            ln["unit_name"] = unit_name
            # A printed unit with nothing behind it is what Loaded flags NEW;
            # a line with no unit at all has nothing to mark.
            ln["unit_is_new"] = bool(ln.get("unit")) and not unit_name

        pl = claim_po_row(ln)
        ln["quantity_ordered"] = pl.get("quantityOrdered") if pl else None


def seed_working_from_loaded(
    data: dict,
    catalogue: list[dict] | None = None,
    units: list[dict] | None = None,
) -> None:
    """Start the working lines where LOADED'S SCREEN starts.

    Loaded's API hands back an unresolved line — ``linkedItemId`` null, the
    unit as the supplier's raw text ("KG"). Its Receive screen resolves both
    in the browser from the supplier code (``resolve_loaded_line``), and the
    resolved line is what a human sees there and what Loaded receives. Norm's
    draft must therefore OPEN on those same values: seeded from the raw
    payload instead, every code-matched line produced a "link this item"
    suggestion for a link Loaded already agrees with, and the card read as
    disagreement where there was none (Angus Meats 1010951: two BONES lines
    proposing 'BONES (STOCK)' — which is exactly what Loaded shows).

    Suggestions then mean what they say: a difference between the copy and
    **what Loaded holds**.

    Two hard rules:

    - Only what Loaded left EMPTY is filled. An existing link — Loaded's own,
      or the user's — is never overwritten.
    - ``description`` is never touched. The replica's line pairing, item
      matching and create-item prefill all key off the printed text (the same
      reason ``attach_item_names`` leaves it alone); the resolved name rides
      on ``item_name``, which is what the editor renders.

    Fresh payloads only — a re-open must not silently re-apply a link the user
    dismissed. Idempotent.
    """
    lines = [ln for ln in data.get("lines") or [] if isinstance(ln, dict)]
    if not lines or not catalogue:
        return
    supplier_id = data.get("linked_supplier_id")
    if not supplier_id:
        return
    unit_by_id = {
        str(u.get("id")): u for u in units or [] if isinstance(u, dict) and u.get("id")
    }

    by_id = {
        str(i.get("id")): i for i in catalogue if isinstance(i, dict) and i.get("id")
    }

    for ln in lines:
        if ln.get("linked_item_id"):
            # Already linked — Loaded shows THAT item, so only fill in its
            # name (free here; attach_item_names would spend a fetch on it).
            item = by_id.get(str(ln["linked_item_id"]))
            variant = _variant_for(item, supplier_id, ln.get("code"))
            if item and not ln.get("item_name"):
                ln["item_name"] = item.get("name")
                ln["item_name_for"] = item.get("id")
        else:
            item, variant = resolve_loaded_line(catalogue, supplier_id, ln.get("code"))
            if item is not None:
                ln["linked_item_id"] = item.get("id")
                ln["item_name"] = item.get("name")
                # attach_item_names' cache marker: the name is resolved, so it
                # does not spend a fetch re-resolving it.
                ln["item_name_for"] = item.get("id")
                if not ln.get("linked_brand_id") and (variant or {}).get("brandId"):
                    ln["linked_brand_id"] = variant["brandId"]
                    ln["brand"] = variant.get("brandName") or ln.get("brand")

        unit = unit_by_id.get(str((variant or {}).get("unitId") or ""))
        if unit and not ln.get("linked_unit_id"):
            ln["linked_unit_id"] = unit.get("id")
            ln["unit"] = unit.get("name") or ln.get("unit")
            if unit.get("ratio") is not None:
                ln["unit_ratio"] = unit.get("ratio")


_REFERENCE_TTL = 300.0
_reference_cache: dict[str, tuple[float, list, list]] = {}


def loaded_reference(venue_id: str, db, lh, config_db=None) -> tuple[list, list]:
    """``(catalogue, units)`` for one venue — the two lists Loaded's own screen
    resolves against.

    Cached briefly per venue: every draft open, every invoice in a batch and
    every mirror refresh wants the same pair, and the catalogue is a
    thousand-item fetch. Best-effort in both halves — an empty catalogue means
    "do not guess", never a failed open.
    """
    import time

    from app.services.item_match import _fetch_raw_stock_items

    hit = _reference_cache.get(venue_id)
    if hit and (time.monotonic() - hit[0]) < _REFERENCE_TTL:
        return hit[1], hit[2]

    catalogue: list = []
    units: list = []
    cdb, owned = config_db, False
    if cdb is None:
        from app.db.engine import _ConfigSessionLocal

        cdb, owned = _ConfigSessionLocal(), True
    try:
        catalogue = _fetch_raw_stock_items(venue_id, db, cdb) or []
    except Exception as exc:  # noqa: BLE001 — resolution is best-effort
        logger.info("loaded reference: catalogue unavailable: %s", exc)
    finally:
        if owned:
            cdb.close()
    try:
        rows = lh.get("/1.0/stock/internal/units")
        units = rows if isinstance(rows, list) else []
    except Exception as exc:  # noqa: BLE001
        logger.info("loaded reference: units unavailable: %s", exc)
    if catalogue:
        _reference_cache[venue_id] = (time.monotonic(), catalogue, units)
    return catalogue, units


def attach_po_reference(data: dict, lh) -> None:
    """Fetch the order's rows and project them onto the doc — the open/review
    path. Idempotent; safe to run on every open."""
    fetch_po_reference(data, lh)
    project_po_reference(data)
