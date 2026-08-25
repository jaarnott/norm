"""Build a full received-invoice working document from OUR extraction alone.

The replica is Norm's own ingestion: it takes the PDF extraction result
(lines / totals / supplier / PO as printed) and resolves everything Loaded's
receive-time matcher resolves — supplier record, stock item + supplier
variant per line, unit record + ratio, tax rate, PO reference — against the
venue catalogue via the API, producing the same shape as
``received_invoice.build_received_invoice_data``.

Used by the DOJO (every sample run builds a replica and scores it against
what Loaded resolved for the same invoice) and by the LIVE review path
(``invoice_review``), where the replica is the single engine behind the
working document's suggestions and confidence issues. Structured findings
land in ``issues`` (``{id, code, blocking, line_id, message, data}``);
``warnings`` keeps the human-prose mirror for the dojo UI.

Resolution order per line (conservative → generous):
1. Deterministic catalogue match (``invoice_line_match.CatalogueIndex``):
   supplier-scoped variant stockCode → unscoped unique code → exact
   description → ≥8-char substring, unique hits only.
2. LLM fallback (``item_match.suggest_item_matches``) for the unresolved
   tail — supplier-aware; returns a match or a create suggestion.
3. Unit: the matched variant's unit first, else the extracted unit text
   resolved against the venue's unit records (name → multipack →
   magnitude — the ``invoice_units`` tiers).
4. Tax: the matched item's ``globalSalesTaxSortOrder`` decoded via the
   sales-tax API (verified live: Exempt 0.0 / GST 0.15); unmatched lines
   take the invoice's prevailing rate, else the copy-derived rate.

Every decision lands in ``resolution_log`` — the troubleshooting record.
Never raises: any stage degrades and logs.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy.orm import Session

from app.services import invoice_line_match as line_match
from app.services.supplier_identity import (
    alias_candidates,
    match_spec,
    norm as _identity_norm,
    resolve_supplier,
)
from app.services.invoice_units import (
    _unit_norm,
    is_multipack,
    is_packaging_word,
    multipack_equal,
    parse_unit,
)

logger = logging.getLogger(__name__)

_APP_HOST = "https://loadedhub.com"


def _norm(text: object) -> str:
    return _identity_norm(text)


# The extraction keeps dates AS PRINTED ('7 Aug 2026', '05.08.2026',
# '06 Aug 26', 'Aug 7, 2026', '07/08/26' — all live-observed); the replica is
# the resolved document, so it stores ISO like Loaded does. Day-first for
# numeric forms — NZ suppliers.
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d %b %Y",
    "%d %B %Y",
    "%d %b %y",
    "%d %B %y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%B %d %Y",
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%y",
    "%d/%m/%y",
    "%d-%m-%y",
)


def _iso_date(v: object) -> str | None:
    """A printed date → 'YYYY-MM-DD'; unparseable text stays verbatim (an
    honest diff beats a silently dropped date)."""
    import datetime as _dt

    if not v:
        return None
    s = " ".join(str(v).split())
    for fmt in _DATE_FORMATS:
        try:
            return (
                _dt.datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt)
                .date()
                .isoformat()
            )
        except ValueError:
            continue
    return s


def _credit_signals(extraction: dict, loaded_total: object = None) -> list[str]:
    """Which signals say this document is a CREDIT NOTE — any one is enough.

    Loaded ingests credit notes as ordinary drafts (it OCRs the PDF; there is
    no b2b feed) and usually keeps the printed positive numbers, so the
    extraction's own classification is the common signal. The other two catch
    documents that print negative, or that Loaded itself read as negative —
    Loaded's own rule is simply ``total < 0``.
    """
    signals: list[str] = []
    if str(extraction.get("document_type") or "").lower() == "credit_note":
        signals.append("document_type")
    printed = extraction.get("total_incl_tax")
    if isinstance(printed, (int, float)) and printed < 0:
        signals.append("printed_total")
    if isinstance(loaded_total, (int, float)) and loaded_total < 0:
        signals.append("loaded_total")
    return signals


def _credit_normalise(extraction: dict) -> dict:
    """Put a credit note into Loaded's sign space: quantities, line totals and
    the header totals NEGATIVE, unit costs POSITIVE.

    That shape is not a guess — it is what all 18 live credit notes across the
    three venues store (a unit cost is a price, never a credit). Loaded's own
    header subtotal/tax are inconsistent in its records (null on 8, positive
    on 7 while the total is negative, correctly negative on 3); we always
    produce the coherent form, which is also what ``receive_request_from_doc``
    derives from the line totals anyway.

    Normalisation is PER SCOPE and idempotent: a sign is only forced where the
    document prints unsigned, so a credit note that already prints negatives
    passes through untouched and normalising twice changes nothing. Returns a
    NEW dict — the caller's as-printed extraction is the audit trail
    (``extracted_snapshot``) and must not be mutated.
    """

    def _num(v: object) -> float | None:
        return float(v) if isinstance(v, (int, float)) else None

    out = dict(extraction)
    lines = [dict(ln) for ln in (extraction.get("lines") or []) if isinstance(ln, dict)]
    lines_signed = any((_num(ln.get("line_total_ex_tax")) or 0) < 0 for ln in lines)
    for ln in lines:
        price = _num(ln.get("unit_price_ex_tax"))
        if price is not None:
            ln["unit_price_ex_tax"] = abs(price)
        total = _num(ln.get("line_total_ex_tax"))
        if total is not None and not lines_signed:
            total = -abs(total)
            ln["line_total_ex_tax"] = total
        qty = _num(ln.get("quantity"))
        if qty is not None:
            # The quantity follows its own line's sign, so a mixed credit note
            # (a restocking charge among the credits) keeps that line positive.
            ln["quantity"] = abs(qty) if (total or 0) > 0 else -abs(qty)
    out["lines"] = lines

    if not (_num(extraction.get("total_incl_tax")) or 0) < 0:
        # discount flips with the rest: it sits inside the identity
        # line_sum + tax − discount = total, so negating both sides negates it.
        for key in (
            "subtotal_ex_tax",
            "tax_amount",
            "total_incl_tax",
            "discount_amount",
        ):
            v = _num(extraction.get(key))
            if v is not None:
                out[key] = -abs(v)
    return out


def sales_tax_rates(lh) -> dict[int, float]:
    """The venue's tax table from ``loadedhub.com/api/sales-tax`` (same auth
    headers as the api host — verified live 09 Aug 2026):
    ``{sortOrder: rate}``, e.g. {0: 0.0, 1: 0.15}. Empty on failure."""
    try:
        r = httpx.get(
            f"{_APP_HOST}/api/sales-tax",
            headers=lh._headers,
            auth=lh._auth,
            timeout=30.0,
        )
        if r.status_code >= 400:
            return {}
        return {
            int(row["sortOrder"]): float(row["rate"])
            for row in r.json()
            if isinstance(row, dict) and row.get("sortOrder") is not None
        }
    except Exception as exc:  # noqa: BLE001 — tax table is an enhancement
        logger.warning("sales-tax fetch failed: %s", exc)
        return {}


def _received_feed(lh) -> list:
    """The received/invoiced-invoices feed (same window as ``resolve_po_id``'s
    feed pass) — the duplicate gate's source of truth."""
    import datetime as _dt

    today = _dt.date.today()
    frm = (today - _dt.timedelta(days=400)).isoformat()
    to = (today + _dt.timedelta(days=1)).isoformat()
    feed = lh.get(
        "/1.0/stock/internal/stock-received"
        f"?from={frm}&to={to}&property=Invoiced"
        "&includeAdjustingInvoices=true&ifNoneGetLastReceived=false"
    )
    return feed if isinstance(feed, list) else (feed or {}).get("data") or []


def fetch_brands(lh) -> list[dict]:
    """``GET /1.0/stock/internal/brands`` (verified live 09 Aug 2026):
    ``[{id, name, masterId, datestampDeleted}]`` — soft-deleted dropped."""
    try:
        rows = lh.get("/1.0/stock/internal/brands")
        rows = rows if isinstance(rows, list) else []
        return [
            r for r in rows if isinstance(r, dict) and not r.get("datestampDeleted")
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("brands fetch failed: %s", exc)
        return []


def _resolve_unit_record(name: object, units: list[dict]) -> dict | None:
    """Unit NAME → the venue's unit record, mirroring invoice_fixes._resolve_unit
    tiers over a pre-fetched list: exact normalized name → multipack
    component-equivalence → (type, magnitude) for simple units."""
    if not name:
        return None
    live = [u for u in units if isinstance(u, dict) and not u.get("datestampDeleted")]
    key = _unit_norm(name)
    for u in live:
        if _unit_norm(u.get("name")) == key:
            return u
    if is_multipack(name):
        for u in live:
            if multipack_equal(u.get("name"), name):
                return u
        return None
    parsed = parse_unit(name)
    if parsed:
        for u in live:
            # Magnitude equivalence must not launder identity: 'EA' parses as
            # a count of 1 and so does a unit literally named 'PACK', but
            # receiving each-lines in PACK is meaningless (Trents 5973784,
            # 18 Aug 2026 — Dunedin's PACK unit swallowed every EA line). A
            # packaging-word-NAMED unit can only be chosen by printing its
            # name exactly (the name tier above).
            if is_packaging_word(u.get("name")):
                continue
            pu = parse_unit(u.get("name"))
            if pu and pu[0] == parsed[0] and abs(pu[1] - parsed[1]) < 0.001:
                return u
    return None


def _sibling_doubled_up(
    lh, sibling_invoice_id: str, replica_lines: list[dict], extraction: dict
) -> tuple[bool, str]:
    """The engine's split-order classifier, replica-side.

    The referenced PO is claimed by a sibling invoice: doubled-up means every
    replica line pairs (``plain_match`` — the same tiers as the engine) with a
    sibling line at the same quantity and cost AND the totals agree; anything
    less — unreadable sibling, unpaired line, quantity/cost/total drift — is a
    genuine split. Any doubt → split, never removal. Returns
    ``(doubled_up, sibling_reference)``."""

    def _close(a: object, b: object, tol: float = 0.011) -> bool:
        try:
            return abs(float(a) - float(b)) <= tol  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False

    try:
        sib = lh.invoice(sibling_invoice_id)
    except Exception:  # noqa: BLE001 — unreadable sibling → split
        return False, "another invoice"
    if not isinstance(sib, dict):
        return False, "another invoice"
    sib_ref = sib.get("referenceNumber") or "another invoice"
    pool = [
        {"code": sl.get("code"), "description": sl.get("description"), "_sl": sl}
        for sl in sib.get("lines") or []
        if isinstance(sl, dict) and not sl.get("deletedAt")
    ]
    if not replica_lines:
        return False, sib_ref
    for ln in replica_lines:
        m = line_match.plain_match(
            {"code": ln.get("code"), "description": ln.get("description")}, pool
        )
        if m is None:
            return False, sib_ref
        sl = m["_sl"]
        if not _close(sl.get("quantityReceived"), ln.get("quantity_received"), 0.001):
            return False, sib_ref
        # Loaded's invoice DETAIL returns unitCostExclTax; `unitCost` is only
        # on the received-invoice FEED. Reading one name meant the cost check
        # could never pass on a real sibling, so every doubled-up invoice was
        # classified a split and the reference was never removed. Verified
        # against live invoices in two venues, 25 Aug 2026 — unitCost was None
        # on every detail line.
        sib_cost = (
            sl.get("unitCost")
            if sl.get("unitCost") is not None
            else sl.get("unitCostExclTax")
        )
        if not _close(sib_cost, ln.get("unit_cost")):
            return False, sib_ref
    if not _close(sib.get("total"), extraction.get("total_incl_tax")):
        return False, sib_ref
    return True, sib_ref


def build_replica(
    db: Session,
    config_db: Session,
    venue_id: str,
    extraction: dict,
    *,
    lh=None,
    catalogue: list[dict] | None = None,
    units: list[dict] | None = None,
    suppliers: list[dict] | None = None,
    tax_rates: dict[int, float] | None = None,
    aliases_by_id: dict[str, list[str]] | None = None,
    item_matcher=None,
    unit_resolver_ask=None,
    own_invoice_id: str | None = None,
    received_feed: list | None = None,
    loaded_total: object = None,
    loaded_supplier_name: object = None,
) -> dict:
    """Extraction result → a complete ``received_invoice``-shaped document.

    The keyword overrides exist for tests and for dojo batch runs (fetch the
    venue's catalogue once, reuse across samples). Never raises — every
    failed stage degrades and appends to ``resolution_log``."""
    log: list[str] = []
    warnings: list[str] = []
    issues: list[dict] = []
    extraction = extraction if isinstance(extraction, dict) else {}

    def _issue(
        code: str,
        message: str,
        *,
        blocking: bool = True,
        line_id: str | None = None,
        data: dict | None = None,
    ) -> None:
        entry: dict = {
            "id": f"{code}:{line_id}" if line_id else code,
            "code": code,
            "blocking": blocking,
            "line_id": line_id,
            "message": message,
        }
        if data:
            entry["data"] = data
        issues.append(entry)

    # ---- Document-type flags (the engine's credit-note/statement gates) ----
    doc_type = str(extraction.get("document_type") or "").lower()
    if doc_type == "statement":
        msg = (
            "this document is a supplier STATEMENT, not an invoice — "
            "it should not be received"
        )
        warnings.append(msg)
        _issue("not_an_invoice", msg, data={"document_type": "statement"})
    elif doc_type == "other" and not extraction.get("lines"):
        msg = "this document is a letter/notice, not an invoice — nothing to receive"
        warnings.append(msg)
        _issue("not_an_invoice", msg, data={"document_type": "letter"})
    # A CREDIT NOTE is a real, receivable document — receiving it REVERSES
    # stock and cost. Recognised from any of three signals (see
    # _credit_signals) and negated here, so everything downstream — the
    # resolution, the working document, the receive request, the dojo's
    # replica-vs-Loaded compare — reads Loaded's own sign space for free.
    credit_signals = _credit_signals(extraction, loaded_total)
    is_credit_note = bool(credit_signals)
    if is_credit_note:
        extraction = _credit_normalise(extraction)
        msg = (
            "CREDIT NOTE — receiving this REVERSES stock and cost "
            "(quantities are negative)"
        )
        warnings.append(msg)
        # Non-blocking on purpose: a clean credit note is immediately receivable
        # (it reverses stock). It's surfaced as a header BANNER on the card, not
        # as an item in the review list, so there's no "notes" bucket.
        _issue(
            "credit_note",
            msg,
            blocking=False,
            data={"document_type": "credit_note", "signals": credit_signals},
        )

    if lh is None:
        from app.services.received_invoice import LoadedInvoiceClient

        lh = LoadedInvoiceClient(db, config_db, venue_id)

    # ---- Reference data (one bulk call each; variants embedded) ----
    if catalogue is None:
        try:
            from app.services.item_match import _fetch_raw_stock_items

            catalogue = _fetch_raw_stock_items(venue_id, db, config_db)
        except Exception as exc:  # noqa: BLE001
            log.append(f"catalogue unavailable: {exc}")
            catalogue = []
    if units is None:
        try:
            rows = lh.get("/1.0/stock/internal/units")
            units = rows if isinstance(rows, list) else []
        except Exception as exc:  # noqa: BLE001
            log.append(f"units unavailable: {exc}")
            units = []
    if suppliers is None:
        try:
            rows = lh.get("/1.0/stock/internal/suppliers")
            suppliers = rows if isinstance(rows, list) else []
        except Exception as exc:  # noqa: BLE001
            log.append(f"suppliers unavailable: {exc}")
            suppliers = []
    if tax_rates is None:
        tax_rates = sales_tax_rates(lh)
        if not tax_rates:
            log.append(
                "sales-tax table unavailable — falling back to copy-derived rate"
            )

    # ---- Supplier ----
    # Ordered by authority: the copy first, then whatever Loaded itself
    # records. Loaded's name earns its place because an invoice raised from a
    # purchase order carries the supplier a HUMAN chose at order time — the
    # one piece of supplier evidence on the page that isn't OCR.
    supplier_printed = extraction.get("supplier_name")
    hints = [supplier_printed, loaded_supplier_name]
    if aliases_by_id is None:
        aliases_by_id = {}
        # Loaded's per-supplier aliases are the account's own spellings of one
        # business — the authority on identity here. They used to be fetched
        # only for suppliers already matching the printed name by containment,
        # which needs the answer in order to ask the question: 'SERVICE FOODS
        # LTD' shares no containment with 'SERVICE FOODS AUCKLAND', so the
        # list holding 'SERVICE FOODS LTD' verbatim was never read and the
        # invoice resolved to nothing. Shared identity WORDS survive a
        # different tail, so they pick the candidates instead.
        for s in alias_candidates(hints, suppliers, limit=3):
            try:
                rows = lh.get(f"/1.0/stock/internal/suppliers/{s['id']}/aliases")
                aliases_by_id[str(s["id"])] = [
                    a.get("name") for a in rows if isinstance(a, dict) and a.get("name")
                ]
            except Exception:  # noqa: BLE001 — aliases are hints
                pass

    supplier, sup_by = resolve_supplier(hints, suppliers, aliases_by_id)
    if supplier is None:
        # Last deterministic hop: a spec's alias list is a global statement
        # that several names mean ONE business ('Ellesmere Butchery' is Tamar
        # Farming Company). If the printed name lands on a spec, every name
        # that spec knows becomes a hint. Tried only after direct evidence
        # fails, so a spec can never overrule the account's own records.
        try:
            from app.db.config_models import SupplierInvoiceSpec
            from app.services.invoice_extraction import MAIN_PROMPT_NAME

            spec, _how = match_spec(
                config_db.query(SupplierInvoiceSpec)
                .filter(SupplierInvoiceSpec.enabled.is_(True))
                .all(),
                hints,
                main_prompt_name=MAIN_PROMPT_NAME,
            )
            if spec is not None:
                supplier, _ = resolve_supplier(
                    [spec.name, *(spec.aliases or [])], suppliers, aliases_by_id
                )
                sup_by = "spec_alias" if supplier else None
        except Exception:  # noqa: BLE001 — hints only
            pass
    if supplier is None and supplier_printed:
        try:
            from app.services.item_match import suggest_supplier_match

            m = suggest_supplier_match(venue_id, str(supplier_printed), db, config_db)
            if m:
                supplier = next(
                    (s for s in suppliers if s.get("id") == m.get("supplier_id")),
                    {"id": m.get("supplier_id"), "name": m.get("supplier_name")},
                )
                sup_by = "llm"
        except Exception as exc:  # noqa: BLE001
            log.append(f"supplier LLM match failed: {exc}")
    supplier_id = supplier.get("id") if supplier else None
    log.append(
        f"supplier: '{supplier_printed}' → "
        + (f"{supplier.get('name')} ({sup_by})" if supplier else "UNRESOLVED")
    )
    if supplier_id is None:
        # The confidence rule: an invoice with no resolved Loaded supplier
        # cannot be received (do_receive hard-fails on it) — say so instead
        # of leaving a log line nobody reads.
        _issue(
            "supplier_unresolved",
            (
                f"no Loaded supplier matches '{supplier_printed}' — link or "
                "create the supplier before receiving"
                if supplier_printed
                else "the copy names no supplier — pick the supplier by hand"
            ),
            # The printed name is what a create would use, so it travels with
            # the blocker rather than being re-parsed out of the message.
            data={"supplier_name": supplier_printed} if supplier_printed else None,
        )

    # Norm's supplier-product catalogue key — global physical facts keyed by
    # (supplier spec, stock code). Best-effort: no spec or no config session
    # (unit tests inject reference data only) → the tier stays silent.
    catalog_key = None
    if config_db is not None:
        try:
            from app.services import supplier_catalog

            catalog_key = supplier_catalog.supplier_key_for(supplier_printed, config_db)
        except Exception as exc:  # noqa: BLE001 — the catalogue never blocks
            logger.info("supplier catalogue unavailable: %s", exc)

    # ---- Lines: deterministic pass ----
    idx = line_match.CatalogueIndex.build(catalogue or [])
    ext_lines = [
        dict(cl) for cl in extraction.get("lines") or [] if isinstance(cl, dict)
    ]
    resolved: list[dict] = []
    unresolved: list[tuple[int, dict]] = []
    for i, el in enumerate(ext_lines):
        item, by = idx.match_line(el.get("code"), el.get("description"), supplier_id)
        resolved.append({"item": item, "by": by})
        if item is None:
            unresolved.append((i, el))
        else:
            log.append(
                f"line {i + 1} '{el.get('description')}' → {item.get('name')} ({by})"
            )

    # ---- Lines: LLM fallback for the unresolved tail ----
    if unresolved:
        if item_matcher is None:
            from app.services.item_match import suggest_item_matches

            item_matcher = suggest_item_matches
        llm_lines = [
            {
                "id": f"rep-{i}",
                "description": str(el.get("description") or ""),
                "code": str(el.get("code") or ""),
                "brand": "",
                "unit": str(el.get("unit") or el.get("unit_of_measure") or ""),
            }
            for i, el in unresolved
        ]
        try:
            suggestions = item_matcher(
                venue_id,
                llm_lines,
                db,
                config_db,
                supplier_name=str(supplier_printed) if supplier_printed else None,
            )
        except Exception as exc:  # noqa: BLE001
            log.append(f"LLM item match failed: {exc}")
            suggestions = {}
        by_id = {i.get("id"): i for i in catalogue or []}
        for i, el in unresolved:
            s = suggestions.get(f"rep-{i}") or {}
            m = s.get("matched_item")
            if isinstance(m, dict) and m.get("id"):
                resolved[i] = {
                    "item": by_id.get(m["id"]) or m,
                    "by": "llm",
                }
                log.append(
                    f"line {i + 1} '{el.get('description')}' → {m.get('name')} (llm)"
                )
            else:
                resolved[i] = {
                    "item": None,
                    "by": None,
                    "suggested_name": s.get("suggested_name"),
                    "suggested_group_id": s.get("suggested_group_id"),
                }
                log.append(
                    f"line {i + 1} '{el.get('description')}' → NEW"
                    + (
                        f" (create '{s.get('suggested_name')}')"
                        if s.get("suggested_name")
                        else ""
                    )
                )

    # ---- Tax: prevailing + copy-derived fallbacks ----
    copy_rate = None
    sub = extraction.get("subtotal_ex_tax")
    tax = extraction.get("tax_amount")
    try:
        # != 0, not > 0: a credit note's subtotal is negative and its tax with
        # it, so the ratio is still the right positive rate.
        if sub and tax is not None and float(sub) != 0:
            copy_rate = round(float(tax) / float(sub), 4)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    matched_rates = []
    for r in resolved:
        item = r.get("item")
        if item and tax_rates:
            so = item.get("globalSalesTaxSortOrder")
            if so is not None and int(so) in tax_rates:
                matched_rates.append(tax_rates[int(so)])
    prevailing = (
        max(set(matched_rates), key=matched_rates.count) if matched_rates else copy_rate
    )

    # ---- Assemble lines ----
    out_lines: list[dict] = []
    unit_residual: list[int] = []  # line indexes for the batched unit resolver
    for i, el in enumerate(ext_lines):
        r = resolved[i]
        item = r.get("item")
        variant = (
            line_match.supplier_variant(item, supplier_id, el.get("code"))
            if item
            else None
        )
        unit_rec = None
        # Which tier supplied unit_rec ("variant" | "copy" | "catalogue" |
        # "printed") — the card finishes unit blockers with the unit in use,
        # and crediting every stand-in to Loaded misattributed the ones
        # Norm's catalogue answered (4366904, 19 Aug 2026).
        unit_source = None
        # The copy's confidently-read delivered unit that isn't in Loaded yet —
        # carried onto the replica line so the review layer can offer a
        # "create this unit" suggestion (never a bare OCR string).
        unit_create_name = None
        count_word_unit = False
        # The copy's derived unit is "confidently delivered" under the
        # engine's _delivered_unit rule: a multipack or a parseable size,
        # never a bare packaging word — and never when the extraction marked
        # the unit unreadable (the never-guess rule).
        derived = el.get("unit_of_measure")
        confident = (
            bool(derived)
            and not el.get("unit_unrecognisable")
            and not is_packaging_word(derived)
            and (is_multipack(derived) or parse_unit(derived) is not None)
        )
        if variant and variant.get("unitId"):
            unit_rec = next(
                (u for u in units if u.get("id") == variant.get("unitId")), None
            ) or {"id": variant.get("unitId"), "name": None, "ratio": None}
            unit_source = "variant"
            # The unit is whatever the INVOICE says. A variant default is
            # user-entered data in Loaded; the copy is the delivery, and
            # receiving it accurately is the job. A venue counting wine
            # bottles as 'Each' is the venue's business — not a reason to book
            # 'Each' against a copy that says 750 mL.
            #
            # This used to be gated on the two units not being
            # `units_equivalent`, plus a `copy_is_more_specific` exception
            # carved out for one Hancocks invoice. Everything outside that
            # exception's shape fell through to Loaded, so '750 mL' lost to
            # 'Each'. The gate is gone: `_resolve_unit_record` already matches
            # by name, by multipack components and by parsed magnitude, so a
            # copy saying '0.7 L' against a variant saying '700 mL' still
            # lands on the same record and nothing moves.
            #
            # `confident` is the whole guard now, which is why a bare
            # packaging word had to be excluded from it above: 'PACK' parses
            # as a count of 1, so without that it would read as trustworthy
            # and override a real variant unit (Trents 5973784).
            if confident:
                copy_rec = _resolve_unit_record(derived, units)
                if copy_rec and copy_rec.get("id") != unit_rec.get("id"):
                    log.append(
                        f"line {i + 1} unit: variant default "
                        f"'{unit_rec.get('name')}' → '{copy_rec.get('name')}' "
                        "(per the copy)"
                    )
                    unit_rec = copy_rec
                    unit_source = "copy"
                elif copy_rec is None:
                    log.append(
                        f"line {i + 1} unit: copy says '{derived}' but the venue "
                        f"has no such unit — kept variant default "
                        f"'{unit_rec.get('name')}' (unit would need creating)"
                    )
                    # Marker (not an issue): the review layer turns this into a
                    # `create_unit` SUGGESTION the user can accept, rather than a
                    # non-blocking note. Only set for a unit WE read off the copy
                    # — never a bare unlinked string Loaded's OCR left behind.
                    unit_create_name = str(derived)
        if unit_rec is None and derived and not is_packaging_word(derived):
            # The page's own derived unit — the page outranks everything
            # below for the document in hand.
            unit_rec = _resolve_unit_record(derived, units)
            if unit_rec is not None:
                unit_source = "copy"
        if unit_rec is None and catalog_key:
            # Norm's supplier-product catalogue: what this (supplier, code)
            # physically IS, learned from sizes printed on OTHER invoices
            # (provenance printed-or-better; venue practice never answers).
            try:
                from app.services import supplier_catalog

                answer = supplier_catalog.catalog_unit_for_line(
                    config_db, catalog_key, el.get("code")
                )
            except Exception:  # noqa: BLE001 — the catalogue never blocks
                answer = None
            if answer:
                unit_rec = _resolve_unit_record(answer["unit_name"], units)
                if unit_rec is not None:
                    unit_source = "catalogue"
                    log.append(
                        f"line {i + 1} unit: Norm catalogue says "
                        f"'{answer['unit_name']}' ({answer['provenance']}) → "
                        f"'{unit_rec.get('name')}'"
                    )
                elif unit_create_name is None:
                    # The catalogue knows the pack but the venue has no such
                    # unit — the existing create-unit suggestion path.
                    unit_create_name = str(answer["unit_name"])
                    log.append(
                        f"line {i + 1} unit: Norm catalogue says "
                        f"'{answer['unit_name']}' ({answer['provenance']}) but "
                        "the venue has no such unit (unit would need creating)"
                    )
        if unit_rec is None:
            # Last: the printed unit column — refusing bare packaging words
            # ('PACK', 'CARTON'): they say how goods were bundled, not what
            # ONE delivered item is, so such a line raises unit_missing and
            # parks instead of silently linking a meaningless unit.
            printed = el.get("unit")
            if printed and not is_packaging_word(printed):
                unit_rec = _resolve_unit_record(printed, units)
                if unit_rec is not None:
                    unit_source = "printed"
                if unit_rec is not None and not derived:
                    # Resolved ONLY by a bare count word ('EA' → Each): that
                    # is how the line is CHARGED, not the pack size (the
                    # user's Trents observation, 18 Aug 2026). Honest but
                    # vague — mark it so the batched resolver can offer the
                    # real pack as an upgrade suggestion.
                    p = parse_unit(printed)
                    count_word_unit = bool(
                        p
                        and p[0] == "count"
                        and p[1] == 1
                        and not any(ch.isdigit() for ch in str(printed))
                    )
        rate = None
        if item and tax_rates and item.get("globalSalesTaxSortOrder") is not None:
            rate = tax_rates.get(int(item["globalSalesTaxSortOrder"]))
        if rate is None:
            rate = prevailing
        qty = el.get("quantity")
        cost = el.get("unit_price_ex_tax")
        total = el.get("line_total_ex_tax")
        if el.get("unit_unrecognisable"):
            # The engine's unit_confirm rule: an unreadable copy unit always
            # needs a human eye, even when a variant supplies the unit.
            msg = (
                f"line {i + 1} '{el.get('description')}': unit can't be read "
                "from the copy"
            )
            warnings.append(msg)
            _issue(
                "unit_unconfirmed",
                msg,
                line_id=f"rep-{i}",
                # The stand-in's source and identity: the card finishes this
                # sentence with the unit in use, and the attribution only
                # holds while the WORKING line shows THIS unit — a different
                # working unit is Loaded's own.
                data=(
                    {"unit_chosen_by": unit_source, "unit_id": unit_rec.get("id")}
                    if unit_rec is not None and unit_source
                    else None
                ),
            )
        if qty is not None and cost is not None and total is not None:
            try:
                if abs(float(qty) * float(cost) - float(total)) > 0.011:
                    log.append(
                        f"line {i + 1}: quantity × price ≠ line total as printed "
                        f"({qty} × {cost} vs {total}) — copy numbers not "
                        "self-consistent"
                    )
            except (TypeError, ValueError):
                pass
        if total is None and qty is not None and cost is not None:
            total = round(float(qty) * float(cost), 4)
        out_lines.append(
            {
                "id": f"rep-{i}",
                "code": el.get("code"),
                "description": el.get("description"),
                "brand": None,
                "unit": (unit_rec or {}).get("name")
                or el.get("unit_of_measure")
                or el.get("unit"),
                "linked_unit_id": (unit_rec or {}).get("id"),
                "original_unit_id": (unit_rec or {}).get("id"),
                "unit_ratio": (unit_rec or {}).get("ratio"),
                "quantity_ordered": None,
                "quantity_received": qty,
                "unit_cost": cost,
                "total_cost": total,
                "tax_amount": (
                    round(float(total) * rate, 4)
                    if total is not None and rate is not None
                    else None
                ),
                "sale_tax_rate": rate,
                "linked_item_id": item.get("id") if item else None,
                "item_name": item.get("name") if item else None,
                "linked_brand_id": (variant or {}).get("brandId")
                or (item or {}).get("defaultBrandId"),
                "item_type": "Default",
                "matched_by": r.get("by"),
                "unit_unrecognisable": el.get("unit_unrecognisable"),
                "unit_source": unit_source,
                "suggested_name": r.get("suggested_name"),
                "suggested_group_id": r.get("suggested_group_id"),
                # The copy's delivered unit that isn't in Loaded — drives a
                # `create_unit` suggestion in review; None on normal lines.
                "unit_create_name": unit_create_name,
            }
        )
        if item is None:
            _issue(
                "item_unmatched",
                f"line {i + 1} '{el.get('description')}': no Loaded stock item "
                "matches — link one or create it before receiving",
                line_id=f"rep-{i}",
                data={
                    k: v
                    for k, v in (
                        ("suggested_name", r.get("suggested_name")),
                        ("suggested_group_id", r.get("suggested_group_id")),
                    )
                    if v
                },
            )
        if (unit_rec or {}).get("id") is None:
            # The confidence rule the user named: the copy's unit can't be
            # resolved to a venue unit record AND no supplier variant supplies
            # one — we cannot be confident in this line. When the copy DID
            # print a confidently-readable unit (it just doesn't exist in
            # Loaded), carry its name so the editor can offer to create it —
            # a replica-sourced name, never Loaded's leftover string.
            _issue(
                "unit_missing",
                f"line {i + 1} '{el.get('description')}': no unit could be "
                "read from the copy",
                line_id=f"rep-{i}",
                data={"unit_name": str(derived)} if confident and derived else None,
            )
            # Candidates for the batched unit resolver — sizeless lines where
            # NOTHING (variant, page, catalogue) says the size. Unreadable
            # units qualify too: never-guess binds the EXTRACTOR (nothing may
            # be invented as a READING of the page), not the resolver, whose
            # answer is a human-gated suggestion. Excluding them parked
            # 'HIGHLAND PARK 15 YEAR OLD GIFT BOX (1X7' at "set one before
            # receiving" with the resolver never consulted (4366904,
            # 19 Aug 2026).
            if unit_create_name is None:
                unit_residual.append(i)
        elif count_word_unit:
            # Resolved only by a bare charge word (EA → Each): honest but
            # vague — no blocker, yet the size is still unknown. The resolver
            # may offer the real pack as an UPGRADE suggestion (interactive
            # Accept; autopilot keeps the count unit — no gate applies here).
            # An unreadable-unit line rides too: its unit_unconfirmed blocker
            # stands regardless, and the resolver's offer turns that Accept
            # from a shrug into an informed choice.
            unit_residual.append(i)

    # ---- The residual unit resolver: one batched LLM call per invoice ----
    # Sibling lines are the evidence (three Malfy lines printing 700ML at the
    # same price answer the sizeless fourth), so the whole invoice rides as
    # context. Output is metadata + suggestions, never a silent link: the
    # unit_missing blocker above stands until a person accepts (or autopilot's
    # receive_without_unit gate applies a high-confidence pick).
    if unit_residual and (unit_resolver_ask is not None or db is not None):
        from app.services import unit_resolver as _ur

        evidence_by_line: dict[str, str] = {}
        category_by_line: dict[str, str] = {}
        unit_names = {u.get("id"): u.get("name") for u in units or []}
        for j in unit_residual:
            item_j = resolved[j].get("item")
            if item_j:
                stocked = sorted(
                    {
                        str(unit_names.get(v.get("unitId")))
                        for v in item_j.get("suppliers") or []
                        if v.get("unitId") and unit_names.get(v.get("unitId"))
                    }
                )
                if stocked:
                    evidence_by_line[f"rep-{j}"] = (
                        f"the venue currently stocks the matched item "
                        f"'{item_j.get('name')}' at {', '.join(stocked)} — "
                        "evidence, not authority (venue setups contain "
                        "mistakes)"
                    )
            if config_db is not None:
                try:
                    from app.services import supplier_catalog

                    if catalog_key:
                        row = supplier_catalog.lookup(
                            config_db, catalog_key, ext_lines[j].get("code")
                        )
                        if row is not None and row.category != "unknown":
                            category_by_line[f"rep-{j}"] = row.category
                    # Cross-supplier catalogue evidence: the same product
                    # under another supplier's code often already has an
                    # answered size (Bidfood's 'SYRUP BUTTERSCOTCH SHOTT' at
                    # Litre informs a sizeless Trents SHOTT line).
                    related = supplier_catalog.related_evidence(
                        config_db,
                        ext_lines[j].get("description"),
                        exclude=(catalog_key or "", str(ext_lines[j].get("code"))),
                    )
                    if related:
                        prior = evidence_by_line.get(f"rep-{j}")
                        evidence_by_line[f"rep-{j}"] = "; ".join(
                            ([prior] if prior else []) + related
                        )
                except Exception:  # noqa: BLE001
                    pass
        try:
            resolved_units = _ur.resolve_units(
                [
                    {
                        "id": f"rep-{k}",
                        "code": xl.get("code"),
                        "description": xl.get("description"),
                        "quantity": xl.get("quantity"),
                        "unit": xl.get("unit"),
                        "unit_of_measure": xl.get("unit_of_measure"),
                        "unit_price": xl.get("unit_price_ex_tax"),
                    }
                    for k, xl in enumerate(ext_lines)
                ],
                [f"rep-{j}" for j in unit_residual],
                units or [],
                supplier_name=supplier_printed,
                evidence_by_line=evidence_by_line,
                category_by_line=category_by_line,
                ask_llm=unit_resolver_ask,
                db=db,
            )
        except Exception as exc:  # noqa: BLE001 — degrade to unit_missing
            logger.info("unit resolver failed: %s", exc)
            resolved_units = {}
        for j in unit_residual:
            r_j = resolved_units.get(f"rep-{j}")
            if not r_j:
                continue
            unit_j = r_j.get("unit")
            if unit_j is None and not r_j.get("create_name"):
                continue
            # The resolver confirming the count-word resolution (Each IS the
            # delivered unit — a crate charge, say) is agreement, not news.
            if unit_j and unit_j.get("id") == out_lines[j].get("linked_unit_id"):
                continue
            out_lines[j]["unit_resolved"] = {
                "unit_id": (unit_j or {}).get("id"),
                "unit_name": (unit_j or {}).get("name"),
                "unit_ratio": (unit_j or {}).get("ratio"),
                "create_name": r_j.get("create_name"),
                "confidence": r_j.get("confidence"),
                "why": r_j.get("why"),
            }
            # A decisive "the right unit doesn't exist here yet" feeds the
            # existing create-unit suggestion path (human-accepted; autopilot
            # gates it behind auto_create_units).
            if (
                unit_j is None
                and r_j.get("create_name")
                and r_j.get("confidence") == "high"
                and not out_lines[j].get("unit_create_name")
            ):
                out_lines[j]["unit_create_name"] = r_j["create_name"]
            log.append(
                f"line {j + 1} unit: nothing says the size — resolver offers "
                f"'{(unit_j or {}).get('name') or r_j.get('create_name')}' "
                f"({r_j.get('confidence')}: {r_j.get('why')})"
            )

    # ---- Totals reconciliation (the engine's `totals` gate, copy-side) ----
    # Loaded's own entry validation absorbs up to 10c of rounding on
    # line_sum + tax − discount vs total; past that band the copy's printed
    # numbers must agree with themselves (±2c per the totals doctrine).
    def _f(v: object) -> float | None:
        try:
            return float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    if is_credit_note:
        # A lump-sum value credit (a price adjustment: no quantity, just a
        # value) cannot be received as-is — receive recomputes every line
        # total as qty × cost, which silently zeroes it. Loaded needs a
        # quantity, so this one has to be resolved by a human.
        for ln in out_lines:
            if not _f(ln.get("quantity_received")) and _f(ln.get("total_cost")):
                _issue(
                    "credit_zero_quantity",
                    f"'{ln.get('description') or ln.get('code') or '?'}' credits a "
                    "value with no quantity — receiving computes quantity × cost, "
                    "so this line would come through as zero. Give it a quantity "
                    "(or handle this credit in Loaded).",
                    line_id=ln.get("id"),
                )

    if out_lines:
        line_sum = sum(t for t in (_f(ln.get("total_cost")) for ln in out_lines) if t)
        sub_f = _f(extraction.get("subtotal_ex_tax"))
        tax_f = _f(extraction.get("tax_amount"))
        total_f = _f(extraction.get("total_incl_tax"))
        disc_f = _f(extraction.get("discount_amount")) or 0.0
        rounding_ok = (
            tax_f is not None
            and total_f is not None
            and abs(line_sum + tax_f - disc_f - total_f) <= 0.10
        )
        if not rounding_ok:
            problems = []
            if sub_f is not None and abs(line_sum - sub_f) > 0.02:
                problems.append(
                    f"lines add to {line_sum:.2f} but the subtotal reads {sub_f:.2f}"
                )
            if (
                sub_f is not None
                and tax_f is not None
                and total_f is not None
                and abs(sub_f + tax_f - disc_f - total_f) > 0.02
            ):
                problems.append(
                    f"subtotal {sub_f:.2f} + tax {tax_f:.2f}"
                    + (f" − discount {disc_f:.2f}" if disc_f else "")
                    + f" ≠ total {total_f:.2f}"
                )
            if problems:
                msg = (
                    "the copy's totals don't reconcile: "
                    + "; ".join(problems)
                    + " — check the extraction before trusting these numbers"
                )
                warnings.append(msg)
                _issue("totals_inconsistent", msg)

    # ---- PO ----
    # customer_… is the extraction contract; the bare name is a legacy field
    # retired from the schema 17 Aug 2026, read here only so extractions
    # cached before the retirement (DocumentExtraction fingerprint cache,
    # stored dojo runs) keep resolving.
    po_number = extraction.get("customer_purchase_order_number") or extraction.get(
        "purchase_order_number"
    )
    linked_po_id = None
    if po_number and is_credit_note:
        # Never resolve a credit note's PO reference. It names the ORIGINAL
        # order, which Loaded (1:1) has already linked to the original
        # invoice — resolving it would classify this as a split order and
        # stamp bogus cross-references onto both that PO and that invoice at
        # receive. The printed reference is kept for display only.
        log.append(
            f"credit note: purchase order {po_number} dropped — the reference "
            "belongs to the invoice being credited, not to this document "
            "(Loaded stores none either)"
        )
        po_number = None
    elif po_number:
        try:
            from app.services.received_invoice import resolve_po_id

            r = resolve_po_id(lh, po_number, supplier_id)
            if r:
                other = r.get("linked_invoice_id")
                order_no = r.get("order_number") or po_number
                if other and other != own_invoice_id:
                    # The split-order validator: the PO is claimed by a
                    # SIBLING invoice (Loaded is 1:1). Classify doubled-up
                    # (sibling already carries the same lines and total —
                    # the reference is bogus) vs genuine split delivery
                    # (reference kept, never linked). Any doubt → split.
                    doubled, sib_ref = _sibling_doubled_up(
                        lh, other, out_lines, extraction
                    )
                    if doubled:
                        msg = (
                            f"order {order_no}: already fully invoiced by "
                            f"{sib_ref} (same lines and total) — doubled-up "
                            "invoice, reference removed"
                        )
                        log.append(msg)
                        _issue(
                            "po_doubled_up",
                            msg,
                            data={
                                "po_id": r.get("id"),
                                "sibling_invoice_id": other,
                                "sibling_reference": sib_ref,
                                "order_number": str(order_no),
                            },
                        )
                        po_number = None
                    else:
                        # Written as the REASON auto-receive stopped, not the
                        # mechanics: the card shows this under "Blocked from
                        # auto receive", and "what do I do" must be in the
                        # sentence.
                        msg = (
                            "this invoice isn't linked to a purchase order — "
                            f"order {order_no} was split across deliveries and "
                            f"{sib_ref} carries the link. Accept the suggestion "
                            "to keep the order reference, then receive."
                        )
                        log.append(msg)
                        _issue(
                            "po_split_order",
                            msg,
                            blocking=True,
                            data={
                                "po_id": r.get("id"),
                                "sibling_invoice_id": other,
                                "sibling_reference": sib_ref,
                                "order_number": str(order_no),
                            },
                        )
                        po_number = order_no
                else:
                    # Unlinked, or claimed by the very invoice we're
                    # replicating — the link is ours.
                    linked_po_id = r.get("id")
                    po_number = order_no
                    log.append(
                        f"order {order_no} → linked"
                        + (
                            " (already claimed by this invoice)"
                            if other
                            else " (open order)"
                        )
                    )
                # The order belonging to another supplier is worth SAYING and
                # not worth stopping for. Soho is supplied by Procure:
                # ordering from Soho in Loaded while the invoice arrives from
                # Procure is the arrangement, not a mistake. The comparison is
                # also against the supplier the replica is PROPOSING, so it
                # fires for a state that does not exist yet — and receiving
                # never touches the order's ownership (do_receive has no PO
                # guard at all), so blocking bought nothing and cost every
                # through-supplier invoice a manual override.
                po_sup = r.get("supplier_id")
                if po_sup and supplier_id and po_sup != supplier_id:
                    po_sup_name = next(
                        (
                            s.get("name")
                            for s in suppliers
                            if isinstance(s, dict) and s.get("id") == po_sup
                        ),
                        None,
                    )
                    # Loaded's OWN alias lists are already in hand (fetched
                    # for supplier resolution above) and were not consulted
                    # here, so a venue that had recorded the relationship still
                    # got flagged. Ask the identity module — the one place that
                    # knows what counts as the same business.
                    same_business = bool(
                        po_sup_name
                        and resolve_supplier(
                            [
                                n
                                for n in (
                                    (supplier or {}).get("name"),
                                    supplier_printed,
                                )
                                if n
                            ],
                            [{"id": po_sup, "name": po_sup_name}],
                            aliases_by_id,
                        )[0]
                    )
                    if not same_business:
                        msg = (
                            f"the copy names "
                            f"{(supplier or {}).get('name') or supplier_printed} "
                            f"and order {order_no} belongs to "
                            f"{po_sup_name or 'a different supplier'} in Loaded "
                            "— normal if one supplies through the other. "
                            "Receiving won't change the order."
                        )
                        warnings.append(msg)
                        _issue(
                            "po_supplier_mismatch",
                            msg,
                            blocking=False,
                            data={
                                "po_supplier_id": po_sup,
                                "po_supplier_name": po_sup_name,
                            },
                        )
            else:
                log.append(f"PO '{po_number}': no unambiguous Loaded match")
                _issue(
                    "po_unresolved",
                    f"the copy references order '{po_number}' but no Loaded "
                    "purchase order matches it unambiguously — receive "
                    "without linking an order",
                    data={"po_number": str(po_number)},
                )
        except Exception as exc:  # noqa: BLE001
            log.append(f"PO resolution failed: {exc}")
            _issue(
                "po_unresolved",
                f"the order reference '{po_number}' could not be checked "
                f"against Loaded ({exc}) — receive without linking an order",
                data={"po_number": str(po_number)},
            )

    # ---- Duplicate check (the engine's Layer 0) ----
    # Same normalized invoice number + same supplier already in the received
    # feed → this document was received before. Two feed record kinds: an
    # "Invoice" row is a real invoice entity; a "PurchaseOrder" row means the
    # goods were receipted straight against the ORDER (no invoice document
    # exists in Loaded — the row id is the PO's id).
    dup_invoice_id = dup_file_id = dup_po_id = None
    inv_no = extraction.get("invoice_number")
    if inv_no:
        try:
            rows = received_feed if received_feed is not None else _received_feed(lh)
            sup_key = _norm((supplier or {}).get("name") or supplier_printed)
            for row in rows or []:
                if not isinstance(row, dict) or row.get("id") == own_invoice_id:
                    continue
                if _norm(row.get("invoiceNumber")) != _norm(inv_no):
                    continue
                if _norm(row.get("supplierName")) != sup_key:
                    continue
                # A credit note usually reprints the number of the invoice it
                # credits. Opposite signs mean this IS that credit, not a
                # duplicate of it — matching signs (credit vs credit) still
                # count, because receiving a credit twice double-reverses
                # stock with nothing else to catch it.
                row_total = row.get("total")
                if isinstance(row_total, (int, float)) and is_credit_note != (
                    row_total < 0
                ):
                    continue
                dup_date = str(row.get("receivedAt") or "")[:10]
                if str(row.get("type") or "") == "PurchaseOrder":
                    dup_po_id = row.get("id")
                    dup_msg = (
                        f"invoice {inv_no} was already receipted on {dup_date} "
                        f"against order {row.get('purchaseOrderNumber') or '?'} — "
                        "the goods came in on the order (no invoice document "
                        "exists in Loaded); this is a duplicate"
                    )
                else:
                    dup_invoice_id = row.get("id")
                    dup_file_id = row.get("fileId")
                    dup_msg = (
                        f"invoice {inv_no} was already received on {dup_date} — "
                        "this is a duplicate"
                    )
                warnings.append(dup_msg)
                log.append(dup_msg)
                _issue(
                    "duplicate_invoice",
                    dup_msg,
                    data={
                        k: v
                        for k, v in (
                            ("duplicate_of_invoice_id", dup_invoice_id),
                            ("duplicate_of_file_id", dup_file_id),
                            ("duplicate_of_purchase_order_id", dup_po_id),
                        )
                        if v
                    },
                )
                break
        except Exception as exc:  # noqa: BLE001
            log.append(f"duplicate check failed: {exc}")

    return {
        "replica": True,
        "invoice_id": None,
        "reference_number": extraction.get("invoice_number"),
        "supplier_name": (supplier or {}).get("name") or supplier_printed,
        "linked_supplier_id": supplier_id,
        "purchase_order_number": str(po_number) if po_number else None,
        "linked_purchase_order_id": linked_po_id,
        "issued_at": _iso_date(extraction.get("invoice_date")),
        "due_at": None,
        "received_at": None,
        # The engine's duplicate-gate markers (same names as the card
        # registry); warnings mirror the blocking flags for the dojo UI.
        "duplicate_of_invoice_id": dup_invoice_id,
        "duplicate_of_file_id": dup_file_id,
        "duplicate_of_purchase_order_id": dup_po_id,
        # A credit note reverses stock and cost — the flag rides so the doc,
        # the editor and the receive path all know without re-deriving it.
        "is_credit_note": is_credit_note,
        "document_type": "credit_note" if is_credit_note else doc_type or "invoice",
        "warnings": warnings,
        "issues": issues,
        "subtotal": extraction.get("subtotal_ex_tax"),
        "tax_amount": extraction.get("tax_amount"),
        "discount_amount": extraction.get("discount_amount"),
        "total": extraction.get("total_incl_tax"),
        "unit_cost_includes_tax": False,
        "file_id": None,
        "is_received": False,
        "status": "draft",
        "notes": "",
        "lines": out_lines,
        "resolution_log": log,
    }
