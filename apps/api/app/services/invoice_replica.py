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
from app.services.invoice_units import (
    _unit_norm,
    is_multipack,
    multipack_equal,
    parse_unit,
    units_equivalent,
)

logger = logging.getLogger(__name__)

_APP_HOST = "https://loadedhub.com"


def _norm(text: object) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


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
            pu = parse_unit(u.get("name"))
            if pu and pu[0] == parsed[0] and abs(pu[1] - parsed[1]) < 0.001:
                return u
    return None


def _resolve_supplier(
    supplier_name: object,
    suppliers: list[dict],
    aliases_by_id: dict[str, list[str]],
    spec_aliases: dict[str, str],
) -> tuple[dict | None, str | None]:
    """Printed supplier name → the venue's supplier record.

    Deterministic tiers (the supplier-matching convention: normalized
    containment, both sides ≥3 chars): exact name/alias equality first, then
    unique containment. ``spec_aliases`` maps a SupplierInvoiceSpec alias →
    canonical spec name for an extra equality hop. Ambiguity → None (the
    caller may fall back to the LLM matcher)."""
    target = _norm(supplier_name)
    if len(target) < 3:
        return None, None
    live = [
        s
        for s in suppliers
        if isinstance(s, dict)
        and s.get("id")
        and not (s.get("removedAt") or s.get("datestampDeleted"))
    ]

    def names_for(s: dict) -> list[str]:
        return [_norm(s.get("name"))] + [
            _norm(a) for a in aliases_by_id.get(str(s.get("id")), [])
        ]

    for s in live:
        if target in [n for n in names_for(s) if n]:
            return s, "exact"
    # Spec-alias hop: the printed name is a known alias of a spec whose
    # canonical name matches a supplier exactly.
    canon = spec_aliases.get(target)
    if canon:
        for s in live:
            if canon in [n for n in names_for(s) if n]:
                return s, "spec_alias"
    hits = []
    for s in live:
        for n in names_for(s):
            if len(n) >= 3 and (n in target or target in n):
                hits.append(s)
                break
    ids = {s.get("id") for s in hits}
    if len(ids) == 1:
        return hits[0], "containment"
    return None, None


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
        if not _close(sl.get("unitCost"), ln.get("unit_cost")):
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
    own_invoice_id: str | None = None,
    received_feed: list | None = None,
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
    total_incl = extraction.get("total_incl_tax")
    if isinstance(total_incl, (int, float)) and total_incl < 0:
        msg = "credit note (negative total) — out of scope for receiving"
        warnings.append(msg)
        _issue("not_an_invoice", msg, data={"document_type": "credit_note"})

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
    supplier_printed = extraction.get("supplier_name")
    if aliases_by_id is None:
        aliases_by_id = {}
        # Aliases are per-supplier; fetch only for containment candidates to
        # keep the call count flat (usually 0-1 fetches). The ≥3 guard
        # matches _resolve_supplier's own floor — an empty/short printed name
        # is `in` every name and would fire pointless fetches.
        printed_key = _norm(supplier_printed)
        cands = (
            [
                s
                for s in suppliers
                if isinstance(s, dict)
                and s.get("id")
                and _norm(s.get("name"))
                and (
                    _norm(s.get("name")) in printed_key
                    or printed_key in _norm(s.get("name"))
                )
            ]
            if len(printed_key) >= 3
            else []
        )
        for s in cands[:3]:
            try:
                rows = lh.get(f"/1.0/stock/internal/suppliers/{s['id']}/aliases")
                aliases_by_id[str(s["id"])] = [
                    a.get("name") for a in rows if isinstance(a, dict) and a.get("name")
                ]
            except Exception:  # noqa: BLE001 — aliases are hints
                pass

    spec_aliases: dict[str, str] = {}
    try:
        from app.db.config_models import SupplierInvoiceSpec

        for sp in (
            config_db.query(SupplierInvoiceSpec)
            .filter(SupplierInvoiceSpec.enabled.is_(True))
            .all()
        ):
            for a in sp.aliases or []:
                if len(_norm(a)) >= 3:
                    spec_aliases[_norm(a)] = _norm(sp.name)
    except Exception:  # noqa: BLE001 — hints only
        pass

    supplier, sup_by = _resolve_supplier(
        supplier_printed, suppliers, aliases_by_id, spec_aliases
    )
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
        )

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
        if sub and tax is not None and float(sub) > 0:
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
    for i, el in enumerate(ext_lines):
        r = resolved[i]
        item = r.get("item")
        variant = (
            line_match.supplier_variant(item, supplier_id, el.get("code"))
            if item
            else None
        )
        unit_rec = None
        # The copy's derived unit is "confidently delivered" under the
        # engine's _delivered_unit rule: a multipack or a parseable size,
        # never a bare packaging word — and never when the extraction marked
        # the unit unreadable (the never-guess rule).
        derived = el.get("unit_of_measure")
        confident = (
            bool(derived)
            and not el.get("unit_unrecognisable")
            and (is_multipack(derived) or parse_unit(derived) is not None)
        )
        if variant and variant.get("unitId"):
            unit_rec = next(
                (u for u in units if u.get("id") == variant.get("unitId")), None
            ) or {"id": variant.get("unitId"), "name": None, "ratio": None}
            # The unit-fix doctrine: the copy's confidently-delivered unit
            # overrides a variant default that names a DIFFERENT pack —
            # equivalent names keep the variant's unit (id-stable vs Loaded).
            if confident and not units_equivalent(unit_rec.get("name"), derived):
                copy_rec = _resolve_unit_record(derived, units)
                if copy_rec and copy_rec.get("id") != unit_rec.get("id"):
                    log.append(
                        f"line {i + 1} unit: variant default "
                        f"'{unit_rec.get('name')}' → '{copy_rec.get('name')}' "
                        "(per the copy)"
                    )
                    unit_rec = copy_rec
                elif copy_rec is None:
                    log.append(
                        f"line {i + 1} unit: copy says '{derived}' but the venue "
                        f"has no such unit — kept variant default "
                        f"'{unit_rec.get('name')}' (unit would need creating)"
                    )
        if unit_rec is None:
            unit_rec = _resolve_unit_record(derived or el.get("unit"), units)
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
                "from the copy — confirm the unit before receiving"
            )
            warnings.append(msg)
            _issue("unit_unconfirmed", msg, line_id=f"rep-{i}")
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
                "suggested_name": r.get("suggested_name"),
                "suggested_group_id": r.get("suggested_group_id"),
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
            # one — we cannot be confident in this line.
            _issue(
                "unit_missing",
                f"line {i + 1} '{el.get('description')}': no unit could be "
                "determined (nothing recognisable on the copy and no unit on "
                "the Loaded variant) — set the unit before receiving",
                line_id=f"rep-{i}",
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
    po_number = extraction.get("customer_purchase_order_number") or extraction.get(
        "purchase_order_number"
    )
    linked_po_id = None
    if po_number:
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
                        msg = (
                            f"order {order_no}: split across deliveries — "
                            f"{sib_ref} carries the order link; reference "
                            "kept without linking"
                        )
                        log.append(msg)
                        _issue(
                            "po_split_order",
                            msg,
                            blocking=False,
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
                # Gate L4's rule: the order must belong to this supplier.
                # Alias-aware — duplicate supplier records with matching
                # names (Ellesmere vs Tamar style) don't false-flag.
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
                    ours = _norm((supplier or {}).get("name") or supplier_printed)
                    theirs = _norm(po_sup_name)
                    same_name = (
                        len(ours) >= 3
                        and len(theirs) >= 3
                        and (ours in theirs or theirs in ours)
                    )
                    if not same_name:
                        msg = (
                            f"order {order_no} belongs to "
                            f"{po_sup_name or 'a different supplier'} in Loaded, "
                            f"not {(supplier or {}).get('name') or supplier_printed}"
                            " — check the order reference"
                        )
                        warnings.append(msg)
                        _issue(
                            "po_supplier_mismatch",
                            msg,
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
                    "purchase order matches it unambiguously — link the order "
                    "by hand or confirm there isn't one",
                    data={"po_number": str(po_number)},
                )
        except Exception as exc:  # noqa: BLE001
            log.append(f"PO resolution failed: {exc}")
            _issue(
                "po_unresolved",
                f"the order reference '{po_number}' could not be checked "
                f"against Loaded ({exc}) — confirm the order link by hand",
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
