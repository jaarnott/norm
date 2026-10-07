"""Unified thread lifecycle endpoints."""

import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db, get_config_db
from app.db.models import Thread, Approval, ToolCall, User
from app.auth.dependencies import get_current_user, require_permission
from app.services.order_service import (
    get_order,
    approve_order,
    reject_order,
    submit_order,
)
from app.services.hr_service import (
    get_hr_thread,
    approve_hr_thread,
    reject_hr_thread,
    submit_hr_thread,
)
from app.services.report_threads import _report_thread_to_dict

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_automated_task_meta(db: Session, conversation_thread_id: str) -> dict | None:
    """Return automated task metadata for a conversation thread, or None."""
    from app.db.models import AutomatedTask as AT
    from app.services import approvals

    at = (
        db.query(AT).filter(AT.conversation_thread_id == conversation_thread_id).first()
    )
    if not at:
        return None
    return {
        "id": at.id,
        "title": at.title,
        "description": at.description,
        "agent_slug": at.agent_slug,
        "schedule_type": at.schedule_type,
        "schedule_config": at.schedule_config or {},
        "status": at.status,
        "prompt": at.prompt,
        "task_config": at.task_config or {},
        "thread_summary": at.thread_summary,
        "tool_filter": at.tool_filter,
        "last_run_at": at.last_run_at.isoformat() if at.last_run_at else None,
        # Runs that stopped at a write which asks — the thread list and the
        # task header show it, since nobody was there when the run asked.
        "waiting_for_approval": len(approvals.waiting_runs(db, at.id)),
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
        return get_hr_thread(db, thread_id), "hr"
    if thread.domain == "time_attendance":
        return get_hr_thread(db, thread_id), "time_attendance"
    if thread.domain == "reports":
        return _report_thread_to_dict(thread), "reports"
    # Generic fallback for any other domain (meta, etc.)
    return _tool_use_thread_to_dict(thread), thread.domain or ""


def _tool_use_thread_to_dict(thread: Thread) -> dict:
    """Serialize a tool-loop thread to a generic response dict."""
    conversation = [
        {
            "role": m.role,
            "text": m.content,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "display_blocks": m.display_blocks,
            "attachments": m.attachments,
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
        # The conversation's notebook (memory tool files), for the card.
        "notebook": _notebook_files(thread),
    }


@router.get("/threads")
async def get_all_threads(
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("tasks:read")),
):
    """Return lightweight thread summaries for the sidebar list.

    Only includes fields needed by ThreadCard (id, domain, status, title, etc.).
    Full conversation/tool_calls/llm_calls are loaded on demand via GET /threads/{id}.
    """
    from sqlalchemy import or_

    threads = (
        db.query(Thread)
        .filter(
            Thread.user_id == user.id,
            # Exclude automated task execution runs — only show conversation threads
            or_(Thread.intent.is_(None), ~Thread.intent.like("%.automated_task")),
            # Exclude delegated sub-runs. A consulted agent gets its own thread to
            # keep its tool calls off the caller's, but the user asked one
            # question and should see one thread — the parent reports the answer.
            Thread.parent_thread_id.is_(None),
        )
        .order_by(Thread.created_at.desc())
        .all()
    )
    apps = _apps_by_thread([t.id for t in threads], db, config_db)
    return {"threads": [_thread_summary(t, db, apps.get(t.id)) for t in threads]}


def _apps_by_thread(
    thread_ids: list[str], db: Session, config_db: Session
) -> dict[str, list[dict]]:
    """thread id -> the Apps whose tools the thread used, in first-use order.

    This is how a thread is labelled and filed now that there is one agent:
    by what it actually touched (BambooHR, Loaded Reports), not by which of
    seven agents a router guessed. Each App carries its team member, so the
    sidebar can file the thread under every member whose App it used. Norm
    Core (email, charts, memory) labels nothing — every thread may use it."""
    if not thread_ids:
        return {}
    from app.services.entitlements import (
        ALL_MEMBERS,
        _catalog,
        app_member,
        tool_owners,
    )

    owners = tool_owners(config_db)
    if not owners:
        return {}
    catalog = {a.slug: a for a in _catalog(config_db)}
    agent_rows = [a for a in catalog.values() if a.tier == "agent"]
    rows = (
        db.query(ToolCall.thread_id, ToolCall.connector_name, ToolCall.action)
        .filter(ToolCall.thread_id.in_(thread_ids))
        .order_by(ToolCall.created_at)
        .all()
    )
    out: dict[str, list[dict]] = {}
    for thread_id, connector, action in rows:
        app = catalog.get(owners.get(f"{connector}.{action}") or "")
        if app is None:
            continue
        member = app_member(app, agent_rows)
        if member == ALL_MEMBERS:
            continue
        used = out.setdefault(thread_id, [])
        if all(u["slug"] != app.slug for u in used):
            used.append({"slug": app.slug, "name": app.name, "member": member})
    return out


def _thread_summary(
    thread: Thread, db: Session | None = None, apps: list[dict] | None = None
) -> dict:
    """Lightweight serialisation — no relationships loaded. ``apps``: the Apps
    the thread used (``_apps_by_thread``)."""
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
        "apps": apps or [],
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
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("tasks:read")),
):
    from app.services.thread_access import thread_for

    # Only the owner, a platform admin, or (for a scheduled task's threads)
    # the task creator's organisation may open a conversation.
    if thread_for(db, thread_id, user) is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    thread, _ = _find(db, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    thread["apps"] = _apps_by_thread([thread_id], db, config_db).get(thread_id, [])
    return thread


def _thread_for_decision(db: Session, thread_id: str, user: User) -> Thread:
    """The thread ``user`` may decide on (theirs, or any for a platform admin).

    A tool-loop thread that is no longer waiting answers 409, so a second click
    can't fall through to the legacy domain flows below.
    """
    from app.services import approvals

    thread = approvals.actionable_thread(db, thread_id, user)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    if approvals.is_tool_loop_thread(thread) and thread.status != approvals.AWAITING:
        raise HTTPException(
            status_code=409, detail="These changes have already been decided."
        )
    return thread


@router.post("/threads/{thread_id}/approve")
async def approve(
    thread_id: str,
    body: dict | None = None,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Approve the proposed changes — all of them, or the ones ticked.

    ``tool_call_ids`` names the rows the person ticked on the card; the rest
    are declined, and the model is told which. ``always_allow`` names tools on
    the card the person ticked "always allow" for — saved as THEIR preference.
    """
    raw_thread = _thread_for_decision(db, thread_id, user)
    if raw_thread.status == "awaiting_tool_approval":
        body = body if isinstance(body, dict) else {}
        ticked = body.get("tool_call_ids")
        always = body.get("always_allow") or []
        for ids in (ticked, always):
            if ids is not None and not (
                isinstance(ids, list) and all(isinstance(i, str) for i in ids)
            ):
                raise HTTPException(
                    status_code=422,
                    detail="tool_call_ids and always_allow must be lists of strings",
                )
        return _approve_tool_calls(
            db,
            raw_thread,
            user,
            config_db=config_db,
            tool_call_ids=ticked,
            always_allow=always,
        )

    thread, domain = _find(db, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    if domain == "procurement":
        return approve_order(db, thread_id, user=user)
    if domain == "hr":
        return approve_hr_thread(db, thread_id, user=user)
    if domain == "reports":
        return _approve_report(db, thread_id, user=user)
    raise HTTPException(status_code=400, detail="Unsupported domain")


def _rejection_org_id(db, user) -> str | None:
    from app.db.models import OrganizationMembership

    if not user:
        return None
    membership = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user.id)
        .first()
    )
    return membership.organization_id if membership else None


@router.post("/threads/{thread_id}/reject")
async def reject(
    thread_id: str,
    body: dict | None = None,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Reject a proposed action, optionally saying why.

    ``notes`` is what makes a rejection informative. Without it the record says
    only "no", which teaches nothing — and until this was added, no call site
    ever populated Approval.notes, so the column had been write-only-in-theory
    since it was created. A supplied reason is banked as a learning signal.
    """
    raw_thread = _thread_for_decision(db, thread_id, user)
    notes = (body or {}).get("notes") if isinstance(body, dict) else None
    if notes:
        from app.services.memory_signals import record_rejection

        record_rejection(
            db,
            organization_id=_rejection_org_id(db, user),
            user_id=getattr(user, "id", None),
            thread_id=raw_thread.id,
            notes=notes,
        )

    if raw_thread.status == "awaiting_tool_approval":
        return _reject_tool_calls(
            db, raw_thread, user, config_db=config_db, notes=notes
        )

    thread, domain = _find(db, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    if domain == "procurement":
        return reject_order(db, thread_id, user=user)
    if domain == "hr":
        return reject_hr_thread(db, thread_id, user=user)
    if domain == "reports":
        return _reject_report(db, thread_id, user=user)
    raise HTTPException(status_code=400, detail="Unsupported domain")


@router.post("/threads/{thread_id}/submit")
async def submit(
    thread_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services import approvals

    if approvals.actionable_thread(db, thread_id, user) is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    thread, domain = _find(db, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    if domain == "procurement":
        result = submit_order(db, thread_id)
    elif domain == "hr":
        result = submit_hr_thread(db, thread_id)
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
    return _report_thread_to_dict(thread)


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
    return _report_thread_to_dict(thread)


def _decide_tool_calls(
    db: Session,
    thread: Thread,
    user: User,
    approve: bool,
    config_db: Session | None = None,
    notes: str | None = None,
    tool_call_ids: list[str] | None = None,
    always_allow: list[str] | None = None,
) -> dict:
    """Decide a suspended turn's pending writes, then resume the loop.

    Claimed first (exactly once — a second click gets 409), decided per call
    (approved, or declined for a venue the approver can't act on), then resumed:
    approved writes run, and the model is told about the rest.
    """
    from app.agents.prompt_builder import build_tool_definitions
    from app.agents.tool_loop import resume_tool_loop
    from app.services import approvals

    try:
        approvals.claim(db, thread)
    except approvals.AlreadyDecided:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="These changes have already been decided."
        )
    if approve and always_allow and config_db is not None:
        approvals.always_allow_from_card(db, config_db, thread, user, always_allow)
    approvals.decide(
        db, thread, user, approve=approve, notes=notes, tool_call_ids=tool_call_ids
    )

    system_prompt, anthropic_tools = build_tool_definitions(
        _suspended_domain(thread), db, user_id=user.id, config_db=config_db
    )
    thread_id = thread.id
    try:
        result = resume_tool_loop(
            thread, db, system_prompt, anthropic_tools, config_db=config_db
        )
    except Exception:  # noqa: BLE001 — the decision must land where the person can see it
        logger.exception("approval_resume_failed", extra={"thread_id": thread_id})
        ran = approvals.executed_before_failure(db, thread)
        db.rollback()
        return approvals.land_failed_resume(thread_id, approve=approve, ran=ran)
    approvals.post_outcome_to_task_conversation(db, thread)
    # A scheduled run that was waiting on this decision is now settled
    # (success or declined) — unless it stopped at another card.
    approvals.settle_task_run(db, thread)
    db.commit()
    return result


def _approve_tool_calls(
    db: Session,
    thread: Thread,
    user: User,
    config_db: Session | None = None,
    tool_call_ids: list[str] | None = None,
    always_allow: list[str] | None = None,
) -> dict:
    """Approve pending write tool calls (all, or the ticked ones) and resume."""
    return _decide_tool_calls(
        db,
        thread,
        user,
        approve=True,
        config_db=config_db,
        tool_call_ids=tool_call_ids,
        always_allow=always_allow,
    )


def _suspended_domain(thread: Thread) -> str:
    """The agent that suspended this loop, not whoever owns the thread now.

    A follow-up can hand a conversation to a different agent while an approval
    is still waiting. Rebuilding tools from the thread's current domain would
    resume a half-finished transcript against the wrong agent's prompt and tool
    list, and fire the pending write with tools it was never planned against.
    """
    return (thread.agent_loop_state or {}).get("domain") or thread.domain


def _reject_tool_calls(
    db: Session,
    thread: Thread,
    user: User,
    config_db: Session | None = None,
    notes: str | None = None,
) -> dict:
    """Reject pending write tool calls and resume the loop (tool results will say 'rejected')."""
    return _decide_tool_calls(
        db, thread, user, approve=False, config_db=config_db, notes=notes
    )


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
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Execute a tool call initiated from an interactive widget."""
    from app.agents.tool_loop import _execute_tool_call, _find_tool_def
    from app.services import approvals
    import uuid

    thread = approvals.actionable_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    # Reject if there are already pending tool calls
    if thread.status == "awaiting_tool_approval":
        raise HTTPException(
            status_code=409,
            detail="Thread already has pending tool calls awaiting approval",
        )

    # Look up the tool definition
    tool_def = _find_tool_def(body.connector_name, body.action, db, config_db=config_db)
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
        result = _execute_tool_call(tc, db, config_db=config_db)
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


def _notebook_files(thread: Thread) -> list[dict]:
    """The thread's memory-tool files. Reads through the thread's own session
    so the serialiser's signature stays as every caller has it."""
    from sqlalchemy.orm import object_session

    from app.agents.memory_tool import notebook

    db = object_session(thread)
    return notebook(db, thread.id) if db is not None else []
