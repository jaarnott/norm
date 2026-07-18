"""Apply user-approved fixes to unreceivable LoadedHub invoices.

The review_and_receive_invoices consolidator proposes structured `fixes`
(link a purchase order; correct a line's unit of measure). The InvoiceFixesCard
renders them and POSTs the selected ones here. Each fix is applied
independently against the venue's LoadedHub connector — a failure isolates to
its own row.

Fix contracts (verified live in the LoadedHub test env, 18 Jul 2026):

- link_po: resolve the referenced PO number to a PO id via
  GET /1.0/stock/internal/purchase-orders (server-side searchTerm is a no-op,
  so filter client-side on orderNumber), then PUT the invoice with
  linkedPurchaseOrderId + purchaseOrderNumber set. Linking does not re-match
  lines.

- unit: mirrors Loaded's own "update variant?" flow.
    1. resolve the proposed unit name to a Loaded unit (GET .../units) — id,
       ratio, stockUnitType. If no confident match exists, the fix fails
       (the unit must be created in Loaded first).
    2. PUT the invoice with the line's unit / linkedUnitId / linkedUnitRatio
       set to the resolved unit.
    3. resolve the supplier variant (GET .../items/{itemId} → suppliers[]
       where supplierId == invoice supplier AND stockCode == line code) and
       PATCH .../item-supplier-variant/{variantId} { unitId } so future
       invoices match.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.config_models import ConnectorSpec
from app.db.engine import get_config_db, get_db
from app.db.models import ConnectorConfig, User

logger = logging.getLogger(__name__)
router = APIRouter()

_HOST = "https://api.loadedhub.com"


class ApplyFixesRequest(BaseModel):
    venue_id: str
    fixes: list[dict]


def _norm(text: object) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def _po_key(text: object) -> str:
    """Normalise a PO number for matching: alphanumerics only, drop a leading
    'po' (so 'PO#1520987' == '1520987')."""
    k = _norm(text)
    return k[2:] if k.startswith("po") else k


class _Loaded:
    """Thin authenticated LoadedHub client scoped to one venue connector."""

    def __init__(self, db: Session, config_db: Session, venue_id: str):
        from app.connectors.spec_executor import _apply_auth

        spec = (
            config_db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == "loadedhub")
            .first()
        )
        if not spec:
            raise HTTPException(404, "loadedhub connector spec not found")
        cred = (
            db.query(ConnectorConfig)
            .filter(
                ConnectorConfig.connector_name == "loadedhub",
                ConnectorConfig.enabled == "true",
                ConnectorConfig.venue_id == venue_id,
            )
            .first()
        )
        if not cred:
            raise HTTPException(400, f"loadedhub not connected for venue {venue_id}")
        creds = cred.config or {}
        headers = {"Content-Type": "application/json"}
        company_id = creds.get("x_loaded_company_id")
        if company_id:
            headers["x-loaded-company-id"] = company_id
        headers, self._auth = _apply_auth(
            headers,
            spec.auth_type,
            spec.auth_config or {},
            creds,
            spec=spec,
            db=db,
            venue_id=venue_id,
        )
        self._headers = headers

    def request(self, method: str, path: str, body: object = None) -> object:
        resp = httpx.request(
            method,
            _HOST + path,
            headers=self._headers,
            json=body if isinstance(body, (dict, list)) else None,
            auth=self._auth,
            timeout=30.0,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Loaded {method} {path} → {resp.status_code}: {resp.text[:200]}"
            )
        try:
            return resp.json()
        except Exception:
            return resp.text

    def get(self, path: str) -> object:
        return self.request("GET", path)

    def invoice(self, invoice_id: str) -> dict:
        return self.request(
            "GET",
            f"/1.0/stock/invoices/{invoice_id}"
            "?isAdjustingInvoice=false&includeDeleted=false",
        )


def _apply_link_po(lh: _Loaded, fix: dict) -> str:
    want = _po_key(fix.get("po_number"))
    if not want:
        raise RuntimeError("no PO number to link")
    pos = lh.get("/1.0/stock/internal/purchase-orders?from=1901-01-01&to=9999-12-31")
    pos = pos if isinstance(pos, list) else (pos or {}).get("data") or []
    matches = [p for p in pos if _po_key(p.get("orderNumber")) == want]
    if not matches:
        raise RuntimeError(f"purchase order {fix.get('po_number')} not found in Loaded")
    if len(matches) > 1:
        raise RuntimeError(f"purchase order {fix.get('po_number')} is ambiguous")
    po = matches[0]
    inv = lh.invoice(fix["invoice_id"])
    inv["linkedPurchaseOrderId"] = po["id"]
    inv["purchaseOrderNumber"] = po.get("orderNumber")
    lh.request("PUT", f"/1.0/stock/internal/invoices/{fix['invoice_id']}", inv)
    return f"Linked purchase order {po.get('orderNumber')}"


def _resolve_unit(lh: _Loaded, proposed: str) -> dict | None:
    """Find the Loaded unit whose name matches the proposed unit (exact, then
    guideline-equivalent via parse_unit). Returns the unit dict or None."""
    from app.services.invoice_units import parse_unit

    units = lh.get("/1.0/stock/internal/units")
    units = [u for u in (units or []) if not u.get("datestampDeleted")]
    for u in units:
        if _norm(u.get("name")) == _norm(proposed):
            return u
    target = parse_unit(proposed)
    if target:
        for u in units:
            pu = parse_unit(u.get("name"))
            if pu and pu[0] == target[0] and abs(pu[1] - target[1]) < 0.001:
                return u
    return None


def _apply_unit(lh: _Loaded, fix: dict) -> str:
    unit = _resolve_unit(lh, fix.get("proposed_unit", ""))
    if not unit:
        raise RuntimeError(
            f"unit '{fix.get('proposed_unit')}' does not exist in Loaded — "
            "create it there first"
        )
    inv = lh.invoice(fix["invoice_id"])
    line = next(
        (ln for ln in inv.get("lines") or [] if ln.get("id") == fix.get("line_id")),
        None,
    )
    if not line:
        raise RuntimeError("invoice line no longer present")
    line["unit"] = unit.get("name")
    line["linkedUnitId"] = unit.get("id")
    line["linkedUnitRatio"] = unit.get("ratio")
    lh.request("PUT", f"/1.0/stock/internal/invoices/{fix['invoice_id']}", inv)

    # Update the matched supplier variant (Loaded's "update variant?" step).
    variant_note = ""
    item = lh.get(f"/1.0/stock/internal/items/{fix['linked_item_id']}")
    variants = (item or {}).get("suppliers") or []
    supplier = fix.get("linked_supplier_id")
    code = _norm(fix.get("line_code"))
    variant = next(
        (
            v
            for v in variants
            if v.get("supplierId") == supplier and _norm(v.get("stockCode")) == code
        ),
        None,
    )
    if variant:
        lh.request(
            "PATCH",
            f"/1.0/stock/internal/item-supplier-variant/{variant['id']}",
            {"unitId": unit.get("id")},
        )
        variant_note = " and updated the variant"
    return f"Set unit to {unit.get('name')}{variant_note}"


_APPLIERS = {"link_po": _apply_link_po, "unit": _apply_unit}


@router.post("/invoice-fixes/apply")
async def apply_invoice_fixes(
    body: ApplyFixesRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Apply the selected invoice fixes; per-fix results, failures isolated."""
    lh = _Loaded(db, config_db, body.venue_id)
    results = []
    for fix in body.fixes:
        fid = fix.get("id")
        applier = _APPLIERS.get(fix.get("type"))
        if not applier:
            results.append({"id": fid, "ok": False, "message": "unknown fix type"})
            continue
        try:
            message = applier(lh, fix)
            results.append({"id": fid, "ok": True, "message": message})
        except Exception as exc:  # noqa: BLE001 — isolate each fix
            logger.warning("invoice fix %s failed: %s", fid, exc)
            results.append({"id": fid, "ok": False, "message": str(exc)})
    applied = sum(1 for r in results if r["ok"])
    return {"results": results, "applied": applied, "total": len(results)}
