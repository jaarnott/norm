"""Working document endpoints — local edit + sync layer."""

import logging
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db.engine import get_db
from app.db.models import Task, WorkingDocument, User
from app.auth.dependencies import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# GET endpoints
# ---------------------------------------------------------------------------

@router.get("/tasks/{task_id}/working-documents")
async def list_documents(
    task_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    docs = db.query(WorkingDocument).filter(WorkingDocument.task_id == task_id).all()
    return {"documents": [_doc_to_dict(d) for d in docs]}


@router.get("/tasks/{task_id}/working-documents/{doc_id}")
async def get_document(
    task_id: str,
    doc_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = _find_doc(db, task_id, doc_id)
    return _doc_to_dict(doc)


# ---------------------------------------------------------------------------
# PATCH — apply local edits
# ---------------------------------------------------------------------------

class PatchRequest(BaseModel):
    ops: list[dict]
    version: int


@router.patch("/tasks/{task_id}/working-documents/{doc_id}")
async def patch_document(
    task_id: str,
    doc_id: str,
    body: PatchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = _find_doc(db, task_id, doc_id)

    # Optimistic concurrency check
    if doc.version != body.version:
        raise HTTPException(status_code=409, detail=f"Version conflict: expected {doc.version}, got {body.version}")

    # Apply ops to the data
    data = doc.data
    for op in body.ops:
        data = _apply_op(data, op)

    doc.data = data
    flag_modified(doc, "data")
    doc.version += 1
    doc.updated_at = datetime.now(timezone.utc)

    # Track pending ops for sync
    pending = doc.pending_ops or []
    pending.extend(body.ops)
    doc.pending_ops = pending
    flag_modified(doc, "pending_ops")

    if doc.sync_mode == "auto":
        doc.sync_status = "dirty"
    elif doc.sync_mode == "submit":
        doc.sync_status = "pending_submit"

    db.commit()
    db.refresh(doc)

    # Trigger background sync for auto mode
    if doc.sync_mode == "auto":
        _trigger_sync(doc.id)

    return _doc_to_dict(doc)


# ---------------------------------------------------------------------------
# POST — submit (for submit-sync mode)
# ---------------------------------------------------------------------------

@router.post("/tasks/{task_id}/working-documents/{doc_id}/submit")
async def submit_document(
    task_id: str,
    doc_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = _find_doc(db, task_id, doc_id)

    if not doc.pending_ops:
        return {"status": "no_changes", "message": "No pending changes to submit."}

    from app.services.document_sync import sync_document
    result = sync_document(doc.id, db)
    db.refresh(doc)
    return {
        "status": doc.sync_status,
        "sync_error": doc.sync_error,
        "document": _doc_to_dict(doc),
    }


# ---------------------------------------------------------------------------
# POST — retry failed sync
# ---------------------------------------------------------------------------

@router.post("/tasks/{task_id}/working-documents/{doc_id}/retry")
async def retry_sync(
    task_id: str,
    doc_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = _find_doc(db, task_id, doc_id)
    if doc.sync_status != "error":
        raise HTTPException(status_code=400, detail="Document is not in error state")

    doc.sync_status = "dirty"
    doc.sync_error = None
    db.commit()
    _trigger_sync(doc.id)

    db.refresh(doc)
    return _doc_to_dict(doc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_doc(db: Session, task_id: str, doc_id: str) -> WorkingDocument:
    doc = db.query(WorkingDocument).filter(
        WorkingDocument.id == doc_id,
        WorkingDocument.task_id == task_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Working document not found")
    return doc


def _doc_to_dict(doc: WorkingDocument) -> dict:
    return {
        "id": doc.id,
        "task_id": doc.task_id,
        "doc_type": doc.doc_type,
        "connector_name": doc.connector_name,
        "sync_mode": doc.sync_mode,
        "data": doc.data,
        "external_ref": doc.external_ref,
        "sync_status": doc.sync_status,
        "sync_error": doc.sync_error,
        "version": doc.version,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }


def _apply_op(data: dict | list, op: dict) -> dict | list:
    """Apply a single structured operation to the document data.

    Supports roster operations (shifts) and order operations (lines).
    """
    op_type = op.get("op", "")

    # --- Order metadata operations ---
    if op_type == "update_notes":
        if isinstance(data, dict):
            data["notes"] = op.get("value", "")
        return data

    # --- Order operations (lines-based documents) ---
    if op_type in ("update_line", "add_line", "remove_line"):
        if not isinstance(data, dict):
            return data
        lines = data.get("lines", [])
        if not isinstance(lines, list):
            lines = []

        if op_type == "update_line":
            idx = op.get("index")
            fields = op.get("fields", {})
            if idx is not None and 0 <= idx < len(lines):
                lines[idx].update(fields)
            # Also support updating by matching product name
            elif fields.get("quantity") is not None:
                for line in lines:
                    if line.get("product") == op.get("product"):
                        line.update(fields)
                        break

        elif op_type == "add_line":
            fields = op.get("fields", op)
            new_line = {
                "product": fields.get("product", ""),
                "supplier": fields.get("supplier", ""),
                "quantity": fields.get("quantity", 1),
                "unit": fields.get("unit", "case"),
                "unit_price": fields.get("unit_price", 0),
            }
            lines.append(new_line)

        elif op_type == "remove_line":
            idx = op.get("index")
            if idx is not None and 0 <= idx < len(lines):
                lines.pop(idx)

        data["lines"] = lines
        return data

    # --- Roster operations (shifts-based documents) ---
    shifts = None
    roster_idx = None
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        if "rosteredShifts" in data[0]:
            shifts = data[0].get("rosteredShifts", [])
            roster_idx = 0
        else:
            shifts = data
    elif isinstance(data, dict) and "rosteredShifts" in data:
        shifts = data.get("rosteredShifts", [])

    if shifts is None:
        return data

    if op_type == "update_shift":
        shift_id = op.get("shift_id")
        fields = op.get("fields", {})
        for s in shifts:
            if s.get("id") == shift_id:
                s.update(fields)
                break

    elif op_type == "add_shift":
        fields = op.get("fields", {})
        shifts.append(fields)

    elif op_type == "delete_shift":
        shift_id = op.get("shift_id")
        for s in shifts:
            if s.get("id") == shift_id:
                s["datestampDeleted"] = datetime.now(timezone.utc).isoformat()
                break

    if roster_idx is not None and isinstance(data, list):
        data[roster_idx]["rosteredShifts"] = shifts
    elif isinstance(data, dict) and "rosteredShifts" in data:
        data["rosteredShifts"] = shifts

    return data


def _trigger_sync(doc_id: str):
    """Trigger background sync in a thread."""
    def run():
        from app.db.engine import SessionLocal
        db = SessionLocal()
        try:
            from app.services.document_sync import sync_document
            sync_document(doc_id, db)
        except Exception as e:
            logger.error("Background sync failed for doc %s: %s", doc_id, e)
        finally:
            db.close()

    threading.Thread(target=run, daemon=True).start()
