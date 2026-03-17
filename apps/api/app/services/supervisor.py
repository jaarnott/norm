"""Supervisor / Orchestrator

Routes user messages to domain-specialist agents. The supervisor:
1. Routes to an existing task's agent when task_id is provided
2. Classifies new messages via the LLM router
3. Delegates to the appropriate domain agent
4. Falls back to clarification for unknown domains
"""

import logging

from sqlalchemy.orm import Session

from app.db.models import Task, Message, LlmCall
from app.agents.registry import get_agent, registered_domains
from app.agents.router import classify

logger = logging.getLogger(__name__)


def handle_message(message: str, db: Session, user_id: str | None = None, task_id: str | None = None) -> dict:
    """Process a user message through routing then agent delegation."""

    # Track whether we're continuing a meta/unknown task
    prior_task = None

    # 1. If a specific task_id is provided, route to that task's domain agent
    if task_id:
        task = db.query(Task).filter(Task.id == task_id).first()
        if task:
            agent = get_agent(task.domain)
            if agent:
                return agent.handle_message(message, db, user_id, task_id)
            # For meta/unknown tasks: remember the old task so we can
            # migrate its conversation into whatever task comes next.
            if task.domain in ("meta", "unknown"):
                prior_task = task

    # 2. Classify the message to a domain (with rich capability descriptions)
    from app.services.agent_config_service import get_all_capabilities_summary

    caps = get_all_capabilities_summary(db)
    domains = registered_domains()
    domain_descs = []
    for slug in domains:
        info = caps.get(slug, {})
        desc = info.get("description", slug)
        actions = ", ".join(c["label"] for c in info.get("capabilities", []) if c.get("enabled", True))
        line = f"{slug}: {desc}" + (f" (can: {actions})" if actions else "")
        domain_descs.append(line)
    # Add meta domain — only for broad "what can you do" with no specific domain
    domain_descs.append("meta: ONLY when the user asks a general question about the whole system's capabilities without mentioning a specific domain (e.g. 'what can you do?', 'help'). If they mention a specific area like HR, procurement, or reports, route to that domain instead.")

    routing = classify(message, domain_descs, db=db)
    domain = routing["domain"]

    # Emit routing event so the frontend knows which agent was selected
    from app.agents.tool_loop import _emit_event
    agent_display = caps.get(domain, {}).get("display_name", domain.title())
    _emit_event({
        "type": "routing",
        "domain": domain,
        "title": routing.get("title"),
        "agent_label": agent_display,
    })

    # Handle meta domain — self-description
    if domain == "meta":
        result = _build_capabilities_response(message, caps, db, user_id, prior_task=prior_task)
        # Back-fill task_id on the routing LLM call
        if routing.get("llm_call_id") and result.get("id"):
            llm_call = db.query(LlmCall).filter(LlmCall.id == routing["llm_call_id"]).first()
            if llm_call:
                llm_call.task_id = result["id"]
                db.commit()
                result["llm_calls"] = [_llm_call_to_dict(llm_call)]
        return result

    # 3. Delegate to the domain agent
    agent = get_agent(domain)
    if agent:
        result = agent.handle_message(message, db, user_id)

        # Set the LLM-generated title on the task
        title = routing.get("title")
        if title and result.get("id"):
            task_obj = db.query(Task).filter(Task.id == result["id"]).first()
            if task_obj and not task_obj.title:
                task_obj.title = title
                db.flush()
            result["title"] = title

        # Migrate prior meta/unknown conversation into the new task
        if prior_task and result.get("id"):
            _migrate_prior_task(prior_task, result["id"], db)
            # Re-read conversation so the response includes the full history
            new_task = db.query(Task).filter(Task.id == result["id"]).first()
            if new_task:
                result["conversation"] = [
                    {"role": m.role, "text": m.content, "created_at": m.created_at.isoformat() if m.created_at else None}
                    for m in sorted(new_task.messages, key=lambda x: x.created_at)
                ]

        # Back-fill task_id on the routing LLM call and include it in the response
        if routing.get("llm_call_id") and result.get("id"):
            llm_call = db.query(LlmCall).filter(LlmCall.id == routing["llm_call_id"]).first()
            if llm_call:
                llm_call.task_id = result["id"]
                db.commit()

                routing_entry = _llm_call_to_dict(llm_call)
                if "llm_calls" in result:
                    result["llm_calls"].insert(0, routing_entry)
                else:
                    result["llm_calls"] = [routing_entry]

        return result

    # 4. Unknown domain — return clarification
    return _create_unknown(message, db, user_id)


def _llm_call_to_dict(llm_call: LlmCall) -> dict:
    return {
        "id": llm_call.id,
        "call_type": llm_call.call_type,
        "model": llm_call.model,
        "system_prompt": llm_call.system_prompt,
        "user_prompt": llm_call.user_prompt,
        "raw_response": llm_call.raw_response,
        "parsed_response": llm_call.parsed_response,
        "status": llm_call.status,
        "error_message": llm_call.error_message,
        "duration_ms": llm_call.duration_ms,
        "tools_provided": llm_call.tools_provided,
        "created_at": llm_call.created_at.isoformat() if llm_call.created_at else None,
    }


def _migrate_prior_task(prior_task: Task, new_task_id: str, db: Session) -> None:
    """Move conversation & LLM calls from a meta/unknown task into the new real task, then delete the old one."""
    old_task_id = prior_task.id

    # Re-parent messages and LLM calls via bulk UPDATE (avoids SQLAlchemy
    # relationship cascade conflicts with the subsequent delete).
    db.query(Message).filter(Message.task_id == old_task_id).update(
        {Message.task_id: new_task_id}, synchronize_session="fetch"
    )
    db.query(LlmCall).filter(LlmCall.task_id == old_task_id).update(
        {LlmCall.task_id: new_task_id}, synchronize_session="fetch"
    )
    db.flush()

    # Now safe to delete the orphaned task (no child rows remain)
    db.query(Task).filter(Task.id == old_task_id).delete(synchronize_session="fetch")
    db.commit()


def _build_capabilities_response(message: str, caps: dict, db: Session, user_id: str | None = None, prior_task: Task | None = None) -> dict:
    """Build a meta response listing all agent capabilities.

    If prior_task is provided, continues that conversation instead of creating a new task.
    """
    lines = ["Here's what I can help you with:\n"]
    for slug, info in caps.items():
        if slug == "router":
            continue
        display = info.get("display_name", slug.title())
        desc = info.get("description", "")
        line = f"**{display}** — {desc}" if desc else f"**{display}**"
        cap_labels = [c["label"] for c in info.get("capabilities", []) if c.get("enabled", True)]
        if cap_labels:
            line += f" (can: {', '.join(cap_labels)})"
        lines.append(f"- {line}")
    lines.append("\nJust type what you need and I'll route it to the right agent.")
    answer = "\n".join(lines)

    if prior_task:
        task = prior_task
        db.add(Message(task_id=task.id, role="user", content=message))
        db.add(Message(task_id=task.id, role="assistant", content=answer))
        db.commit()
        db.refresh(task)
    else:
        task = Task(
            user_id=user_id,
            intent="meta.capabilities",
            domain="meta",
            status="completed",
            raw_prompt=message,
            extracted_fields={},
            missing_fields=[],
        )
        db.add(task)
        db.flush()
        db.add(Message(task_id=task.id, role="user", content=message))
        db.add(Message(task_id=task.id, role="assistant", content=answer))
        db.commit()
        db.refresh(task)

    return {
        "id": task.id,
        "domain": "meta",
        "intent": "meta.capabilities",
        "title": task.title,
        "message": message,
        "status": "completed",
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "conversation": [
            {"role": m.role, "text": m.content, "created_at": m.created_at.isoformat() if m.created_at else None}
            for m in sorted(task.messages, key=lambda x: x.created_at)
        ],
    }


def _create_unknown(message: str, db: Session, user_id: str | None = None) -> dict:
    """Handle unknown intent."""
    question = "I'm not sure what you need. Try asking me to order stock, set up a new employee, or generate a report."

    task = Task(
        user_id=user_id,
        intent="unknown",
        domain="unknown",
        status="needs_clarification",
        raw_prompt=message,
        extracted_fields={},
        missing_fields=[],
        clarification_question=question,
    )
    db.add(task)
    db.flush()

    db.add(Message(task_id=task.id, role="user", content=message))
    db.add(Message(task_id=task.id, role="assistant", content=question))
    db.commit()
    db.refresh(task)

    return {
        "id": task.id,
        "domain": "unknown",
        "intent": "unknown",
        "title": task.title,
        "message": message,
        "status": "needs_clarification",
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "conversation": [
            {"role": "user", "text": message},
            {"role": "assistant", "text": question},
        ],
        "clarification_question": question,
    }
