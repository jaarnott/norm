"""What the invoice copy says — from whatever Norm already knows.

Reconciliation and receiving read the same piece of paper. Receiving reads it
with the supplier's own extraction spec (trained in the dojo) and stores the
result verbatim on the invoice's working document; reconciliation was reading
it again with a private five-field schema and one generic instruction. Two
consequences, both measured on the 17 Aug 2026 run:

- The private schema asks for a single ``purchase_order_number``. ``PDF_SCHEMA``
  splits that in two ON PURPOSE — ``customer_purchase_order_number`` (ours, the
  one that matches a Loaded PO) and ``supplier_order_number`` (theirs, kept as
  a decoy so it can never be misfiled as ours). Without the split, an invoice
  printing ORD10658598 above our 1520599 reads as a PO mismatch. That was 27 of
  67 failures.
- Schema and instructions are both cache-key material, so the two paths shared
  no cache rows: every copy the receive flow had already read was read and paid
  for again, one model call at a time.

So this module is deliberately a READER of the receive path, never a fork of
it. It adds nothing to the extraction contract and changes nothing about how
receiving works — it looks up what receiving already produced, and when there
is nothing to find it calls the same extractor with the same schema and the
same per-supplier instructions, landing on the same cache row.
"""

from __future__ import annotations

import datetime
import logging
import re

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Where the header came from, reported per invoice so a run can be audited:
#: "norm read this months ago" is a very different cost from "we just read it".
SOURCE_STORED = "stored"
SOURCE_EXTRACTED = "extracted"

# Brand-new suppliers the sensei may train in one reconciliation run. Matches
# the receive consolidator's `max_sensei`, for the same reason: training reads
# a PDF and calls the LLM, so a venue meeting a dozen new suppliers at once
# should spread that over runs rather than pay for it in one morning. The
# backlog is not lost — the next run picks up where this one stopped.
MAX_SENSEI_PER_RUN = 2


def _as_utc(value: object) -> datetime.datetime | None:
    """A timezone-aware UTC datetime from a stored value, or None.

    Snapshots carry an ISO string, rows carry a datetime, and older rows carry
    a naive one — comparing a naive to an aware datetime raises, which on this
    path would sink a whole reconciliation run.
    """
    if isinstance(value, str):
        try:
            value = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime.datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc)


def _read_under_an_older_prompt(doc, stale_before) -> bool:
    """Was this snapshot read before the supplier's instructions last changed?

    Compared against when the REVIEW ran, not the row's ``updated_at``: a
    document is touched by later edits (accepting a suggestion, receiving it)
    that do not re-read the copy, and those would make a stale extraction look
    current.
    """
    if stale_before is None:
        return False
    when = (doc.data or {}).get("reviewed_at")
    read_at = _as_utc(when) or _as_utc(doc.created_at)
    if read_at is None:
        return True  # cannot date it — do not gamble on it being current
    return read_at < stale_before


def _supplier_alias_cache(lh, config_db: Session):
    """``inv -> {"key", "detail", "aliases", "stale_before"}``, once per supplier.

    Both halves of ``copy_headers`` need the same answer — the stored-snapshot
    check needs to know when this supplier's prompt last moved, and the
    extraction needs the aliases that compose it — and each answer costs a
    Loaded call. Resolved once here and shared.

    ``stale_before`` is the newest of the supplier's spec and the main prompt:
    either one changes the instructions, so a snapshot read before it is out of
    date. It is deliberately NOT the newest spec in the roster — a fix to one
    supplier must not re-read every other supplier's invoices.
    """
    from app.services.invoice_extraction import find_spec_for_supplier
    from app.services.invoice_review import account_suppliers, supplier_aliases

    suppliers = account_suppliers(lh) if lh is not None else []
    main_changed = _main_prompt_changed_at(config_db)
    cache: dict[tuple, dict] = {}

    def resolve(inv: dict) -> dict:
        name = str(inv.get("supplierName") or "")
        sup_id = inv.get("supplierId") or inv.get("linkedSupplierId")
        key = (name, str(sup_id or ""))
        if key not in cache:
            detail = {"supplierName": name or None, "linkedSupplierId": sup_id}
            aliases = supplier_aliases(lh, detail, suppliers) if lh is not None else []
            spec_changed = None
            if config_db is not None and name:
                try:
                    spec = find_spec_for_supplier(config_db, name, *aliases)
                    spec_changed = _as_utc(getattr(spec, "updated_at", None))
                except Exception as exc:  # noqa: BLE001 — freshness is advisory
                    logger.info("spec freshness unavailable for '%s': %s", name, exc)
            stamps = [t for t in (spec_changed, main_changed) if t is not None]
            cache[key] = {
                "key": key,
                "detail": detail,
                "aliases": aliases,
                "stale_before": max(stamps) if stamps else None,
            }
        return cache[key]

    return resolve


def _main_prompt_changed_at(config_db: Session) -> datetime.datetime | None:
    """When the shared main extraction prompt last changed — it is in every
    supplier's instructions, so editing it dates every snapshot."""
    if config_db is None:
        return None
    try:
        from app.db.config_models import SupplierInvoiceSpec
        from app.services.invoice_extraction import MAIN_PROMPT_NAME

        row = (
            config_db.query(SupplierInvoiceSpec)
            .filter(SupplierInvoiceSpec.name == MAIN_PROMPT_NAME)
            .first()
        )
        return _as_utc(getattr(row, "updated_at", None))
    except Exception as exc:  # noqa: BLE001 — freshness is advisory
        logger.info("main prompt freshness unavailable: %s", exc)
        return None


def stored_header(
    db: Session,
    venue_id: str,
    invoice_id: str,
    stale_before: datetime.datetime | None = None,
) -> dict | None:
    """The extraction the RECEIVE flow already made for this invoice, if any.

    `invoice_review` writes it verbatim onto the working document as
    `extracted_snapshot` — same schema, same spec-composed instructions, and no
    LLM call to repeat. Deleted and superseded documents are skipped, but a
    RECEIVED one is exactly what reconciliation wants: the invoice is received,
    that is why it is being reconciled.

    ``stale_before`` is when this supplier's instructions last changed. A
    snapshot older than that was read under a prompt that no longer exists, and
    reusing it makes the saving permanent: six Kaans invoices read on 17-21 Aug
    2026 carried no PO, and because reconciliation kept preferring those
    snapshots it never re-read the copies — so fixing the spec on 26 Aug healed
    only the three invoices extracted after it, and the other six failed every
    morning against a prompt nobody could see. The extraction CACHE is keyed on
    the instructions and would have re-read them; this path bypasses the cache,
    so it has to ask the same question itself.
    """
    from app.db.models import WorkingDocument

    rows = (
        db.query(WorkingDocument)
        .filter(
            WorkingDocument.doc_type == "received_invoice",
            WorkingDocument.venue_id == venue_id,
        )
        .all()
    )
    docs = [
        d
        for d in rows
        if (d.external_ref or {}).get("invoice_id") == invoice_id
        and not (d.data or {}).get("is_deleted")
        and not _read_under_an_older_prompt(d, stale_before)
    ]
    if not docs:
        return None
    # Newest first; a doc that carries a review is better evidence than a bare
    # draft that never got one.
    docs.sort(
        key=lambda d: (
            bool((d.data or {}).get("reviewed_at")),
            d.updated_at or d.created_at,
        ),
        reverse=True,
    )
    header = ((docs[0].data or {}).get("extracted_snapshot") or {}).get("header")
    return dict(header) if isinstance(header, dict) else None


def copy_headers(
    db: Session,
    config_db: Session,
    lh,
    venue_id: str,
    invoices: list[dict],
) -> dict[str, dict]:
    """``{invoice_id: header}`` for every invoice, cheapest source first.

    ``invoices`` items need ``id`` and may carry ``fileId``, ``supplierName``,
    ``supplierId`` and ``purchaseOrderNumber``. ``supplierId`` is what buys the
    supplier's Loaded aliases, and those decide which spec is found — without
    it a supplier filed under a different spelling reads with the generic
    prompt. The returned header is a ``PDF_SCHEMA`` header plus ``_source``
    (stored | extracted), ``_po_verdict``/``_po_note``, and on failure
    ``error``.

    The PO verdict is stamped HERE rather than by the caller: the consolidator
    that consumes this runs sandboxed and cannot import anything, and the rule
    for telling our PO number from the supplier's is worth having in exactly
    one tested place.
    """
    from app.services.invoice_extraction import extract_invoice_copies_parallel

    out: dict[str, dict] = {}
    pending: list[dict] = []
    aliases_for = _supplier_alias_cache(lh, config_db)

    for inv in invoices:
        iid = str(inv.get("id") or "")
        if not iid:
            continue
        # Ask per SUPPLIER when its prompt last moved, so only the invoices
        # whose spec has changed since they were read get re-read.
        header = stored_header(db, venue_id, iid, aliases_for(inv)["stale_before"])
        if header is not None:
            out[iid] = {**header, "_source": SOURCE_STORED}
            continue
        if not inv.get("fileId"):
            out[iid] = {
                "error": "no invoice copy attached",
                "_source": SOURCE_EXTRACTED,
            }
            continue
        pending.append(inv)

    if pending:
        # THE receive path's composer, not a second implementation. This module
        # exists to read the copy with the supplier's spec, but it composed from
        # Loaded's feed spelling alone: 'Kaans Catering' matches no spec, so
        # reconciliation ran the generic prompt (2818 chars) while receiving ran
        # the spec's (6315) carrying Kaans' 'External Document No.' rule.
        # Instructions are CACHE-KEY material, so it was self-sealing — with the
        # spec absent from the key, fixing it in the dojo invalidated nothing
        # and the pre-fix answer was served for ever. Composing identically
        # puts both paths on one cache row, as the docstring already promises.
        #
        # The sensei runs here too, on the same terms receiving uses: a
        # spec-less supplier is a spec-less supplier, whether the invoice came
        # from Bidfood or from a sister venue. Both paths are unattended
        # (receiving runs under the autopilot ladder), so the budget, not the
        # audience, is what bounds it.
        from app.services.invoice_review import (
            _maybe_sensei,
            extraction_instructions,
        )

        # One instruction set per SUPPLIER, not per invoice: composing it hits
        # the config DB for the spec and Loaded for the aliases, and a run is
        # mostly a handful of suppliers with many invoices each. The aliases
        # were already resolved above for the staleness check — same cache.
        by_supplier: dict[tuple, str] = {}
        requests = []
        budget = MAX_SENSEI_PER_RUN
        for inv in pending:
            ctx = aliases_for(inv)
            name = str(inv.get("supplierName") or "")
            key = ctx["key"]
            if key not in by_supplier:
                detail, aliases = ctx["detail"], ctx["aliases"]
                # BEFORE the instructions are composed — a spec trained now has
                # to be in this pass's cache key, not the next one's.
                if (
                    name
                    and budget > 0
                    and inv.get("fileId")
                    and _maybe_sensei(
                        db, config_db, venue_id, str(inv.get("id")), name, *aliases
                    )
                ):
                    budget -= 1
                by_supplier[key] = extraction_instructions(
                    config_db, lh, detail, aliases
                )
            requests.append(
                {
                    "file_id": inv.get("fileId"),
                    "instructions": by_supplier[key],
                    "venue_key": venue_id,
                }
            )
        results = extract_invoice_copies_parallel(db, lh, requests)
        for inv, res in zip(pending, results):
            res = res if isinstance(res, dict) else {"error": "unreadable"}
            out[str(inv["id"])] = {**res, "_source": SOURCE_EXTRACTED}

    by_id = {str(i.get("id")): i for i in invoices if i.get("id")}
    for iid, header in out.items():
        if header.get("error"):
            continue
        state, note = po_verdict(
            (by_id.get(iid) or {}).get("purchaseOrderNumber"), header
        )
        header["_po_verdict"], header["_po_note"] = state, note

    # Only an `absent` verdict can be overturned by a split-order note, and the
    # note lives on the invoice DETAIL — the received-invoice feed carries no
    # `notes` field. So the extra read is paid only for the invoices it could
    # actually rescue, in parallel, and a note that cannot be read simply
    # leaves the verdict where it was.
    absent = [i for i, h in out.items() if h.get("_po_verdict") == "absent"]
    if absent:
        details = _invoice_details(lh, absent)
        cached = _CachedGets(lh)
        for iid in absent:
            header, detail = out[iid], details.get(iid) or {}
            notes = str(detail.get("notes") or "")
            if notes:
                state, note = po_verdict(
                    (by_id.get(iid) or {}).get("purchaseOrderNumber"), header, notes
                )
                header["_po_verdict"], header["_po_note"] = state, note
                if state == "match":
                    continue
            if not detail:
                continue
            # No usable note. Establish the split from Loaded itself — the note
            # is only ever written when an invoice was received through Norm's
            # split path, which is 1 of 18 in practice (Bessie & Engineers,
            # 23 Aug 2026), while 13 of those 18 were provably splits.
            verdict = split_verdict(cached, detail, header)
            if not verdict:
                continue
            kind, data = verdict
            header["_split"] = {**data, "kind": kind}
            if kind == "split":
                header["_po_verdict"] = "match"
                header["_po_note"] = (
                    f"split delivery — order {data['order_number']} is linked to "
                    f"{data['sibling_reference']} from the same order"
                )
            else:
                # A doubled-up reference is a possible DUPLICATE invoice, not a
                # split. Never reconciled on that basis; reported for a person.
                header["_po_note"] = (
                    f"order {data['order_number']} is already fully invoiced by "
                    f"{data['sibling_reference']} — possible duplicate invoice"
                )

    return out


#: The marker receiving already writes (``services/received_invoice.py``), so
#: reconciliation leaves the SAME sentence and ``split_order_number`` reads
#: either without knowing which wrote it.
SPLIT_NOTE = "Split order: order {order} also covers {sibling}"


def record_split(
    lh, invoice_id: str, order_number: str, sibling_reference: str
) -> dict:
    """Record a confirmed split on the invoice that has no PO.

    Two writes with very different standing, and the difference is the point:

    - the NOTE is the durable one. Verified 25 Aug 2026: a user opening the
      received invoice, confirming the stocktake dialog and pressing Save keeps
      the note (and their own edit to it).
    - the REFERENCE is a convenience for whoever reads Loaded's invoice list,
      where it renders in the Order Number column exactly like a linked order.
      The SAME save wipes it. Nothing may depend on it.

    ``linkedPurchaseOrderId`` is never touched: Loaded models PO↔invoice as
    1:1 and the link belongs to the sibling. Writing it here would steal it.

    Idempotent — an invoice already carrying the marker and a reference is left
    alone, so a daily run does not rewrite the same invoice forever.
    """
    detail = lh.invoice(invoice_id)
    if not isinstance(detail, dict) or not detail.get("id"):
        return {"ok": False, "error": "invoice could not be read"}

    marker = SPLIT_NOTE.format(
        order=order_number, sibling=sibling_reference or "another invoice"
    )
    notes = str(detail.get("notes") or "")
    add_note = marker not in notes
    add_ref = not detail.get("purchaseOrderNumber")
    if not add_note and not add_ref:
        return {"ok": True, "unchanged": True}

    body = dict(detail)
    if add_note:
        body["notes"] = (notes + "\n" if notes else "") + marker
    if add_ref:
        body["purchaseOrderNumber"] = str(order_number)
    lh.request("PUT", f"/1.0/stock/internal/invoices/{invoice_id}", body)
    return {"ok": True, "noted": add_note, "referenced": add_ref}


class _CachedGets:
    """``lh`` with GET memoisation for the life of one batch.

    ``resolve_po_id`` scans the same three lists (open POs, drafts, the
    received feed) for every number it is asked about. A venue with 34 blocked
    invoices would otherwise fetch each list 34 times.
    """

    def __init__(self, lh):
        self._lh = lh
        self._seen: dict[str, object] = {}

    def get(self, path: str):
        if path not in self._seen:
            self._seen[path] = self._lh.get(path)
        return self._seen[path]

    def __getattr__(self, name):
        return getattr(self._lh, name)


def split_verdict(lh, invoice: dict, header: dict) -> tuple[str, dict] | None:
    """Why an invoice has no PO — ``split`` | ``doubled_up``, or None.

    Reuses the receive path's classifier rather than re-deciding: the rule for
    telling a genuine split delivery from a doubled-up invoice already exists,
    is tested, and belongs in one place. Loaded models PO↔invoice as 1:1, so
    when one order arrives across several invoices only the first can hold the
    link.

    None means there is nothing to explain — the number on the copy resolves to
    no Loaded order at all (a supplier's own reference, like Ocean's North's
    standing order 631518146), or the order is this invoice's own.
    """
    from app.services.invoice_replica import _sibling_doubled_up
    from app.services.received_invoice import resolve_po_id

    printed = header.get("customer_purchase_order_number") or header.get(
        "supplier_order_number"
    )
    if not printed:
        return None
    try:
        resolved = resolve_po_id(lh, printed, invoice.get("linkedSupplierId"))
    except Exception as exc:  # noqa: BLE001 — an unresolvable number is not a split
        logger.info("split resolve failed for %s: %s", printed, exc)
        return None
    if not resolved:
        return None
    sibling = resolved.get("linked_invoice_id")
    if not sibling or sibling == invoice.get("id"):
        return None

    lines = [
        {
            "code": ln.get("code"),
            "description": ln.get("description"),
            "quantity_received": ln.get("quantityReceived"),
            # The detail's spelling; the classifier understands both sides.
            "unit_cost": (
                ln.get("unitCost")
                if ln.get("unitCost") is not None
                else ln.get("unitCostExclTax")
            ),
        }
        for ln in invoice.get("lines") or []
        if isinstance(ln, dict) and not ln.get("deletedAt")
    ]
    doubled, sib_ref = _sibling_doubled_up(lh, sibling, lines, header)
    data = {
        "order_number": str(resolved.get("order_number") or printed),
        "po_id": resolved.get("id"),
        "sibling_invoice_id": sibling,
        "sibling_reference": sib_ref,
    }
    return ("doubled_up" if doubled else "split"), data


def _invoice_details(lh, invoice_ids: list[str]) -> dict[str, dict]:
    """``{invoice_id: detail}``, fetched in parallel. Best effort by design:
    this only ever ADDS evidence, so a failed read costs a rescue, never a
    wrong reconcile. The detail carries both the ``notes`` and the lines the
    split classifier compares — the received-invoice feed carries neither."""
    from concurrent.futures import ThreadPoolExecutor

    def fetch(invoice_id: str) -> tuple[str, dict]:
        try:
            return invoice_id, (lh.invoice(invoice_id) or {})
        except Exception as exc:  # noqa: BLE001 — what we cannot read is no evidence
            logger.info("split evidence read failed for %s: %s", invoice_id, exc)
            return invoice_id, {}

    with ThreadPoolExecutor(max_workers=min(8, len(invoice_ids))) as pool:
        return dict(pool.map(fetch, invoice_ids))


#: The note ``do_receive`` stamps on a split delivery's other invoice —
#: ``"Split order: order 1521169 also covers IN11411819"``
#: (``services/received_invoice.py``). Loaded models PO↔invoice as 1:1, so when
#: one purchase order is delivered across several invoices only the first can
#: hold the link and the rest carry an empty PO field forever. The note is the
#: record of WHY it is empty, written at receive time by the code that saw it.
_SPLIT_ORDER_RE = re.compile(r"split order:\s*order\s+(\S+)\s+also covers", re.I)


def split_order_number(notes: object) -> str | None:
    """The order number recorded in a ``Split order:`` note, or None."""
    match = _SPLIT_ORDER_RE.search(str(notes or ""))
    return match.group(1) if match else None


def po_verdict(
    loaded_po: object, header: dict, split_note: object = None
) -> tuple[str, str]:
    """``(verdict, note)`` for one invoice's PO — ``match`` | ``mismatch`` | ``absent``.

    Reconcile only when Norm can tell OUR number from the supplier's and they
    agree. Two ways that happens, and both are a real match:

    - the copy's customer PO equals Loaded's — the ordinary case;
    - the copy's SUPPLIER order number equals Loaded's — Loaded's own
      `purchaseOrderNumber` is frequently the supplier's number rather than
      ours (documented in tests/test_invoice_fixes_handler.py), so the two
      sides are naming the same document by the supplier's name for it.

    A third route applies only when Loaded holds nothing: a SPLIT DELIVERY,
    where ``split_note`` is this invoice's ``notes`` field and names the order
    the copy prints. See ``split_order_number``.

    Anything else is left as a mismatch for a person. Two numbers that are
    genuinely different is exactly what this check is for.
    """
    from app.services.received_invoice import _po_key

    ours = _po_key(header.get("customer_purchase_order_number"))
    theirs = _po_key(header.get("supplier_order_number"))
    loaded = _po_key(loaded_po)

    if not loaded:
        # A SPLIT DELIVERY, not a missing PO. Loaded's field is empty because
        # the order is linked to a sibling invoice, and receiving recorded that
        # at the time. Reconcile only when the order named in the note is the
        # one printed on this copy — the note alone proves a split happened,
        # the match proves it is THIS invoice's split. Measured 23 Aug 2026:
        # 13 of 18 blocked invoices at Bessie & Engineers were splits.
        split = split_order_number(split_note)
        if split and _po_key(split) in {k for k in (ours, theirs) if k}:
            return "match", (
                f"split delivery — order {split} is linked to another invoice "
                "from the same order, and this copy names that order"
            )
        found = header.get("customer_purchase_order_number") or header.get(
            "supplier_order_number"
        )
        return "absent", (
            f"Received invoice has no PO number (the copy shows {found})"
            if found
            else "No PO number on the received invoice or the invoice copy"
        )
    if ours and ours == loaded:
        return "match", ""
    if theirs and theirs == loaded:
        return "match", (
            "matched on the supplier's own order number — Loaded's PO field "
            f"holds {header.get('supplier_order_number')}, not a Loaded order"
        )
    if ours:
        return "mismatch", (
            f"PO number mismatch: received invoice PO#{loaded_po} vs invoice "
            f"copy {header.get('customer_purchase_order_number')}"
        )
    return "mismatch", (
        f"PO number mismatch: received invoice PO#{loaded_po} vs invoice copy "
        f"{header.get('supplier_order_number') or '(none found)'}"
    )
