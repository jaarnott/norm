"""Catalogue hygiene — where a venue's Loaded setup disagrees with truth.

The report the provenance hierarchy exists to power: venue practice is never
truth, so when Norm's supplier-product catalogue (printed/human/enriched
tiers) or the category rules say a stocking unit is wrong, that is a finding
— per venue, per item, with the evidence named. The canonical case: a
beverage syrup stocked as 'Each' at every venue — an error, not a
preference.

Findings are read-only here. Fixing a live item's unit converts recipes and
stock history inside Loaded, so repairs stay a deliberate human act (or a
future gated suggestion), never an auto-repair.

Endpoints are plain ``def`` — they do sync Loaded HTTP; ``async def`` would
park the whole event loop (the 16 Aug 2026 freeze).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permission
from app.db.config_models import SupplierProduct
from app.db.engine import get_config_db_rw, get_db
from app.db.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/supplier-catalog", tags=["supplier-catalog"])


def _is_countish(name: object) -> bool:
    """A unit that counts rather than measures ('Each', 'PACK', bare 'Unit')."""
    from app.services.invoice_units import is_packaging_word, parse_unit

    if not name:
        return False
    if is_packaging_word(name):
        return True
    p = parse_unit(name)
    return bool(p and p[0] == "count" and p[1] == 1)


def _same_pack_strict(a: object, b: object) -> bool:
    """Pack identity for HYGIENE — stricter than units_equivalent.

    The receiving doctrine deliberately calls 'Each' ≡ '1L bottle' the same
    delivered pack (a copy printing EA must not fight a sized variant). For
    hygiene that forgiveness hides the exact finding this report exists for:
    a syrup STOCKED as Each cannot cost recipes measured in millilitres.
    Here only name identity, multipack equality, or same-type-same-magnitude
    count as the same pack.
    """
    from app.services.invoice_units import _unit_norm, multipack_equal, parse_unit

    if _unit_norm(a) == _unit_norm(b):
        return True
    if multipack_equal(a, b):
        return True
    pa, pb = parse_unit(a), parse_unit(b)
    return bool(pa and pb and pa[0] == pb[0] and abs(pa[1] - pb[1]) < 0.001)


@router.get("/summary")
def catalog_summary(
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """The catalogue at a glance: size, coverage, open questions."""
    rows = config_db.query(SupplierProduct).all()
    by_prov: dict[str, int] = {}
    by_supplier: dict[str, int] = {}
    conflicts = []
    for r in rows:
        by_prov[r.provenance] = by_prov.get(r.provenance, 0) + 1
        by_supplier[r.supplier_key] = by_supplier.get(r.supplier_key, 0) + 1
        if r.unit_name is None and any(
            (r.evidence or {}).get(t) for t in ("human", "printed")
        ):
            conflicts.append(
                {
                    "supplier": r.supplier_key,
                    "code": r.code,
                    "description": r.description,
                }
            )
    answered = sum(1 for r in rows if r.unit_name or r.pack_type == "random_weight")
    return {
        "products": len(rows),
        "answered": answered,
        "by_provenance": by_prov,
        "by_supplier": by_supplier,
        "open_questions": conflicts[:50],
    }


@router.get("/hygiene/{venue_id}")
def venue_hygiene(
    venue_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    """Scan one venue's live Loaded catalogue against Norm truth + rules.

    Two finding kinds:
    - ``catalogue_disagrees``: the variant's stocking unit differs from what
      the catalogue KNOWS the product physically is (provenance ≥ enriched).
    - ``category_rule``: a beverage-category item stocked in a bare count
      unit — beverages are always volumes, whatever every venue does.
    """
    from app.services.item_match import _fetch_raw_stock_items, _fetch_stock_groups
    from app.services.received_invoice import LoadedInvoiceClient
    from app.services.supplier_catalog import catalog_unit_for_line, supplier_key_for

    lh = LoadedInvoiceClient(db, config_db, venue_id)
    items = _fetch_raw_stock_items(venue_id, db, config_db)
    groups = {g["id"]: g for g in _fetch_stock_groups(lh)}
    units = {
        u.get("id"): u.get("name")
        for u in (lh.get("/1.0/stock/internal/units") or [])
        if isinstance(u, dict)
    }
    suppliers = {
        s.get("id"): s.get("name") or s.get("supplierName")
        for s in (lh.get("/1.0/stock/suppliers") or [])
        if isinstance(s, dict)
    }
    # Printed supplier name → spec key, resolved once per distinct name.
    key_cache: dict[str, str | None] = {}

    def _key(supplier_name: object) -> str | None:
        name = str(supplier_name or "")
        if name not in key_cache:
            try:
                key_cache[name] = supplier_key_for(name, config_db)
            except Exception:  # noqa: BLE001
                key_cache[name] = None
        return key_cache[name]

    findings: list[dict] = []
    for item in items:
        group = groups.get(item.get("groupId")) or {}
        category = str(group.get("category") or "").lower()
        is_beverage = "bev" in category
        ordering_unit = units.get(item.get("orderingUnitId"))
        variants = [v for v in item.get("suppliers") or [] if isinstance(v, dict)]

        # Norm-truth comparison, per supplier variant with a stock code.
        for v in variants:
            code = v.get("stockCode")
            sup_name = suppliers.get(v.get("supplierId"))
            v_unit = units.get(v.get("unitId"))
            if not code or not sup_name or not v_unit:
                continue
            truth = catalog_unit_for_line(config_db, _key(sup_name), code)
            if truth and not _same_pack_strict(truth["unit_name"], v_unit):
                findings.append(
                    {
                        "kind": "catalogue_disagrees",
                        "item_id": item.get("id"),
                        "item_name": item.get("name"),
                        "group": group.get("name"),
                        "supplier": sup_name,
                        "code": code,
                        "current_unit": v_unit,
                        "expected_unit": truth["unit_name"],
                        "reason": (
                            f"Norm's catalogue says this product is "
                            f"{truth['unit_name']} ({truth['provenance']}) — "
                            f"the venue stocks it as {v_unit}"
                        ),
                    }
                )

        # Category rule: beverages are volumes, always.
        if is_beverage:
            for unit_name in {
                ordering_unit,
                *(units.get(v.get("unitId")) for v in variants),
            }:
                if unit_name and _is_countish(unit_name):
                    findings.append(
                        {
                            "kind": "category_rule",
                            "item_id": item.get("id"),
                            "item_name": item.get("name"),
                            "group": group.get("name"),
                            "supplier": None,
                            "code": None,
                            "current_unit": unit_name,
                            "expected_unit": None,
                            "reason": (
                                f"a beverage stocked as '{unit_name}' — "
                                "beverages are always volumes; this is a "
                                "setup error, not a preference"
                            ),
                        }
                    )
                    break  # one category finding per item is enough

    return {
        "venue_id": venue_id,
        "items_scanned": len(items),
        "findings": findings,
    }
