"""Supervisor / Orchestrator

Routes user messages to domain-specialist agents. The supervisor:
1. Routes to an existing thread's agent when thread_id is provided
2. Classifies new messages via the LLM router
3. Delegates to the appropriate domain agent
4. Falls back to clarification for unknown domains
"""

import logging

from sqlalchemy.orm import Session

from app.db.models import Thread, Message, LlmCall
from app.agents.registry import get_agent, registered_domains
from app.agents.router import classify

logger = logging.getLogger(__name__)


def handle_message(
    message: str,
    db: Session,
    config_db: Session | None = None,
    user_id: str | None = None,
    thread_id: str | None = None,
    venue_id: str | None = None,
    page_context: dict | None = None,
) -> dict:
    """Process a user message through routing then agent delegation."""
    _cdb = config_db
    if _cdb is None:
        raise RuntimeError(
            "config_db is required — check that config_db is passed through the call chain"
        )

    # Quota gate — block before any LLM call if tokens exhausted
    from app.services.billing_service import check_quota_for_user

    check_quota_for_user(db, user_id)

    # Track whether we're continuing a meta/unknown thread
    prior_thread = None
    venue_name = None
    venue_timezone = None

    # 1. If a specific thread_id is provided, re-route through classifier
    if thread_id:
        thread = db.query(Thread).filter(Thread.id == thread_id).first()
        if thread:
            # Load venue from existing thread for follow-ups
            venue_id = thread.venue_id
            venue_name = None
            venue_timezone = None
            if venue_id:
                from app.db.models import Venue

                venue_obj = db.query(Venue).filter(Venue.id == venue_id).first()
                if venue_obj:
                    venue_name = venue_obj.name
                    venue_timezone = venue_obj.timezone

            # Detect automated task "Run Now" — skip playbook routing
            if thread.intent and thread.intent.endswith(".automated_conversation"):
                from app.db.models import AutomatedTask

                at = (
                    db.query(AutomatedTask)
                    .filter(AutomatedTask.conversation_thread_id == thread_id)
                    .first()
                )
                if at and message.strip() == at.prompt.strip():
                    logger.info(
                        "Automated task Run Now detected (task=%s), bypassing router",
                        at.id[:12],
                    )
                    agent = get_agent(thread.domain)
                    if agent:
                        system_prompt, anthropic_tools = agent.get_tool_definitions(
                            db,
                            user_id=user_id,
                            active_venue_name=venue_name,
                            venue_timezone=venue_timezone,
                            config_db=_cdb,
                        )
                        if at.tool_filter:
                            allowed = set(at.tool_filter)
                            anthropic_tools = [
                                t
                                for t in anthropic_tools
                                if t["name"].split("__", 1)[-1] in allowed
                                or t["name"] in allowed
                            ]
                            logger.info(
                                "Task tool_filter applied: %d tools",
                                len(anthropic_tools),
                            )
                        from app.agents.tool_loop import run_tool_loop

                        return run_tool_loop(
                            message,
                            thread,
                            db,
                            system_prompt,
                            anthropic_tools,
                            config_db=_cdb,
                        )

            # Classify the follow-up to decide how to handle it
            from app.agents.router import classify_followup
            from app.db.config_models import Playbook

            # Build a brief summary of recent conversation
            recent_msgs = (
                db.query(Message)
                .filter(Message.thread_id == thread_id)
                .order_by(Message.created_at.desc())
                .limit(4)
                .all()
            )
            summary_parts = []
            for m in reversed(recent_msgs):
                role = "User" if m.role == "user" else "Agent"
                summary_parts.append(f"{role}: {m.content[:100]}")
            recent_summary = "\n".join(summary_parts) if summary_parts else "New thread"

            followup = classify_followup(
                message,
                thread.domain,
                None,  # no "current" playbook — each message gets its own
                recent_summary,
                thread_id=thread_id,
                playbook_tools=None,
                db=db,
                config_db=_cdb,
            )

            action = followup.get("action", "continue")
            logger.info(
                "Follow-up routing: action=%s domain=%s playbook=%s reason=%s",
                action,
                followup.get("domain"),
                followup.get("playbook"),
                followup.get("reason", ""),
            )

            if action == "new_thread":
                # User switched topics — fall through to normal routing below
                thread_id = None
                prior_thread = thread
            else:
                # Load playbook for THIS message if the classifier matched one
                message_playbook = None
                playbook_slug = followup.get("playbook")
                logger.info("Follow-up playbook slug from classifier: %s", playbook_slug)
                if playbook_slug:
                    bare_slug = playbook_slug.split("/")[-1] if "/" in playbook_slug else playbook_slug
                    message_playbook = (
                        _cdb.query(Playbook)
                        .filter(Playbook.slug == bare_slug, Playbook.enabled == True)  # noqa: E712
                        .first()
                    )
                    logger.info(
                        "Playbook lookup: slug=%s found=%s name=%s",
                        bare_slug,
                        message_playbook is not None,
                        message_playbook.display_name if message_playbook else "N/A",
                    )

                logger.info(
                    "Calling agent.handle_message with playbook=%s",
                    message_playbook.display_name if message_playbook else "None",
                )
                agent = get_agent(thread.domain)
                if agent:
                    return agent.handle_message(
                        message,
                        db,
                        user_id,
                        thread_id,
                        venue_id=venue_id,
                        venue_name=venue_name,
                        venue_timezone=venue_timezone,
                        config_db=_cdb,
                        playbook=message_playbook,
                    )
            # Handle venue clarification follow-ups — resolve venue from reply
            # and re-route the original message
            if thread.intent == "venue_clarification":
                from app.services.venue_service import resolve_venue_id
                from app.db.models import Venue

                resolved_id = resolve_venue_id(message.strip(), db)
                if resolved_id:
                    venue_obj = db.query(Venue).filter(Venue.id == resolved_id).first()
                    # Add the user's venue reply to the conversation
                    db.add(Message(thread_id=thread.id, role="user", content=message))
                    db.flush()
                    # Re-route the original message with the resolved venue
                    original_message = thread.raw_prompt or message
                    prior_thread = thread
                    # Fall through to classification with venue set
                    venue_id = resolved_id
                    venue_name = venue_obj.name if venue_obj else message.strip()
                    venue_timezone = venue_obj.timezone if venue_obj else None
                    # Override message to the original request
                    message = original_message
                else:
                    # Couldn't resolve — ask again
                    from app.services.venue_service import get_user_venues

                    venues = get_user_venues(db)
                    venue_list = ", ".join(v.name for v in venues)
                    reply = f"I couldn't find a venue called '{message.strip()}'. Available venues: {venue_list}"
                    db.add(Message(thread_id=thread.id, role="user", content=message))
                    db.add(
                        Message(thread_id=thread.id, role="assistant", content=reply)
                    )
                    db.commit()
                    db.refresh(thread)
                    return {
                        "id": thread.id,
                        "domain": "unknown",
                        "intent": "venue_clarification",
                        "title": thread.title,
                        "message": message,
                        "status": "needs_clarification",
                        "created_at": thread.created_at.isoformat(),
                        "updated_at": thread.updated_at.isoformat(),
                        "conversation": [
                            {
                                "role": m.role,
                                "text": m.content,
                                "created_at": m.created_at.isoformat()
                                if m.created_at
                                else None,
                            }
                            for m in sorted(thread.messages, key=lambda x: x.created_at)
                        ],
                        "clarification_question": reply,
                    }

            # For meta/unknown threads: remember the old thread so we can
            # migrate its conversation into whatever thread comes next.
            elif thread.domain in ("meta", "unknown"):
                prior_thread = thread

    # 2. Classify the message to a domain
    from app.services.agent_config_service import get_all_capabilities_summary

    caps = get_all_capabilities_summary(_cdb)

    # Skip LLM routing when page_context tells us which agent to use
    if page_context and not thread_id:
        domain = page_context["agent"]
        routing = {"domain": domain, "title": None, "venue": None, "llm_call_id": None}
        logger.info("Skipped LLM routing — page_context directed to %s", domain)
    else:
        # Pass simple domain slugs — the router prompt has static capability
        # descriptions that use user-facing language rather than verbose
        # tool descriptions from the DB.
        domains = registered_domains()
        routing = classify(message, domains, db=db, config_db=_cdb)
        domain = routing["domain"]

    # Resolve venue (skip if already resolved from venue clarification follow-up)
    if not venue_id:
        from app.services.venue_service import get_user_venues, resolve_venue_id
        from app.db.models import Venue

        venues = get_user_venues(db)

        if len(venues) == 1:
            venue_id = venues[0].id
            venue_name = venues[0].name
            venue_timezone = venues[0].timezone
        elif len(venues) > 1:
            router_venue = routing.get("venue")
            if router_venue and router_venue != "all":
                resolved_id = resolve_venue_id(router_venue, db)
                if resolved_id:
                    venue_id = resolved_id
                    venue_name = router_venue
                    venue_obj = db.query(Venue).filter(Venue.id == resolved_id).first()
                    venue_timezone = venue_obj.timezone if venue_obj else None
            # "all" or no specific venue → agent gets all venues in its prompt
            # and can make tool calls per venue or ask for clarification itself

    # Emit routing event so the frontend knows which agent was selected
    from app.agents.tool_loop import _emit_event

    agent_display = caps.get(domain, {}).get("display_name", domain.title())
    _emit_event(
        {
            "type": "routing",
            "domain": domain,
            "title": routing.get("title"),
            "agent_label": agent_display,
        }
    )

    # Handle meta domain — self-description
    if domain == "meta":
        result = _build_capabilities_response(
            message, caps, db, user_id, prior_thread=prior_thread
        )
        # Back-fill thread_id on the routing LLM call
        if routing.get("llm_call_id") and result.get("id"):
            llm_call = (
                db.query(LlmCall).filter(LlmCall.id == routing["llm_call_id"]).first()
            )
            if llm_call:
                llm_call.thread_id = result["id"]
                db.commit()
                result["llm_calls"] = [_llm_call_to_dict(llm_call)]
        return result

    # Load playbook if the router matched one
    playbook = None
    playbook_slug = routing.get("playbook")
    if playbook_slug:
        from app.db.config_models import Playbook

        # Router may return "agent/slug" format — strip the prefix
        bare_slug = playbook_slug.split("/")[-1] if "/" in playbook_slug else playbook_slug

        playbook = (
            _cdb.query(Playbook)
            .filter(Playbook.slug == bare_slug, Playbook.enabled == True)  # noqa: E712
            .first()
        )

    # 3. Delegate to the domain agent
    agent = get_agent(domain)
    if agent:
        result = agent.handle_message(
            message,
            db,
            user_id,
            venue_id=venue_id,
            venue_name=venue_name,
            venue_timezone=venue_timezone,
            config_db=_cdb,
            page_context=page_context,
            playbook=playbook,
        )

        # Set the LLM-generated title on the thread + backfill routing LlmCall thread_id
        title = routing.get("title")
        llm_call_id = routing.get("llm_call_id")
        if result.get("id"):
            thread_obj = db.query(Thread).filter(Thread.id == result["id"]).first()
            if thread_obj:
                if title and not thread_obj.title:
                    thread_obj.title = title
                # Link the initial routing LlmCall to this thread
                if llm_call_id:
                    routing_call = db.query(LlmCall).filter(LlmCall.id == llm_call_id).first()
                    if routing_call and not routing_call.thread_id:
                        routing_call.thread_id = thread_obj.id
                db.flush()
            if title:
                result["title"] = title

        # Migrate prior meta/unknown conversation into the new thread
        if prior_thread and result.get("id"):
            _migrate_prior_thread(prior_thread, result["id"], db)
            # Re-read conversation so the response includes the full history
            new_thread = db.query(Thread).filter(Thread.id == result["id"]).first()
            if new_thread:
                result["conversation"] = [
                    {
                        "role": m.role,
                        "text": m.content,
                        "created_at": m.created_at.isoformat()
                        if m.created_at
                        else None,
                    }
                    for m in sorted(new_thread.messages, key=lambda x: x.created_at)
                ]

        # Back-fill thread_id on the routing LLM call and include it in the response
        if routing.get("llm_call_id") and result.get("id"):
            llm_call = (
                db.query(LlmCall).filter(LlmCall.id == routing["llm_call_id"]).first()
            )
            if llm_call:
                llm_call.thread_id = result["id"]
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


def _migrate_prior_thread(
    prior_thread: Thread, new_thread_id: str, db: Session
) -> None:
    """Move conversation & LLM calls from a meta/unknown thread into the new real thread, then delete the old one."""
    old_thread_id = prior_thread.id

    # Re-parent messages and LLM calls via bulk UPDATE (avoids SQLAlchemy
    # relationship cascade conflicts with the subsequent delete).
    db.query(Message).filter(Message.thread_id == old_thread_id).update(
        {Message.thread_id: new_thread_id}, synchronize_session="fetch"
    )
    db.query(LlmCall).filter(LlmCall.thread_id == old_thread_id).update(
        {LlmCall.thread_id: new_thread_id}, synchronize_session="fetch"
    )
    db.flush()

    # Now safe to delete the orphaned thread (no child rows remain)
    db.query(Thread).filter(Thread.id == old_thread_id).delete(
        synchronize_session="fetch"
    )
    db.commit()


def _build_capabilities_response(
    message: str,
    caps: dict,
    db: Session,
    user_id: str | None = None,
    prior_thread: Thread | None = None,
) -> dict:
    """Build a meta response listing all agent capabilities.

    If prior_thread is provided, continues that conversation instead of creating a new thread.
    """
    lines = ["Here's what I can help you with:\n"]
    for slug, info in caps.items():
        if slug == "router":
            continue
        display = info.get("display_name", slug.title())
        desc = info.get("description", "")
        line = f"**{display}** — {desc}" if desc else f"**{display}**"
        cap_labels = [
            c["label"] for c in info.get("capabilities", []) if c.get("enabled", True)
        ]
        if cap_labels:
            line += f" (can: {', '.join(cap_labels)})"
        lines.append(f"- {line}")
    lines.append("\nJust type what you need and I'll route it to the right agent.")
    answer = "\n".join(lines)

    if prior_thread:
        thread = prior_thread
        db.add(Message(thread_id=thread.id, role="user", content=message))
        db.add(Message(thread_id=thread.id, role="assistant", content=answer))
        db.commit()
        db.refresh(thread)
    else:
        thread = Thread(
            user_id=user_id,
            intent="meta.capabilities",
            domain="meta",
            status="completed",
            raw_prompt=message,
            extracted_fields={},
            missing_fields=[],
        )
        db.add(thread)
        db.flush()
        db.add(Message(thread_id=thread.id, role="user", content=message))
        db.add(Message(thread_id=thread.id, role="assistant", content=answer))
        db.commit()
        db.refresh(thread)

    return {
        "id": thread.id,
        "domain": "meta",
        "intent": "meta.capabilities",
        "title": thread.title,
        "message": message,
        "status": "completed",
        "created_at": thread.created_at.isoformat(),
        "updated_at": thread.updated_at.isoformat(),
        "conversation": [
            {
                "role": m.role,
                "text": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in sorted(thread.messages, key=lambda x: x.created_at)
        ],
    }


def _create_venue_clarification(
    message: str, venue_list: str, db: Session, user_id: str | None = None
) -> dict:
    """Ask the user to specify which venue before proceeding."""
    question = f"Which venue would you like me to check? Available venues: {venue_list}"

    thread = Thread(
        user_id=user_id,
        intent="venue_clarification",
        domain="unknown",
        status="needs_clarification",
        raw_prompt=message,
        extracted_fields={},
        missing_fields=["venue"],
        clarification_question=question,
    )
    db.add(thread)
    db.flush()

    db.add(Message(thread_id=thread.id, role="user", content=message))
    db.add(Message(thread_id=thread.id, role="assistant", content=question))
    db.commit()
    db.refresh(thread)

    return {
        "id": thread.id,
        "domain": "unknown",
        "intent": "venue_clarification",
        "title": thread.title,
        "message": message,
        "status": "needs_clarification",
        "created_at": thread.created_at.isoformat(),
        "updated_at": thread.updated_at.isoformat(),
        "conversation": [
            {"role": "user", "text": message},
            {"role": "assistant", "text": question},
        ],
        "clarification_question": question,
    }


def _create_unknown(message: str, db: Session, user_id: str | None = None) -> dict:
    """Handle unknown intent."""
    question = "I'm not sure what you need. Try asking me to order stock, set up a new employee, or generate a report."

    thread = Thread(
        user_id=user_id,
        intent="unknown",
        domain="unknown",
        status="needs_clarification",
        raw_prompt=message,
        extracted_fields={},
        missing_fields=[],
        clarification_question=question,
    )
    db.add(thread)
    db.flush()

    db.add(Message(thread_id=thread.id, role="user", content=message))
    db.add(Message(thread_id=thread.id, role="assistant", content=question))
    db.commit()
    db.refresh(thread)

    return {
        "id": thread.id,
        "domain": "unknown",
        "intent": "unknown",
        "title": thread.title,
        "message": message,
        "status": "needs_clarification",
        "created_at": thread.created_at.isoformat(),
        "updated_at": thread.updated_at.isoformat(),
        "conversation": [
            {"role": "user", "text": message},
            {"role": "assistant", "text": question},
        ],
        "clarification_question": question,
    }
