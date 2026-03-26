"""Background sync for working documents — pushes pending ops to external systems."""

import logging
import uuid

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import WorkingDocument, ToolCall

logger = logging.getLogger(__name__)


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
            action = _op_to_action(op_type)
            if not action:
                processed += 1
                continue

            # Build params from the op
            params = _op_to_params(op, doc)

            # Create and execute a ToolCall
            tc = ToolCall(
                id=str(uuid.uuid4()),
                thread_id=doc.thread_id,
                iteration=0,
                tool_name=f"{doc.connector_name}__{action}",
                connector_name=doc.connector_name,
                action=action,
                method=_action_method(action),
                input_params=params,
                status="executed",
            )
            db.add(tc)
            db.flush()

            result = _execute_tool_call(tc, db)

            if tc.status == "failed":
                raise Exception(
                    f"Sync op failed: {action} — {tc.error_message or result.get('error')}"
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


def _op_to_action(op_type: str) -> str | None:
    """Map an op type to a connector tool action."""
    mapping = {
        "update_shift": "update_shift",
        "add_shift": "create_shift",
        "delete_shift": "delete_shift",
        "submit_order": "create_order",
    }
    return mapping.get(op_type)


def _op_to_params(op: dict, doc: WorkingDocument) -> dict:
    """Build tool input params from an op."""
    op_type = op.get("op", "")
    ref = doc.external_ref or {}

    if op_type == "update_shift":
        fields = op.get("fields", {})
        return {
            "shift_id": op.get("shift_id", ""),
            "roster_id": fields.get("rosterId", ref.get("roster_id", "")),
            "staff_member_id": fields.get("staffMemberId", ""),
            "role_id": fields.get("roleId", ""),
            "clockin_time": fields.get("clockinTime", ""),
            "clockout_time": fields.get("clockoutTime", ""),
        }
    elif op_type == "add_shift":
        fields = op.get("fields", {})
        return {
            "roster_id": fields.get("rosterId", ref.get("roster_id", "")),
            "staff_member_id": fields.get("staffMemberId", ""),
            "role_id": fields.get("roleId", ""),
            "clockin_time": fields.get("clockinTime", ""),
            "clockout_time": fields.get("clockoutTime", ""),
        }
    elif op_type == "delete_shift":
        fields = op.get("fields", {})
        return {
            "shift_id": op.get("shift_id", ""),
            "roster_id": fields.get("rosterId", ref.get("roster_id", "")),
            "staff_member_id": fields.get("staffMemberId", ""),
            "role_id": fields.get("roleId", ""),
            "clockin_time": fields.get("clockinTime", ""),
            "clockout_time": fields.get("clockoutTime", ""),
        }
    elif op_type == "submit_order":
        # Submit the full order data from the document
        order_data = op.get("data", {})
        return {
            "product_name": order_data.get("product_name", ""),
            "venue_name": order_data.get("venue_name", ref.get("venue_name", "")),
            "quantity": str(order_data.get("quantity", "")),
        }
    return {}


def _action_method(action: str) -> str:
    """Return the HTTP method for a tool action."""
    if action in ("create_shift", "create_order"):
        return "POST"
    return "PUT"  # update_shift and delete_shift use PUT on LoadedHub
