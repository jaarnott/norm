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
    original_user_text = None
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

            # An automated task's conversation is ABOUT that task. Resolve it up
            # front so every message in the thread keeps that identity — without
            # it the agent has no task id, so "add my email to this" reaches for
            # create_automated_task and silently leaves a duplicate draft while
            # the real task is unchanged.
            automated_task_ctx = None
            if thread.intent and thread.intent.endswith(".automated_conversation"):
                from app.db.models import AutomatedTask

                at = (
                    db.query(AutomatedTask)
                    .filter(AutomatedTask.conversation_thread_id == thread_id)
                    .first()
                )
                if at:
                    schedule = at.schedule_type or "manual"
                    cfg = at.schedule_config or {}
                    if cfg.get("hour") is not None:
                        schedule += f" at {int(cfg['hour']):02d}:{int(cfg.get('minute') or 0):02d}"
                    automated_task_ctx = {
                        "id": at.id,
                        "title": at.title,
                        "prompt": at.prompt,
                        "status": at.status,
                        "schedule": schedule,
                    }
                if at and " ".join(message.split()) == " ".join(
                    (at.prompt or "").split()
                ):
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
                            tool_filter=at.tool_filter,
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

            # A thread that is waiting on a venue answer: the very next message
            # IS that answer, so resolve it here — before the follow-up
            # classifier. The classifier reads a bare venue name as a topic
            # change, and once it has, `message` is rewritten into a
            # "[Prior conversation] ..." blob that no longer means what the
            # user typed.
            if thread.intent == "venue_clarification":
                unresolved = _resume_venue_clarification(message, thread, db)
                if unresolved is not None:
                    return unresolved

                # Venue recorded on the thread — resume the original request
                # in this same thread, using the routing the router already
                # worked out before it asked.
                message = thread.raw_prompt or message
                venue_id = thread.venue_id
                venue_name = None
                venue_timezone = None
                if venue_id:
                    from app.db.models import Venue

                    venue_obj = db.query(Venue).filter(Venue.id == venue_id).first()
                    if venue_obj:
                        venue_name = venue_obj.name
                        venue_timezone = venue_obj.timezone

                agent = get_agent(thread.domain)
                if agent:
                    from app.agents.tool_loop import run_tool_loop, _emit_event

                    system_prompt, anthropic_tools = agent.get_tool_definitions(
                        db,
                        user_id=user_id,
                        active_venue_name=venue_name,
                        venue_timezone=venue_timezone,
                        config_db=_cdb,
                        playbook=_stored_playbook(thread, _cdb),
                    )
                    # Same id as the thread the user is already looking at —
                    # the frontend needs it to stay put, not swap threads.
                    _emit_event({"type": "thread_created", "thread_id": thread.id})
                    return run_tool_loop(
                        message,
                        thread,
                        db,
                        system_prompt,
                        anthropic_tools,
                        context=agent.build_context(db, user_id),
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

            if action == "new_thread" and automated_task_ctx:
                # Never spin a new thread out of an automated task's own
                # conversation. Doing so abandons the task the user is looking
                # at — the thread "forgets" which task it belongs to and the
                # next request lands somewhere they cannot see.
                logger.info(
                    "Follow-up wanted a new thread, but this is automated task "
                    "%s's conversation — staying put",
                    automated_task_ctx["id"][:12],
                )
                action = "continue"

            if action == "new_thread" and (
                thread.status == "awaiting_tool_approval" or thread.agent_loop_state
            ):
                # A suspended tool loop belongs to the agent that suspended it,
                # and its state lives in columns on this thread. Moving on would
                # send the thread down the migrate-and-delete path and take the
                # pending approval with it — the approval card would point at a
                # thread that no longer exists, leaving the write neither
                # approvable nor rejectable. Answer here instead.
                logger.info(
                    "Follow-up wanted a new thread, but thread %s has a tool "
                    "approval pending — staying put",
                    thread.id[:12],
                )
                action = "continue"

            if action == "new_thread":
                rebound = _rebind_thread_agent(
                    followup.get("domain"), thread, message, db, _cdb, user_id
                )
                if rebound is not None:
                    return rebound

                # Not safe to rebind — fall through to normal routing below,
                # which creates a fresh thread and migrates this one into it.
                # Prepend conversation context so the full classifier can
                # still infer venue, names, etc. from the prior exchange.
                thread_id = None
                prior_thread = thread
                if recent_summary:
                    # Keep what the user actually typed. The blob is scaffolding
                    # for the router; storing it verbatim leaves the user
                    # staring at a transcript of themselves where their question
                    # should be, and it is redundant once the prior
                    # conversation is migrated into the new thread below.
                    original_user_text = message
                    message = f"[Prior conversation]\n{recent_summary}\n\n[New request]\n{message}"
            else:
                # Load playbook for THIS message if the classifier matched one
                message_playbook = None
                playbook_slug = followup.get("playbook")
                logger.info(
                    "Follow-up playbook slug from classifier: %s", playbook_slug
                )
                if playbook_slug:
                    bare_slug = (
                        playbook_slug.split("/")[-1]
                        if "/" in playbook_slug
                        else playbook_slug
                    )
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
                        automated_task=automated_task_ctx,
                    )
            # For meta/unknown threads: remember the old thread so we can
            # migrate its conversation into whatever thread comes next.
            if thread.domain in ("meta", "unknown"):
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

            if router_venue == "all":
                # Cross-venue query — agent handles multiple venues
                pass
            elif router_venue == "unclear":
                # Needs a venue but user didn't specify — show venue picker
                return _create_venue_clarification(
                    message, venues, domain, routing, db, user_id
                )
            elif router_venue:
                # Router extracted specific venue(s) — resolve
                resolved_id = resolve_venue_id(router_venue, db)
                if resolved_id:
                    venue_id = resolved_id
                    venue_obj = db.query(Venue).filter(Venue.id == resolved_id).first()
                    venue_name = venue_obj.name if venue_obj else router_venue
                    venue_timezone = venue_obj.timezone if venue_obj else None
                else:
                    # Router picked a name that doesn't resolve — clarify
                    return _create_venue_clarification(
                        message, venues, domain, routing, db, user_id
                    )
            # else: no venue field in router response — request doesn't need one

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
        bare_slug = (
            playbook_slug.split("/")[-1] if "/" in playbook_slug else playbook_slug
        )

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
                    routing_call = (
                        db.query(LlmCall).filter(LlmCall.id == llm_call_id).first()
                    )
                    if routing_call and not routing_call.thread_id:
                        routing_call.thread_id = thread_obj.id
                db.flush()
            if title:
                result["title"] = title

        # Migrate prior meta/unknown conversation into the new thread
        if prior_thread and result.get("id"):
            _restore_user_text(result["id"], message, original_user_text, db)
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
    return _create_unknown(message, db, user_id, routing=routing)


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
        "input_tokens": llm_call.input_tokens,
        "output_tokens": llm_call.output_tokens,
        "tools_provided": llm_call.tools_provided,
        "created_at": llm_call.created_at.isoformat() if llm_call.created_at else None,
    }


def _stored_playbook(thread: Thread, config_db: Session):
    """The playbook the router picked before it stopped to ask for a venue.

    `_create_venue_clarification` stashes the whole routing result on the
    thread so the original request can be resumed without re-classifying it.
    """
    from app.db.config_models import Playbook

    slug = ((thread.extracted_fields or {}).get("routing") or {}).get("playbook")
    if not slug:
        return None
    bare_slug = slug.split("/")[-1] if "/" in slug else slug
    return (
        config_db.query(Playbook)
        .filter(Playbook.slug == bare_slug, Playbook.enabled == True)  # noqa: E712
        .first()
    )


def _rebind_thread_agent(
    target_domain: str | None,
    thread: Thread,
    message: str,
    db: Session,
    config_db: Session,
    user_id: str | None,
) -> dict | None:
    """Hand this conversation to a different agent without splitting it.

    `classify_followup`'s "new_thread" means a *domain switch* — the request
    belongs to another agent — and the old answer to that was to abandon the
    thread, build a new one, migrate the conversation across and delete the
    original. `Thread.domain` is just a column: set it and carry on, so the
    user keeps one conversation and the new agent gets the real history
    instead of a 4-message, 100-chars-each summary blob.

    Returns the agent's response, or None when rebinding is not safe and the
    caller should fall back to spawning a new thread.
    """
    if not target_domain or target_domain == thread.domain:
        return None

    # Only ordinary tool-loop conversations. Every other kind of thread carries
    # an identity in its intent that a rebind would erase: `.mcp_playbook` runs,
    # `.automated_conversation` threads (four call sites key off that suffix to
    # find the task they belong to), and the legacy structured order/HR threads
    # whose serialisation in routers/threads.py is keyed on domain.
    if not (thread.intent or "").endswith(".tool_use"):
        return None

    agent = get_agent(target_domain)
    if agent is None:
        return None

    venue_name = None
    venue_timezone = None
    if thread.venue_id:
        from app.db.models import Venue

        venue_obj = db.query(Venue).filter(Venue.id == thread.venue_id).first()
        if venue_obj:
            venue_name = venue_obj.name
            venue_timezone = venue_obj.timezone

    system_prompt, anthropic_tools = agent.get_tool_definitions(
        db,
        user_id=user_id,
        active_venue_name=venue_name,
        venue_timezone=venue_timezone,
        config_db=config_db,
    )
    # An agent with no bound tools does not answer in the thread it was given —
    # it builds and commits one of its own (see marketing/time_attendance
    # agent.py). Rebinding into that would relabel this thread and then reply
    # somewhere else entirely, so leave it to the normal path.
    if not anthropic_tools:
        return None

    from app.agents.tool_loop import _emit_event, run_tool_loop

    previous_domain = thread.domain
    thread.domain = target_domain
    thread.intent = f"{target_domain}.tool_use"
    db.flush()
    logger.info(
        "Thread %s handed from %s to %s in place",
        thread.id[:12],
        previous_domain,
        target_domain,
    )

    # Tell the frontend who is answering now. It rewrites the thread's agent
    # in place from this event; without it the old agent's label sits there
    # for the whole turn.
    from app.services.agent_config_service import get_all_capabilities_summary

    caps = get_all_capabilities_summary(config_db)
    _emit_event(
        {
            "type": "routing",
            "domain": target_domain,
            "title": thread.title,
            "agent_label": caps.get(target_domain, {}).get(
                "display_name", target_domain.title()
            ),
        }
    )
    _emit_event({"type": "thread_created", "thread_id": thread.id})

    db.add(Message(thread_id=thread.id, role="user", content=message))
    db.flush()

    return run_tool_loop(
        message,
        thread,
        db,
        system_prompt,
        anthropic_tools,
        context=agent.build_context(db, user_id),
        config_db=config_db,
    )


def _resume_venue_clarification(
    message: str, thread: Thread, db: Session
) -> dict | None:
    """Answer a pending venue question on the thread that asked it.

    Returns a response dict when the reply names no venue we know (we ask
    again), or None once the venue is recorded on the thread and the caller
    should resume the original request.
    """
    from app.services.venue_service import get_user_venues, resolve_venue_id

    reply = message.strip()
    db.add(Message(thread_id=thread.id, role="user", content=message))
    db.flush()

    resolved_id = None
    if reply.lower() not in ("all", "all venues"):
        resolved_id = resolve_venue_id(reply, db)
        if not resolved_id:
            venues = get_user_venues(db)
            venue_list = ", ".join(v.name for v in venues)
            question = (
                f"I couldn't find a venue called '{reply}'. "
                f"Available venues: {venue_list}"
            )
            db.add(Message(thread_id=thread.id, role="assistant", content=question))
            thread.clarification_question = question
            db.commit()
            db.refresh(thread)
            return {
                "id": thread.id,
                "domain": thread.domain,
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
                "clarification_question": question,
            }

    # Resolved (or "all venues"). Record the venue and stand the thread down
    # from clarification — without this the thread stays armed forever and
    # every later message gets read as another venue reply.
    thread.venue_id = resolved_id
    thread.intent = f"{thread.domain}.tool_use"
    thread.status = "in_progress"
    thread.missing_fields = []
    thread.clarification_question = None
    db.commit()
    return None


def _restore_user_text(
    thread_id: str, stored_text: str, real_text: str | None, db: Session
) -> None:
    """Put the user's own words back where the router's context blob was stored.

    On a topic change the message handed to the router is the user's request
    wrapped in a "[Prior conversation] ..." summary. The agent persists
    whatever it was given, so the thread ends up showing that blob instead of
    the question the user asked — which reads as their message having been
    replaced. The blob has done its job by now, and the prior conversation is
    about to be migrated into this thread anyway.
    """
    if not real_text or real_text == stored_text:
        return

    msg = (
        db.query(Message)
        .filter(
            Message.thread_id == thread_id,
            Message.role == "user",
            Message.content == stored_text,
        )
        .order_by(Message.created_at.desc())
        .first()
    )
    if msg:
        msg.content = real_text

    thread = db.query(Thread).filter(Thread.id == thread_id).first()
    if thread and thread.raw_prompt == stored_text:
        thread.raw_prompt = real_text
    db.flush()


def _migrate_prior_thread(
    prior_thread: Thread, new_thread_id: str, db: Session
) -> None:
    """Move a prior thread's conversation into the new thread, then retire it."""
    from sqlalchemy.exc import SQLAlchemyError

    from app.db.models import ToolCall

    old_thread_id = prior_thread.id
    if old_thread_id == new_thread_id:
        return

    # Re-parent conversation rows via bulk UPDATE (avoids SQLAlchemy
    # relationship cascade conflicts with the subsequent delete). tool_calls
    # matters as much as messages: a thread that ran any tool has rows here,
    # and they hold the display blocks the conversation renders from.
    for model, column in (
        (Message, Message.thread_id),
        (LlmCall, LlmCall.thread_id),
        (ToolCall, ToolCall.thread_id),
    ):
        db.query(model).filter(column == old_thread_id).update(
            {column: new_thread_id}, synchronize_session="fetch"
        )
    db.flush()

    # Retire the emptied thread. Twelve other tables carry an FK to threads.id
    # and none of them are re-parented above, so this delete can legitimately
    # fail — do it inside a SAVEPOINT and keep the thread if it does. Tidying
    # up must never cost the user the answer they just waited for.
    try:
        with db.begin_nested():
            db.query(Thread).filter(Thread.id == old_thread_id).delete(
                synchronize_session="fetch"
            )
    except SQLAlchemyError as exc:
        logger.warning(
            "Kept thread %s after migrating it into %s — still referenced: %s",
            old_thread_id[:12],
            new_thread_id[:12],
            exc,
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
    message: str,
    venues: list,
    domain: str,
    routing: dict,
    db: Session,
    user_id: str | None = None,
) -> dict:
    """Ask the user to specify which venue before proceeding.

    Shows a venue picker component with clickable buttons. Stores the
    routing result so the original request can be resumed after selection.
    """
    question = (
        routing.get("venue_question")
        or "Sure! Which venue would you like me to look at?"
    )

    # Store routing info so we can resume after venue selection
    extracted = {"routing": {k: v for k, v in routing.items() if k != "llm_call_id"}}

    thread = Thread(
        user_id=user_id,
        intent="venue_clarification",
        domain=domain,
        status="needs_clarification",
        raw_prompt=message,
        extracted_fields=extracted,
        missing_fields=["venue"],
        clarification_question=question,
    )
    db.add(thread)
    db.flush()

    venue_data = [{"id": v.id, "name": v.name} for v in venues]
    display_blocks = [{"component": "venue_picker", "data": {"venues": venue_data}}]

    db.add(Message(thread_id=thread.id, role="user", content=message))
    db.add(
        Message(
            thread_id=thread.id,
            role="assistant",
            content=question,
            display_blocks=display_blocks,
        )
    )
    # Backfill routing LLM call onto the new thread
    llm_calls_list = []
    if routing.get("llm_call_id"):
        routing_call = (
            db.query(LlmCall).filter(LlmCall.id == routing["llm_call_id"]).first()
        )
        if routing_call:
            routing_call.thread_id = thread.id
            db.flush()
            llm_calls_list.append(_llm_call_to_dict(routing_call))

    db.commit()
    db.refresh(thread)

    return {
        "id": thread.id,
        "domain": domain,
        "intent": "venue_clarification",
        "title": routing.get("title") or thread.title,
        "message": message,
        "status": "needs_clarification",
        "created_at": thread.created_at.isoformat(),
        "updated_at": thread.updated_at.isoformat(),
        "conversation": [
            {"role": "user", "text": message, "created_at": None},
            {
                "role": "assistant",
                "text": question,
                "display_blocks": display_blocks,
                "created_at": None,
            },
        ],
        "clarification_question": question,
        "llm_calls": llm_calls_list,
    }


def _create_unknown(
    message: str, db: Session, user_id: str | None = None, routing: dict | None = None
) -> dict:
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

    # Backfill routing LLM call onto the new thread
    llm_calls_list = []
    if routing and routing.get("llm_call_id"):
        routing_call = (
            db.query(LlmCall).filter(LlmCall.id == routing["llm_call_id"]).first()
        )
        if routing_call:
            routing_call.thread_id = thread.id
            db.flush()
            llm_calls_list.append(_llm_call_to_dict(routing_call))

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
        "llm_calls": llm_calls_list,
    }
