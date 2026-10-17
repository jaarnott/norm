"""Procurement threads in their original, pre-tool-loop shape: how they read
(``get_order`` — automated-task and older procurement threads still display
through it) and the thread-level approve / reject / submit buttons.

The draft-order store behind the old ``/api/orders`` endpoints (the ``orders``
and ``order_lines`` tables) was removed on 17 Oct 2026: nothing created a row
in production, ever — stock orders are placed from the purchase-order card,
straight to LoadedHub.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import Thread, Approval
from app.connectors.registry import resolve_connector
from app.services.integration_service import execute_submission_v2


def get_order(db: Session, thread_id: str) -> dict | None:
    task = (
        db.query(Thread)
        .filter(Thread.id == thread_id, Thread.domain == "procurement")
        .first()
    )
    if not task:
        return None
    return _thread_to_dict(task)


def approve_order(db: Session, thread_id: str, user=None) -> dict | None:
    result = _set_status(db, thread_id, "approved")
    if result:
        db.add(
            Approval(
                thread_id=thread_id,
                action="approved",
                performed_by=user.email if user else "system",
                user_id=user.id if user else None,
            )
        )
        db.commit()
    return result


def reject_order(db: Session, thread_id: str, user=None) -> dict | None:
    result = _set_status(db, thread_id, "rejected")
    if result:
        db.add(
            Approval(
                thread_id=thread_id,
                action="rejected",
                performed_by=user.email if user else "system",
                user_id=user.id if user else None,
            )
        )
        db.commit()
    return result


def submit_order(db: Session, thread_id: str) -> dict | None:
    task = (
        db.query(Thread)
        .filter(Thread.id == thread_id, Thread.domain == "procurement")
        .first()
    )
    if not task or task.status != "approved":
        return None

    action = (task.extracted_fields or {}).get("_action", "create_order")
    spec, creds, operation = resolve_connector("procurement", action, db)
    run = execute_submission_v2(db, task, spec, creds, operation)

    if run.status == "success":
        task.status = "submitted"
    else:
        task.status = "approved"  # keep approved so user can retry

    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    return _thread_to_dict(task)


def _set_status(db: Session, thread_id: str, status: str) -> dict | None:
    task = (
        db.query(Thread)
        .filter(Thread.id == thread_id, Thread.domain == "procurement")
        .first()
    )
    if not task:
        return None
    task.status = status
    task.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    return _thread_to_dict(task)


def _thread_to_dict(task: Thread) -> dict:
    extracted = task.extracted_fields or {}
    product = extracted.get("product")
    venue = extracted.get("venue")
    quantity = extracted.get("quantity")

    line_summary = None
    if product and venue:
        qty = quantity or "?"
        supplier = product.get("supplier", "?")
        line_summary = f"{qty} x {product['name']} -> {venue['name']} via {supplier}"

    # Latest integration run
    integration_run = None
    if task.integration_runs:
        latest_run = task.integration_runs[-1]
        integration_run = {
            "connector": latest_run.connector_name,
            "status": latest_run.status,
            "reference": (latest_run.response_payload or {}).get("order_reference"),
            "submitted_at": latest_run.created_at.isoformat()
            if latest_run.created_at
            else None,
            "error": latest_run.error_message,
        }

    # Latest approval
    approval = None
    if task.approvals:
        latest_approval = task.approvals[-1]
        approval = {
            "action": latest_approval.action,
            "performed_by": latest_approval.performed_by or "system",
            "performed_at": latest_approval.performed_at.isoformat()
            if latest_approval.performed_at
            else None,
        }

    return {
        "id": task.id,
        "domain": "procurement",
        "message": task.raw_prompt,
        "intent": task.intent,
        "title": task.title,
        "status": task.status,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "venue": {"id": venue["id"], "name": venue["name"]} if venue else None,
        "product": {
            "id": product["id"],
            "name": product["name"],
            "unit": product.get("unit", "case"),
            "category": product.get("category"),
        }
        if product
        else None,
        "supplier": product.get("supplier") if product else None,
        "quantity": quantity,
        "line_summary": line_summary,
        "missing_fields": task.missing_fields or [],
        "clarification_question": task.clarification_question,
        "conversation": [
            {
                "role": m.role,
                "text": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in sorted(task.messages, key=lambda x: x.created_at)
        ],
        "integration_run": integration_run,
        "approval": approval,
        "llm_calls": [
            {
                "id": c.id,
                "call_type": c.call_type,
                "model": c.model,
                "system_prompt": c.system_prompt,
                "user_prompt": c.user_prompt,
                "raw_response": c.raw_response,
                "parsed_response": c.parsed_response,
                "status": c.status,
                "error_message": c.error_message,
                "duration_ms": c.duration_ms,
                "tools_provided": c.tools_provided,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in task.llm_calls
        ],
        "tool_calls": [
            {
                "id": tc.id,
                "iteration": tc.iteration,
                "tool_name": tc.tool_name,
                "connector_name": tc.connector_name,
                "action": tc.action,
                "method": tc.method,
                "input_params": tc.input_params,
                "status": tc.status,
                "result_payload": tc.result_payload,
                "error_message": tc.error_message,
                "duration_ms": tc.duration_ms,
                "created_at": tc.created_at.isoformat() if tc.created_at else None,
            }
            for tc in sorted(task.tool_calls, key=lambda x: x.created_at)
        ]
        if task.tool_calls
        else [],
        "thinking_steps": task.thinking_steps or [],
    }
