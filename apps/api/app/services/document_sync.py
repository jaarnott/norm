"""Background sync for working documents — pushes pending ops to external systems.

Operation-to-connector mappings are read from ComponentApiConfig records,
configured via the Components panel in Settings.
"""

import logging
import uuid

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import WorkingDocument, ToolCall

logger = logging.getLogger(__name__)


def _get_mapping(op_type: str, doc: WorkingDocument, db: Session) -> dict | None:
    """Look up the operation mapping from component_api_configs."""
    from app.db.config_models import ComponentApiConfig
    from app.db.engine import _ConfigSessionLocal

    # Determine component_key from doc_type
    doc_type_to_component = {
        "roster": "roster_editor",
        "order": "purchase_order_editor",
        "criteria": "criteria_editor",
    }
    component_key = doc_type_to_component.get(doc.doc_type)
    if not component_key:
        return None

    _cdb = _ConfigSessionLocal()
    cfg = (
        _cdb.query(ComponentApiConfig)
        .filter(
            ComponentApiConfig.component_key == component_key,
            ComponentApiConfig.connector_name == doc.connector_name,
            ComponentApiConfig.action_name == op_type,
            ComponentApiConfig.enabled.is_(True),
        )
        .first()
    )
    _cdb.close()
    if not cfg:
        return None
    return {
        "operation": cfg.action_name,
        "target_action": cfg.action_name,
        "method": cfg.method,
        "field_mapping": cfg.field_mapping or {},
        "ref_fields": cfg.ref_fields or {},
        "id_field": cfg.id_field,
    }


def _current_shift(doc: WorkingDocument, shift_id: str) -> dict:
    """The shift an op names, as the document holds it now — after the op
    itself was applied (the PATCH applies before the sync runs)."""
    data = doc.data
    shifts: list = []
    if isinstance(data, list) and data and isinstance(data[0], dict):
        if "rosteredShifts" in data[0]:
            # Every roster in the document — a window that touches two weeks
            # returns both.
            for roster in data:
                if isinstance(roster, dict):
                    shifts.extend(roster.get("rosteredShifts") or [])
        else:
            shifts = data
    elif isinstance(data, dict):
        shifts = data.get("rosteredShifts") or []
    for s in shifts:
        if isinstance(s, dict) and s.get("id") == shift_id:
            return s
    return {}


def _build_params(op: dict, doc: WorkingDocument, mapping: dict) -> dict:
    """Build tool params from an operation using the configured field mapping.

    A roster op on an existing shift is filled in from the shift as the
    document holds it. Loaded's shift writes are read-modify-write — a PUT
    replaces the whole shift, and a delete is a PUT that stamps
    datestampDeleted — but the card sends a delete as the shift id alone. So
    the delete carried no datestamp (refused as missing a required field) and,
    had it gone, would have blanked the shift's times and roster (Oct 2026).
    """
    fields = op.get("fields", op.get("data", {}))
    id_field = mapping.get("id_field")
    entity_id = op.get(id_field, op.get("shift_id")) if id_field else None
    if entity_id and doc.doc_type == "roster":
        fields = {**_current_shift(doc, entity_id), **(fields or {})}
    ref = doc.external_ref or {}
    params: dict = {}

    # Apply field_mapping: maps operation field names to tool param names
    for op_field, tool_param in mapping.get("field_mapping", {}).items():
        if op_field in fields:
            params[tool_param] = fields[op_field]

    # Apply ref_fields: pull values from external_ref for fields not in the op
    for tool_param, ref_key in mapping.get("ref_fields", {}).items():
        if tool_param not in params or not params[tool_param]:
            params[tool_param] = ref.get(ref_key, "")

    # Apply id_field: pull the entity ID from the op root (e.g., shift_id)
    if id_field:
        params[id_field] = op.get(id_field, op.get("shift_id", ""))

    return params


def sync_document(doc_id: str, db: Session, config_db: Session | None = None) -> None:
    """Process pending_ops for a working document by executing them against the external API.

    ``config_db`` is optional because both callers (the submit route and the
    background auto-sync thread) run outside a config-DB request scope; without
    one this opens its own. It used to pass none at all, and
    ``_execute_tool_call`` refuses to run without it, so every mapped op failed
    and the document sat in the error state.
    """
    if config_db is None:
        from app.db.engine import _ConfigSessionLocal

        cdb = _ConfigSessionLocal()
        try:
            return sync_document(doc_id, db, config_db=cdb)
        finally:
            cdb.close()
    doc = db.query(WorkingDocument).filter(WorkingDocument.id == doc_id).first()
    if not doc:
        return
    if doc.sync_status not in ("dirty", "pending_submit"):
        return
    if not doc.pending_ops:
        doc.sync_status = "synced"
        db.commit()
        return

    if doc.thread_id is None and any(
        _get_mapping(op.get("op", ""), doc, db) for op in doc.pending_ops
    ):
        # Every write is recorded as a ToolCall, and a ToolCall belongs to a
        # thread (tool_calls.thread_id is NOT NULL) — so a document opened
        # outside a conversation (the Roster page) failed here on a database
        # error, every time. Say so plainly instead. The chat roster card ties
        # its document to its thread, so its edits do save.
        doc.sync_status = "error"
        doc.sync_error = (
            "Edits made outside a conversation can't be saved yet — open the "
            "roster from a chat to edit it."
        )
        db.commit()
        return

    doc.sync_status = "syncing"
    db.commit()

    from app.agents.tool_loop import _execute_tool_call

    processed = 0
    try:
        for op in doc.pending_ops:
            op_type = op.get("op", "")
            mapping = _get_mapping(op_type, doc, db)
            if not mapping:
                logger.warning(
                    "No mapping for op %r on connector %s (doc_type=%s)",
                    op_type,
                    doc.connector_name,
                    doc.doc_type,
                )
                processed += 1
                continue

            params = _build_params(op, doc, mapping)
            # The document's venue picks the login. Without it the credential
            # lookup had no venue, found no venue-less Loaded connection and
            # fell back to whichever venue's login came first — a roster edit
            # written under the wrong venue's token (Oct 2026). venue_id is
            # stripped before the request renders; it only selects the login.
            if getattr(doc, "venue_id", None):
                params["venue_id"] = doc.venue_id

            tc = ToolCall(
                id=str(uuid.uuid4()),
                thread_id=doc.thread_id,
                iteration=0,
                tool_name=f"{doc.connector_name}__{mapping['target_action']}",
                connector_name=doc.connector_name,
                action=mapping["target_action"],
                method=mapping.get("method", "POST"),
                input_params=params,
                status="executed",
            )
            db.add(tc)
            db.flush()

            result = _execute_tool_call(tc, db, config_db=config_db)

            if tc.status == "failed":
                raise Exception(
                    f"Sync op failed: {mapping['target_action']} — "
                    f"{tc.error_message or result.get('error')}"
                )

            processed += 1

        # All ops succeeded
        doc.pending_ops = []
        flag_modified(doc, "pending_ops")
        doc.sync_status = "synced"
        doc.sync_error = None
        db.commit()
        logger.info("Synced %d ops for doc %s", processed, doc_id)

    except Exception as e:
        # Preserve unprocessed ops
        doc.pending_ops = doc.pending_ops[processed:]
        flag_modified(doc, "pending_ops")
        doc.sync_status = "error"
        doc.sync_error = str(e)
        db.commit()
        logger.error("Sync failed for doc %s after %d ops: %s", doc_id, processed, e)
