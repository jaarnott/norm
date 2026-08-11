"""Supplier invoice specs CRUD — per-supplier extraction instructions.

The review engine (review_and_receive_invoices) fetches these once per run via
the engine-only ``norm.get_supplier_invoice_specs`` tool and appends a matching
spec's instructions to the PDF-extraction prompt (matched on the invoice's
supplierName against name/aliases, normalized substring). Maintained in
Settings → Supplier Specs. Extraction-scope only — review checks stay uniform.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permission
from app.db.config_models import SupplierInvoiceSpec
from app.db.engine import get_config_db, get_config_db_rw
from app.db.models import User
from app.services.supplier_identity import alias_conflict, norm

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/supplier-invoice-specs", tags=["supplier-invoice-specs"])


class SpecCreate(BaseModel):
    name: str
    aliases: list[str] = []
    instructions: str = ""
    enabled: bool = True


class SpecUpdate(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    instructions: str | None = None
    enabled: bool | None = None


def _norm(text: object) -> str:
    return norm(text)


def _validate(
    name: str | None,
    aliases: list[str] | None,
    config_db: Session | None = None,
    spec_id: str | None = None,
) -> None:
    if name is not None and not name.strip():
        raise HTTPException(422, "name must not be empty")
    if aliases is None:
        return
    for a in aliases:
        if not str(a).strip():
            raise HTTPException(422, "aliases must not contain empty entries")
        if len(_norm(a)) < 3:
            # A 1-2 character alias would substring-match half the supplier
            # list — the engine ignores those, so reject them at entry.
            raise HTTPException(
                422, f"alias '{a}' is too short to match safely (min 3 characters)"
            )
    if config_db is None:
        return
    # An alias is an identity claim on a GLOBAL row, so two specs claiming one
    # string is never right — it leaves match order deciding which layout a
    # document gets. 'Service Foods' sat on both the Service Foods spec (its
    # name) and the Eurovintage spec (an alias), and alphabetical order handed
    # a food-service invoice to a wine wholesaler's prompt (11 Aug 2026).
    rows = config_db.query(SupplierInvoiceSpec).all()
    for a in aliases:
        owner = alias_conflict(rows, a, spec_id=spec_id)
        if owner:
            raise HTTPException(
                409,
                f"alias '{a}' already belongs to the '{owner}' spec — "
                "one name, one spec. Remove it there first, or add this "
                "supplier's sample to that spec instead.",
            )


def _to_dict(s: SupplierInvoiceSpec) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "aliases": s.aliases or [],
        "instructions": s.instructions or "",
        "enabled": bool(s.enabled),
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


# Reserved row: the MAIN extraction prompt the review engine uses for every
# supplier (supplier rows append to it). Editable like any spec, but it can't
# be deleted or renamed — the engine finds it by this exact name.
MAIN_PROMPT_NAME = "Main prompt"


@router.get("")
async def list_specs(
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("admin:system")),
):
    specs = (
        config_db.query(SupplierInvoiceSpec).order_by(SupplierInvoiceSpec.name).all()
    )
    return {"specs": [_to_dict(s) for s in specs]}


@router.post("", status_code=201)
async def create_spec(
    body: SpecCreate,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    _validate(body.name, body.aliases, config_db)
    existing = (
        config_db.query(SupplierInvoiceSpec)
        .filter(SupplierInvoiceSpec.name == body.name.strip())
        .first()
    )
    if existing:
        raise HTTPException(409, f"a spec for '{body.name}' already exists")
    spec = SupplierInvoiceSpec(
        name=body.name.strip(),
        aliases=[a.strip() for a in body.aliases],
        instructions=body.instructions,
        enabled=body.enabled,
    )
    config_db.add(spec)
    config_db.commit()
    config_db.refresh(spec)
    return _to_dict(spec)


@router.put("/{spec_id}")
async def update_spec(
    spec_id: str,
    body: SpecUpdate,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    spec = (
        config_db.query(SupplierInvoiceSpec)
        .filter(SupplierInvoiceSpec.id == spec_id)
        .first()
    )
    if not spec:
        raise HTTPException(404, "spec not found")
    fields = body.model_dump(exclude_unset=True)
    _validate(fields.get("name"), fields.get("aliases"), config_db, spec_id)
    if "name" in fields:
        fields["name"] = fields["name"].strip()
    if spec.name == MAIN_PROMPT_NAME and fields.get("name", spec.name) != spec.name:
        raise HTTPException(
            400, "the Main prompt row can't be renamed — the engine finds it by name"
        )
    if "aliases" in fields and fields["aliases"] is not None:
        fields["aliases"] = [a.strip() for a in fields["aliases"]]
    for k, v in fields.items():
        setattr(spec, k, v)
    config_db.commit()
    config_db.refresh(spec)
    return _to_dict(spec)


@router.delete("/{spec_id}")
async def delete_spec(
    spec_id: str,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    spec = (
        config_db.query(SupplierInvoiceSpec)
        .filter(SupplierInvoiceSpec.id == spec_id)
        .first()
    )
    if not spec:
        raise HTTPException(404, "spec not found")
    if spec.name == MAIN_PROMPT_NAME:
        raise HTTPException(
            400,
            "the Main prompt row can't be deleted — edit it, or disable it to "
            "fall back to the built-in prompt",
        )
    config_db.delete(spec)
    config_db.commit()
    return {"deleted": True}
