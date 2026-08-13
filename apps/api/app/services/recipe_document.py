"""Recipe working document — the draft the editor and the agent both edit.

A recipe opened in the editor becomes a ``WorkingDocument(doc_type="recipe")``
keyed by ``{recipe_id, venue_id}``, so the Recipes page and any agent thread
resolve to the SAME draft (the received-invoice pattern in ``invoice_fixes.py``).
The editor patches it with ops (see ``working_documents._apply_op`` recipe ops);
the agent edits it through the executive_chef recipe-edit tool; a Save writes it
to LoadedHub via ``recipe_save.save_recipe``.

``build_recipe_draft`` mirrors the frontend ``toDraft`` (RecipeEditor.tsx): Loaded
stores line quantities in BASE units, the draft works in DISPLAY units
(quantity / unitRatio), and a removed line (``deletedAt``) is dropped.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified


def _num(v: object) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def build_recipe_draft(r: dict) -> dict:
    """A get_recipe payload -> the editable draft (display units)."""
    cv = r.get("currentVersion") or {}
    yr = _num(cv.get("yieldUnitRatio")) or 1
    lines = []
    for line in cv.get("lines") or []:
        if not isinstance(line, dict) or line.get("deletedAt"):
            continue
        ratio = _num(line.get("unitRatio")) or 1
        is_item = bool(line.get("itemId"))
        lines.append(
            {
                "id": line.get("id") or str(uuid.uuid4()),
                "kind": "item" if is_item else "recipe",
                "ref_id": line.get("itemId") or line.get("recipeId"),
                "name": (line.get("itemName") or line.get("recipeName") or "").strip(),
                "unit_id": line.get("unitId"),
                "unit_name": line.get("unitName"),
                "unit_ratio": ratio,
                "quantity": _num(line.get("quantity")) / ratio,
            }
        )
    return {
        "recipe_id": r.get("id"),
        "version_id": cv.get("id"),
        "name": (r.get("name") or "").strip(),
        "notes": cv.get("notes") or r.get("notes") or "",
        "is_counted_in_stocktake": bool(r.get("isCountedInStocktake")),
        "yield_quantity": _num(cv.get("yieldQuantity")) / yr,
        "yield_unit_id": cv.get("yieldUnitId"),
        "yield_unit_name": cv.get("yieldUnitName"),
        "lines": lines,
    }


def find_recipe_doc(db: Session, venue_id: str, recipe_id: str):
    """The existing recipe working doc for this venue+recipe, if any."""
    from app.db.models import WorkingDocument

    for d in (
        db.query(WorkingDocument)
        .filter(
            WorkingDocument.doc_type == "recipe",
            WorkingDocument.venue_id == venue_id,
        )
        .order_by(WorkingDocument.created_at.desc())
        .all()
    ):
        if (d.external_ref or {}).get("recipe_id") == recipe_id:
            return d
    return None


def load_recipe_payload(
    venue_id: str, recipe_id: str, db: Session, config_db: Session
) -> dict:
    """Fetch the full recipe from LoadedHub via the recipe_editor component API."""
    from app.services.component_api import execute_component_action

    res = execute_component_action(
        "recipe_editor", "get_recipe", {"recipe_id": recipe_id}, venue_id, db, config_db
    )
    data = res.get("data") if isinstance(res, dict) else None
    return data if isinstance(data, dict) else {}


def open_recipe_doc(venue_id: str, recipe_id: str, db: Session, config_db: Session):
    """Open the recipe as a working document (the shared draft the editor and the
    agent both edit, keyed by {recipe_id, venue_id}).

    Opening is a deliberate reload, so the data is always rebuilt from LoadedHub —
    this keeps it in step after a Save and never resurrects a stale draft. The
    remount case (which must NOT reload) restores from the client session store,
    not through here. The same row is reused so the agent's ref lookup still
    resolves; mid-session agent edits are applied to it and the editor's poll
    reflects them until the next explicit open.
    """
    from app.db.models import WorkingDocument

    draft = build_recipe_draft(load_recipe_payload(venue_id, recipe_id, db, config_db))
    existing = find_recipe_doc(db, venue_id, recipe_id)
    if existing:
        existing.data = draft
        existing.pending_ops = []
        existing.sync_status = "synced"
        existing.version = (existing.version or 0) + 1
        flag_modified(existing, "data")
        db.commit()
        db.refresh(existing)
        return existing

    doc = WorkingDocument(
        thread_id=None,
        doc_type="recipe",
        connector_name="loadedhub",
        venue_id=venue_id,
        sync_mode="submit",
        data=draft,
        external_ref={"recipe_id": recipe_id, "venue_id": venue_id},
        sync_status="synced",
        version=1,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc
