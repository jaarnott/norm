"""Working document endpoints — local edit + sync layer."""

import logging
import threading
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db.engine import get_db, get_config_db
from app.db.models import WorkingDocument, User
from app.auth.dependencies import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# GET endpoints
# ---------------------------------------------------------------------------


@router.get("/threads/{thread_id}/working-documents")
async def list_documents(
    thread_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    docs = (
        db.query(WorkingDocument).filter(WorkingDocument.thread_id == thread_id).all()
    )
    return {"documents": [_doc_to_dict(d) for d in docs]}


@router.get("/threads/{thread_id}/working-documents/{doc_id}")
async def get_document(
    thread_id: str,
    doc_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = _find_doc(db, thread_id, doc_id)
    return _doc_to_dict(doc)


# ---------------------------------------------------------------------------
# PATCH — apply local edits
# ---------------------------------------------------------------------------


class PatchRequest(BaseModel):
    ops: list[dict]
    version: int


def post_apply(doc, data) -> None:
    """Recompute SERVER-OWNED derived state after a patch.

    Some document types carry a projection derived from their editable values
    plus cached reference data — for a received_invoice, the purchase-order
    reconciliation (per-line ordered qty, "ordered, not delivered"). It has
    exactly one writer: here. A client that patched it directly drifted the
    moment an edit was undone (INV-958, 10 Aug 2026), so the editor now sends
    only real edits and reads the recomputed projection back off the response.

    Pure and best-effort — no network, no config DB; never fails a patch.
    """
    try:
        if getattr(doc, "doc_type", None) == "received_invoice" and isinstance(
            data, dict
        ):
            from app.services.invoice_po_reference import project_po_reference

            project_po_reference(data)
    except Exception:  # noqa: BLE001 — a projection must never sink an edit
        logger.warning("post-apply projection failed", exc_info=True)


@router.patch("/threads/{thread_id}/working-documents/{doc_id}")
async def patch_document(
    thread_id: str,
    doc_id: str,
    body: PatchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = _find_doc(db, thread_id, doc_id)

    # Optimistic concurrency check
    if doc.version != body.version:
        raise HTTPException(
            status_code=409,
            detail=f"Version conflict: expected {doc.version}, got {body.version}",
        )

    # Apply ops to the data
    data = doc.data
    for op in body.ops:
        data = _apply_op(data, op)
    post_apply(doc, data)

    doc.data = data
    flag_modified(doc, "data")
    doc.version += 1
    doc.updated_at = datetime.now(timezone.utc)

    # Track pending ops for sync (exclude set_status — it's local-only)
    syncable_ops = [o for o in body.ops if o.get("op") != "set_status"]

    # Bank the correction before pending_ops is drained. This delta — what Norm
    # drafted versus what the human actually wanted — is the best learning
    # signal in the product, and it was being destroyed on successful sync.
    if syncable_ops:
        from app.services.memory_signals import record_draft_edit

        record_draft_edit(
            db,
            organization_id=_signal_org_id(db, doc),
            user_id=getattr(user, "id", None),
            thread_id=doc.thread_id,
            document_kind=doc.doc_type if hasattr(doc, "doc_type") else None,
            ops=syncable_ops,
        )

    if syncable_ops:
        pending = doc.pending_ops or []
        pending.extend(syncable_ops)
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


@router.post("/threads/{thread_id}/working-documents/{doc_id}/submit")
async def submit_document(
    thread_id: str,
    doc_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = _find_doc(db, thread_id, doc_id)

    if not doc.pending_ops:
        return {"status": "no_changes", "message": "No pending changes to submit."}

    from app.services.document_sync import sync_document

    sync_document(doc.id, db)
    db.refresh(doc)
    return {
        "status": doc.sync_status,
        "sync_error": doc.sync_error,
        "document": _doc_to_dict(doc),
    }


# ---------------------------------------------------------------------------
# POST — retry failed sync
# ---------------------------------------------------------------------------


@router.post("/threads/{thread_id}/working-documents/{doc_id}/retry")
async def retry_sync(
    thread_id: str,
    doc_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = _find_doc(db, thread_id, doc_id)
    if doc.sync_status != "error":
        raise HTTPException(status_code=400, detail="Document is not in error state")

    doc.sync_status = "dirty"
    doc.sync_error = None
    db.commit()
    _trigger_sync(doc.id)

    db.refresh(doc)
    return _doc_to_dict(doc)


# ---------------------------------------------------------------------------
# Taskless working documents (for functional pages)
# ---------------------------------------------------------------------------


class FromConnectorRequest(BaseModel):
    connector_name: str
    action: str
    params: dict = {}
    doc_type: str = "generic"
    venue_id: str | None = None


@router.post("/working-documents/from-connector")
async def create_from_connector(
    body: FromConnectorRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Fetch data from a connector and create a working document (no task required).

    This enables the working document edit/sync pattern for functional pages
    that load data directly without going through the LLM agent.
    """
    # Execute the connector tool to fetch data
    from app.agents.internal_tools import get_handler

    handler = get_handler(body.connector_name, body.action)

    if handler:
        result = handler(body.params, db, None)
        data = result.get("data", result)
    else:
        # External connector — use spec executor
        from app.db.models import ConnectorSpec, ConnectorConfig
        from app.connectors.spec_executor import execute_spec

        spec = (
            config_db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == body.connector_name)
            .first()
        )
        if not spec:
            raise HTTPException(404, f"Connector not found: {body.connector_name}")

        tool_def = None
        for t in spec.tools or []:
            if t.get("action") == body.action:
                tool_def = t
                break
        if not tool_def:
            raise HTTPException(404, f"Tool not found: {body.action}")

        config_query = db.query(ConnectorConfig).filter(
            ConnectorConfig.connector_name == body.connector_name,
            ConnectorConfig.enabled == "true",
        )
        if body.venue_id:
            config_query = config_query.filter(
                ConnectorConfig.venue_id == body.venue_id
            )
        config_row = config_query.first()
        if not config_row:
            raise HTTPException(
                400, f"No credentials configured for {body.connector_name}"
            )

        # Venue params identify which credentials to use; they are not API
        # fields, and leaving them in makes them render into templates.
        params_for_spec = {
            k: v
            for k, v in (body.params or {}).items()
            if k not in ("venue", "venue_id", "venue_name")
        }

        # venue_id is required, not optional: without it get_valid_access_token
        # falls back to an unfiltered .first() and authenticates as whichever
        # venue happens to be first. LoadedHub scopes by token and ignores
        # x-loaded-company-id, so the call is made against the wrong venue —
        # which is why loading a roster here returned a bare 500 while the
        # identical request through /test (which passes it) succeeded.
        conn_result, _rendered = execute_spec(
            spec,
            tool_def,
            params_for_spec,
            config_row.config,
            db,
            venue_id=config_row.venue_id,
        )
        if not conn_result.success:
            raise HTTPException(502, f"Connector error: {conn_result.error_message}")
        data = conn_result.response_payload

    # Create working document
    doc = WorkingDocument(
        thread_id=None,
        doc_type=body.doc_type,
        connector_name=body.connector_name,
        venue_id=body.venue_id,
        sync_mode="auto",
        data=data,
        external_ref=body.params or None,
        sync_status="synced",
        version=1,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    return _doc_to_dict(doc)


@router.get("/working-documents/{doc_id}")
async def get_standalone_document(
    doc_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get a working document by ID (no task context required)."""
    doc = db.query(WorkingDocument).filter(WorkingDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Working document not found")
    return _doc_to_dict(doc)


@router.patch("/working-documents/{doc_id}")
async def patch_standalone_document(
    doc_id: str,
    body: PatchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Patch a working document by ID (no task context required)."""
    doc = db.query(WorkingDocument).filter(WorkingDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Working document not found")

    if doc.version != body.version:
        raise HTTPException(
            status_code=409,
            detail=f"Version conflict: expected {doc.version}, got {body.version}",
        )

    data = doc.data
    for op in body.ops:
        data = _apply_op(data, op)
    post_apply(doc, data)

    doc.data = data
    flag_modified(doc, "data")
    doc.version += 1
    doc.updated_at = datetime.now(timezone.utc)

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

    if doc.sync_mode == "auto":
        _trigger_sync(doc.id)

    return _doc_to_dict(doc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_doc(db: Session, thread_id: str, doc_id: str) -> WorkingDocument:  # noqa: ARG001 — thread_id kept for the route shape
    # By id alone: ref-keyed documents (one doc per invoice) are shared across
    # threads, so the URL's thread is just the card's context — a card in
    # thread B legitimately holds a doc whose home thread is A. The id is an
    # unguessable UUID and auth (get_current_user) is unchanged.
    doc = db.query(WorkingDocument).filter(WorkingDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Working document not found")
    return doc


def _doc_to_dict(doc: WorkingDocument) -> dict:
    return {
        "id": doc.id,
        "thread_id": doc.thread_id,
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


def _signal_org_id(db, doc) -> str | None:
    """Organisation for a working document, via its thread's owner."""
    from app.db.models import OrganizationMembership, Thread

    thread = db.query(Thread).filter(Thread.id == doc.thread_id).first()
    if not thread or not thread.user_id:
        return None
    membership = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == thread.user_id)
        .first()
    )
    return membership.organization_id if membership else None


def _apply_op(data: dict | list, op: dict) -> dict | list:
    """Apply a single structured operation to the document data.

    Supports roster operations (shifts) and order operations (lines).
    """
    op_type = op.get("op", "")

    # --- Status operations ---
    if op_type == "set_status":
        if isinstance(data, dict):
            data["status"] = op.get("value", "draft")
        return data

    # --- Order metadata operations ---
    if op_type == "update_notes":
        if isinstance(data, dict):
            data["notes"] = op.get("value", "")
        return data

    # --- Header operations (top-level field merge) ---
    # Used by the received-invoice editor to link a PO (linked_purchase_order_id
    # + purchase_order_number) and set header state, without a bespoke op per
    # field. Generic and safe: only the fields the caller names are merged.
    if op_type == "update_header":
        if isinstance(data, dict):
            fields = op.get("fields")
            if isinstance(fields, dict):
                data.update(fields)
        return data

    # --- Menu operations (a MenuModel: groups[] -> lines[]) ---
    # The menu editor's lines are nested under sections (groups), so they need
    # their own ops rather than the top-level order line ops below. Addressed by
    # id (group_id / line_id); new groups and lines carry a client-generated id.
    if op_type in (
        "add_menu_group",
        "update_menu_group",
        "remove_menu_group",
        "add_menu_line",
        "update_menu_line",
        "remove_menu_line",
    ):
        if not isinstance(data, dict):
            return data
        groups = data.get("groups")
        if not isinstance(groups, list):
            groups = []

        def _find_group(gid):
            return next(
                (g for g in groups if isinstance(g, dict) and g.get("id") == gid),
                None,
            )

        if op_type == "add_menu_group":
            group = dict(op.get("group", {}))
            group.setdefault("lines", [])
            groups.append(group)
        elif op_type == "update_menu_group":
            g = _find_group(op.get("group_id"))
            if g is not None:
                g.update(op.get("fields", {}))
        elif op_type == "remove_menu_group":
            gid = op.get("group_id")
            groups = [g for g in groups if g.get("id") != gid]
        elif op_type == "add_menu_line":
            g = _find_group(op.get("group_id"))
            if g is not None:
                g.setdefault("lines", []).append(dict(op.get("line", {})))
        elif op_type == "update_menu_line":
            g = _find_group(op.get("group_id"))
            if g is not None:
                ln = next(
                    (
                        line
                        for line in g.get("lines", [])
                        if isinstance(line, dict)
                        and line.get("id") == op.get("line_id")
                    ),
                    None,
                )
                if ln is not None:
                    ln.update(op.get("fields", {}))
        elif op_type == "remove_menu_line":
            g = _find_group(op.get("group_id"))
            if g is not None:
                g["lines"] = [
                    line
                    for line in g.get("lines", [])
                    if line.get("id") != op.get("line_id")
                ]

        data["groups"] = groups
        return data

    # --- Recipe operations (ingredient lines, addressed by line id) ---
    # A recipe draft's lines are flat (unlike the menu's group nesting) but need
    # their own ops so an ingredient's kind/ref/unit/quantity are addressed by a
    # stable id — server rebuilds may reorder, so index-addressing is unsafe.
    # Recipe name / yield / stocktake flag go through update_header; the method
    # goes through update_notes.
    if op_type in ("add_recipe_line", "update_recipe_line", "remove_recipe_line"):
        if not isinstance(data, dict):
            return data
        lines = data.get("lines")
        if not isinstance(lines, list):
            lines = []
        if op_type == "add_recipe_line":
            new_line = {k: v for k, v in dict(op.get("line", {})).items()}
            new_line.setdefault("id", op.get("line_id") or str(uuid.uuid4()))
            lines.append(new_line)
        elif op_type == "update_recipe_line":
            lid = op.get("line_id")
            for ln in lines:
                if isinstance(ln, dict) and ln.get("id") == lid:
                    ln.update(op.get("fields", {}))
                    break
        elif op_type == "remove_recipe_line":
            lid = op.get("line_id")
            lines = [
                ln for ln in lines if not (isinstance(ln, dict) and ln.get("id") == lid)
            ]
        data["lines"] = lines
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
            line_id = op.get("line_id")
            target = None
            # Prefer id-addressing: server-side rebuilds (reshape, review
            # merges) can reorder or append lines, so a client index taken
            # from an older snapshot may point at the wrong line.
            if line_id is not None:
                target = next(
                    (
                        line
                        for line in lines
                        if isinstance(line, dict) and line.get("id") == line_id
                    ),
                    None,
                )
            if target is None and idx is not None and 0 <= idx < len(lines):
                target = lines[idx]
            if target is not None:
                target.update(fields)
            # Also support updating by matching product name
            elif fields.get("quantity") is not None:
                for line in lines:
                    if line.get("product") == op.get("product"):
                        line.update(fields)
                        break

        elif op_type == "add_line":
            fields = op.get("fields", op)
            # Preserve all fields from the line (including enrichment fields
            # like stock_code, itemId, supplierId, unitId, etc.)
            new_line = {k: v for k, v in fields.items() if k != "op"}
            # Ensure required fields have defaults
            new_line.setdefault("product", "")
            new_line.setdefault("supplier", "")
            new_line.setdefault("quantity", 1)
            new_line.setdefault("unit", "case")
            new_line.setdefault("unit_price", 0)
            lines.append(new_line)

        elif op_type == "remove_line":
            idx = op.get("index")
            if idx is not None and 0 <= idx < len(lines):
                lines.pop(idx)

        data["lines"] = lines
        return data

    # --- Criteria operations ---
    if isinstance(data, dict) and "criteria" in data:
        criteria_list = data.get("criteria", [])

        if op_type == "update_criterion":
            crit_id = op.get("criterion_id")
            fields = op.get("fields", {})
            for c in criteria_list:
                if c.get("id") == crit_id:
                    c.update(fields)
                    break
            data["criteria"] = criteria_list
            return data

        if op_type == "add_criterion":
            fields = op.get("fields", {})
            if not fields.get("id"):
                import uuid as _uuid

                fields["id"] = str(_uuid.uuid4())[:8]
            criteria_list.append(fields)
            data["criteria"] = criteria_list
            return data

        if op_type == "remove_criterion":
            crit_id = op.get("criterion_id")
            data["criteria"] = [c for c in criteria_list if c.get("id") != crit_id]
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
        logger.warning(
            "_apply_op: could not locate shifts array in data (type=%s, keys=%s)",
            type(data).__name__,
            list(data.keys())
            if isinstance(data, dict)
            else f"list[{len(data)}]"
            if isinstance(data, list)
            else "?",
        )
        return data

    if op_type == "update_shift":
        shift_id = op.get("shift_id")
        fields = op.get("fields", {})
        found = False
        for s in shifts:
            if s.get("id") == shift_id:
                s.update(fields)
                found = True
                logger.info(
                    "update_shift: updated shift %s with %s",
                    shift_id,
                    list(fields.keys()),
                )
                break
        if not found:
            logger.warning(
                "update_shift: shift %s not found in %d shifts (ids: %s)",
                shift_id,
                len(shifts),
                [s.get("id") for s in shifts[:5]],
            )

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
