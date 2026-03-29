"""Background sync for working documents — pushes pending ops to external systems.

Operation-to-connector mappings are read from ConnectorSpec.operation_mappings
instead of being hardcoded, so new connectors can be configured via the UI.
"""

import logging
import uuid

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import WorkingDocument, ToolCall

logger = logging.getLogger(__name__)


def _get_mapping(op_type: str, doc: WorkingDocument, db: Session) -> dict | None:
    """Look up the operation mapping from the connector spec."""
    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    _cdb = _ConfigSessionLocal()
    spec = (
        _cdb.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == doc.connector_name)
        .first()
    )
    _cdb.close()
    if not spec or not spec.operation_mappings:
        return None
    for m in spec.operation_mappings:
        if m.get("operation") == op_type and m.get("doc_type") == doc.doc_type:
            return m
    return None


def _build_params(op: dict, doc: WorkingDocument, mapping: dict) -> dict:
    """Build tool params from an operation using the configured field mapping."""
    fields = op.get("fields", op.get("data", {}))
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
    id_field = mapping.get("id_field")
    if id_field:
        params[id_field] = op.get(id_field, op.get("shift_id", ""))

    return params


def sync_document(doc_id: str, db: Session) -> None:
    """Process pending_ops for a working document by executing them against the external API."""
    doc = db.query(WorkingDocument).filter(WorkingDocument.id == doc_id).first()
    if not doc:
        return
    if doc.sync_status not in ("dirty", "pending_submit"):
        return
    if not doc.pending_ops:
        doc.sync_status = "synced"
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

            result = _execute_tool_call(tc, db)

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
