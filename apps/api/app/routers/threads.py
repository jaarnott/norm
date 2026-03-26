"""Unified thread lifecycle endpoints."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import Thread, Approval, ToolCall, User
from app.auth.dependencies import get_current_user, require_permission
from app.services.order_service import (
    get_order,
    approve_order,
    reject_order,
    submit_order,
)
from app.services.hr_service import (
    get_task as get_hr_task,
    approve_task as approve_hr_task,
    reject_task as reject_hr_task,
    submit_task as submit_hr_task,
)
from app.agents.reports.context import _report_task_to_dict

router = APIRouter()


def _get_automated_task_meta(db: Session, conversation_thread_id: str) -> dict | None:
    """Return automated task metadata for a conversation thread, or None."""
    from app.db.models import AutomatedTask as AT

    at = (
        db.query(AT).filter(AT.conversation_thread_id == conversation_thread_id).first()
    )
    if not at:
        return None
    return {
        "id": at.id,
        "title": at.title,
        "description": at.description,
        "schedule_type": at.schedule_type,
        "schedule_config": at.schedule_config or {},
        "status": at.status,
        "prompt": at.prompt,
        "task_config": at.task_config or {},
        "thread_summary": at.thread_summary,
        "last_run_at": at.last_run_at.isoformat() if at.last_run_at else None,
    }


def _find(db: Session, thread_id: str) -> tuple[dict | None, str]:
    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        return None, ""
    # Tool-loop threads and automated conversation threads use a generic dict format
    if thread.intent and (
        thread.intent.endswith(".tool_use")
        or thread.intent.endswith(".automated_conversation")
    ):
        d = _tool_use_thread_to_dict(thread)
        # Attach automated task metadata if this is a conversation thread
        if thread.intent.endswith(".automated_conversation"):
            d["automated_task"] = _get_automated_task_meta(db, thread.id)
        return d, thread.domain
    if thread.domain == "procurement":
        return get_order(db, thread_id), "procurement"
    if thread.domain == "hr":
        return get_hr_task(db, thread_id), "hr"
    if thread.domain == "reports":
        return _report_task_to_dict(thread), "reports"
    return None, ""


def _tool_use_thread_to_dict(thread: Thread) -> dict:
    """Serialize a tool-loop thread to a generic response dict."""
    conversation = [
        {
            "role": m.role,
            "text": m.content,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "display_blocks": m.display_blocks,
        }
        for m in sorted(thread.messages, key=lambda x: x.created_at)
    ]
    tool_calls = [
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
            "slimmed_content": tc.slimmed_content,
            "error_message": tc.error_message,
            "duration_ms": tc.duration_ms,
            "created_at": tc.created_at.isoformat() if tc.created_at else None,
        }
        for tc in sorted(thread.tool_calls, key=lambda x: x.created_at)
    ]
    llm_calls = [
        {
            "id": lc.id,
            "call_type": lc.call_type,
            "model": lc.model,
            "system_prompt": lc.system_prompt,
            "user_prompt": lc.user_prompt,
            "raw_response": lc.raw_response,
            "parsed_response": lc.parsed_response,
            "status": lc.status,
            "error_message": lc.error_message,
            "duration_ms": lc.duration_ms,
            "input_tokens": lc.input_tokens,
            "output_tokens": lc.output_tokens,
            "tools_provided": lc.tools_provided,
            "created_at": lc.created_at.isoformat() if lc.created_at else None,
        }
        for lc in sorted(thread.llm_calls, key=lambda x: x.created_at)
    ]
    return {
        "id": thread.id,
        "domain": thread.domain,
        "intent": thread.intent,
        "title": thread.title,
        "message": thread.raw_prompt or "",
        "status": thread.status,
        "extracted_fields": thread.extracted_fields,
        "missing_fields": thread.missing_fields,
        "clarification_question": thread.clarification_question,
        "created_at": thread.created_at.isoformat() if thread.created_at else None,
        "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
        "conversation": conversation,
        "tool_calls": tool_calls,
        "llm_calls": llm_calls,
        "thinking_steps": thread.thinking_steps or [],
    }


@router.get("/threads")
async def get_all_threads(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("tasks:read")),
):
    """Return lightweight thread summaries for the sidebar list.

    Only includes fields needed by TaskCard (id, domain, status, title, etc.).
    Full conversation/tool_calls/llm_calls are loaded on demand via GET /threads/{id}.
    """
    from sqlalchemy import or_

    threads = (
        db.query(Thread)
        .filter(
            Thread.user_id == user.id,
            # Exclude automated task execution runs — only show conversation threads
            or_(Thread.intent.is_(None), ~Thread.intent.like("%.automated_task")),
        )
        .order_by(Thread.created_at.desc())
        .all()
    )
    return {"threads": [_thread_summary(t, db) for t in threads]}


def _thread_summary(thread: Thread, db: Session | None = None) -> dict:
    """Lightweight serialisation — no relationships loaded."""
    extracted = thread.extracted_fields or {}
    venue = extracted.get("venue")
    product = extracted.get("product")

    summary: dict = {
        "id": thread.id,
        "domain": thread.domain,
        "intent": thread.intent,
        "title": thread.title,
        "message": thread.raw_prompt or "",
        "status": thread.status,
        "created_at": thread.created_at.isoformat() if thread.created_at else None,
        "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
        "missing_fields": thread.missing_fields or [],
        "clarification_question": thread.clarification_question,
        "thinking_steps": thread.thinking_steps or [],
    }

    # Domain-specific card fields
    if thread.domain == "procurement":
        summary["venue"] = {"id": venue["id"], "name": venue["name"]} if venue else None
        summary["product"] = (
            {
                "id": product["id"],
                "name": product["name"],
                "unit": product.get("unit", "case"),
                "category": product.get("category"),
            }
            if product
            else None
        )
        summary["supplier"] = product.get("supplier") if product else None
        summary["quantity"] = extracted.get("quantity")
    elif thread.domain == "hr":
        summary["employee_name"] = extracted.get("employee_name")
        summary["venue"] = {"id": venue["id"], "name": venue["name"]} if venue else None
        summary["role"] = extracted.get("role")
        summary["start_date"] = extracted.get("start_date")
    elif thread.domain == "reports":
        summary["report_type"] = extracted.get("report_type")

    # Automated task metadata
    if db and thread.intent and thread.intent.endswith(".automated_conversation"):
        summary["automated_task"] = _get_automated_task_meta(db, thread.id)

    return summary


@router.delete("/threads/{thread_id}")
async def delete_thread(
    thread_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("tasks:write")),
):
    thread = (
        db.query(Thread)
        .filter(Thread.id == thread_id, Thread.user_id == user.id)
        .first()
    )
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    db.delete(thread)
    db.commit()
    return {"ok": True}


@router.get("/threads/{thread_id}")
async def get_thread_detail(
    thread_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("tasks:read")),
):
    thread, _ = _find(db, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread


@router.post("/threads/{thread_id}/approve")
async def approve(
    thread_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Check if this is a tool-approval request
    raw_thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if raw_thread and raw_thread.status == "awaiting_tool_approval":
        return _approve_tool_calls(db, raw_thread, user)

    thread, domain = _find(db, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    if domain == "procurement":
        return approve_order(db, thread_id, user=user)
    if domain == "hr":
        return approve_hr_task(db, thread_id, user=user)
    if domain == "reports":
        return _approve_report(db, thread_id, user=user)
    raise HTTPException(status_code=400, detail="Unsupported domain")


@router.post("/threads/{thread_id}/reject")
async def reject(
    thread_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Check if this is a tool-rejection request
    raw_thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if raw_thread and raw_thread.status == "awaiting_tool_approval":
        return _reject_tool_calls(db, raw_thread, user)

    thread, domain = _find(db, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    if domain == "procurement":
        return reject_order(db, thread_id, user=user)
    if domain == "hr":
        return reject_hr_task(db, thread_id, user=user)
    if domain == "reports":
        return _reject_report(db, thread_id, user=user)
    raise HTTPException(status_code=400, detail="Unsupported domain")


@router.post("/threads/{thread_id}/submit")
async def submit(
    thread_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    thread, domain = _find(db, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    if domain == "procurement":
        result = submit_order(db, thread_id)
    elif domain == "hr":
        result = submit_hr_task(db, thread_id)
    elif domain == "reports":
        # Reports don't submit externally — approve is the terminal action
        raise HTTPException(
            status_code=400, detail="Reports cannot be submitted to external systems"
        )
    else:
        raise HTTPException(status_code=400, detail="Unsupported domain")
    if not result:
        raise HTTPException(status_code=400, detail="Thread not in approved state")
    return result


def _approve_report(db: Session, thread_id: str, user: User | None = None) -> dict:
    from datetime import datetime, timezone

    thread = (
        db.query(Thread)
        .filter(Thread.id == thread_id, Thread.domain == "reports")
        .first()
    )
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    thread.status = "approved"
    thread.updated_at = datetime.now(timezone.utc)
    db.add(
        Approval(
            thread_id=thread_id,
            action="approved",
            performed_by=user.email if user else "system",
            user_id=user.id if user else None,
        )
    )
    db.commit()
    db.refresh(thread)
    return _report_task_to_dict(thread)


def _reject_report(db: Session, thread_id: str, user: User | None = None) -> dict:
    from datetime import datetime, timezone

    thread = (
        db.query(Thread)
        .filter(Thread.id == thread_id, Thread.domain == "reports")
        .first()
    )
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    thread.status = "rejected"
    thread.updated_at = datetime.now(timezone.utc)
    db.add(
        Approval(
            thread_id=thread_id,
            action="rejected",
            performed_by=user.email if user else "system",
            user_id=user.id if user else None,
        )
    )
    db.commit()
    db.refresh(thread)
    return _report_task_to_dict(thread)


def _update_approval_display_block(thread: Thread, new_status: str) -> None:
    """Update the tool_approval display block status in the approval message."""
    from sqlalchemy.orm.attributes import flag_modified

    for msg in thread.messages:
        if msg.display_blocks:
            blocks = list(msg.display_blocks)
            updated = False
            for block in blocks:
                if block.get("component") == "tool_approval":
                    block["data"]["status"] = new_status
                    updated = True
            if updated:
                msg.display_blocks = blocks
                flag_modified(msg, "display_blocks")


def _approve_tool_calls(db: Session, thread: Thread, user: User) -> dict:
    """Approve pending write tool calls and resume the agentic loop."""
    from app.agents.tool_loop import resume_tool_loop
    from app.agents.prompt_builder import build_tool_definitions

    # Mark pending tool calls as approved
    for tc_id in thread.pending_tool_call_ids or []:
        tc = db.query(ToolCall).filter(ToolCall.id == tc_id).first()
        if tc and tc.status == "pending_approval":
            tc.status = "approved"
    _update_approval_display_block(thread, "approved")
    db.flush()

    # Record the approval
    db.add(
        Approval(
            thread_id=thread.id,
            action="tool_calls_approved",
            performed_by=user.email,
            user_id=user.id,
        )
    )
    db.flush()

    # Resume the loop
    system_prompt, anthropic_tools = build_tool_definitions(
        thread.domain, db, user_id=user.id
    )
    return resume_tool_loop(thread, db, system_prompt, anthropic_tools)


def _reject_tool_calls(db: Session, thread: Thread, user: User) -> dict:
    """Reject pending write tool calls and resume the loop (tool results will say 'rejected')."""
    from app.agents.tool_loop import resume_tool_loop
    from app.agents.prompt_builder import build_tool_definitions

    # Mark pending tool calls as rejected
    for tc_id in thread.pending_tool_call_ids or []:
        tc = db.query(ToolCall).filter(ToolCall.id == tc_id).first()
        if tc and tc.status == "pending_approval":
            tc.status = "rejected"
    _update_approval_display_block(thread, "rejected")
    db.flush()

    # Record the rejection
    db.add(
        Approval(
            thread_id=thread.id,
            action="tool_calls_rejected",
            performed_by=user.email,
            user_id=user.id,
        )
    )
    db.flush()

    # Resume the loop — the tool results will contain rejection messages
    system_prompt, anthropic_tools = build_tool_definitions(
        thread.domain, db, user_id=user.id
    )
    return resume_tool_loop(thread, db, system_prompt, anthropic_tools)


# ---------------------------------------------------------------------------
# Widget-initiated tool actions
# ---------------------------------------------------------------------------


class WidgetActionRequest(BaseModel):
    connector_name: str
    action: str
    params: dict = {}


@router.post("/threads/{thread_id}/widget-action")
async def widget_action(
    thread_id: str,
    body: WidgetActionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Execute a tool call initiated from an interactive widget."""
    from app.agents.tool_loop import _execute_tool_call, _find_tool_def
    import uuid

    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    # Reject if there are already pending tool calls
    if thread.status == "awaiting_tool_approval":
        raise HTTPException(
            status_code=409,
            detail="Thread already has pending tool calls awaiting approval",
        )

    # Look up the tool definition
    tool_def = _find_tool_def(body.connector_name, body.action, db)
    if not tool_def:
        raise HTTPException(
            status_code=404,
            detail=f"Tool not found: {body.action} on {body.connector_name}",
        )

    method = tool_def.get("method", "POST").upper()
    is_read_only = method == "GET"

    # Create a ToolCall record
    tc = ToolCall(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        iteration=0,
        tool_name=f"{body.connector_name}__{body.action}",
        connector_name=body.connector_name,
        action=body.action,
        method=method,
        input_params=body.params,
        status="executed" if is_read_only else "pending_approval",
    )
    db.add(tc)
    db.flush()

    if is_read_only:
        # Auto-execute read-only tool
        result = _execute_tool_call(tc, db)
        db.commit()
        return {"status": "executed", "data": result}
    else:
        # Queue for approval
        thread.pending_tool_call_ids = [tc.id]
        thread.status = "awaiting_tool_approval"
        db.commit()

        return {
            "status": "pending_approval",
            "tool_call_id": tc.id,
            "action": body.action,
            "connector_name": body.connector_name,
            "params": body.params,
        }
