"""Agentic tool-use loop engine.

Drives multi-turn conversations where the LLM can invoke connector tools
(read-only auto-execute, write tools pause for approval) in a loop of up
to MAX_ITERATIONS before returning a response to the user.
"""

import datetime
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from app.db.models import Thread, Message, ToolCall

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 10


def _human_readable_summary(action: str, connector: str, params: dict | None) -> str:
    """Return a short human-readable description of a tool call."""
    params = params or {}
    if action in ("send_notification", "send_email"):
        to = params.get("to") or params.get("recipient") or "recipient"
        template = params.get("template_name") or params.get("subject") or ""
        label = "email" if action == "send_email" else "notification"
        return f"Send {label} to {to}" + (f" ({template})" if template else "")
    if action == "create_order":
        supplier = params.get("supplier_name") or connector
        return f"Create purchase order via {supplier}"
    if action == "create_employee":
        name = (
            f"{params.get('firstName', '')} {params.get('lastName', '')}".strip()
            or "new employee"
        )
        return f"Create employee record: {name}"
    return f"{action.replace('_', ' ').title()} via {connector}"


def _ts_step(text: str) -> str:
    """Return a thinking step prefixed with an ISO timestamp."""
    return f"[ts:{datetime.datetime.now(datetime.timezone.utc).isoformat()}] {text}"


# Thread-local storage for streaming events to the client during the tool loop
_thread_local = threading.local()


def set_event_callback(callback):
    """Set the event callback for the current thread."""
    _thread_local.event_callback = callback


def _emit_event(event: dict):
    """Emit an event to the client if a callback is set."""
    cb = getattr(_thread_local, "event_callback", None)
    if cb:
        logger.debug("Emitting SSE event: type=%s", event.get("type"))
        cb(event)
    else:
        logger.debug(
            "No event callback set, skipping event: type=%s", event.get("type")
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_tool_loop(
    message: str,
    task: Thread,
    db: Session,
    system_prompt: str,
    anthropic_tools: list[dict],
    context: dict | None = None,
    test_mode: bool = False,
    config_db: Session | None = None,
    messages_override: list[dict] | None = None,
) -> dict:
    """Run the agentic tool loop for a user message.

    Returns a result dict suitable for the API response.
    If test_mode=True, write tools (POST/PUT/DELETE) are simulated.
    If messages_override is provided, use it instead of building from task history.
    """
    # Build initial messages list from conversation history
    messages = messages_override or _build_messages(task, message, context, db=db)

    return _execute_loop(
        messages,
        task,
        db,
        system_prompt,
        anthropic_tools,
        start_iteration=1,
        test_mode=test_mode,
        config_db=config_db,
    )


def resume_tool_loop(
    task: Thread,
    db: Session,
    system_prompt: str,
    anthropic_tools: list[dict],
    config_db: Session | None = None,
) -> dict:
    """Resume the tool loop after write-tool approval.

    Loads saved loop state, injects approved tool results, and continues.
    """
    state = task.agent_loop_state
    if not state:
        raise ValueError("No saved loop state to resume")

    messages = state["messages"]
    iteration = state["iteration"]

    # Gather approved tool calls and inject their results
    search_available = any(
        t["name"] == "norm__search_tool_result" for t in anthropic_tools
    )
    pending_ids = task.pending_tool_call_ids or []
    tool_results_content = []

    for tc_id in pending_ids:
        tc = db.query(ToolCall).filter(ToolCall.id == tc_id).first()
        if not tc:
            continue

        if tc.status == "approved":
            # Execute the approved write tool (transform applied inside _execute_tool_call)
            result = _execute_tool_call(tc, db, config_db=config_db)
            tool_results_content.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": _slim_tool_result(
                        result, tc.id, search_available=search_available
                    ),
                }
            )
        elif tc.status == "rejected":
            tool_results_content.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": json.dumps(
                        {"status": "rejected", "message": "User rejected this action."}
                    ),
                }
            )

    # Inject results into conversation
    if tool_results_content:
        messages.append({"role": "user", "content": tool_results_content})

    # Clear pending state and reset status
    task.pending_tool_call_ids = None
    task.agent_loop_state = None
    task.status = "in_progress"
    task.remove_tag("approval_required")
    db.flush()

    return _execute_loop(
        messages,
        task,
        db,
        system_prompt,
        anthropic_tools,
        start_iteration=iteration + 1,
        config_db=config_db,
    )


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------


def _execute_loop(
    messages: list[dict],
    task: Thread,
    db: Session,
    system_prompt: str,
    anthropic_tools: list[dict],
    start_iteration: int = 1,
    test_mode: bool = False,
    config_db: Session | None = None,
) -> dict:
    """Run the agentic loop up to MAX_ITERATIONS."""
    from app.interpreter.llm_interpreter import call_llm_with_tools

    # Build a lookup from tool name -> tool metadata
    tool_meta = _build_tool_meta(anthropic_tools, db)

    # Check if the search tool is available for this agent
    search_available = any(
        t["name"] == "norm__search_tool_result" for t in anthropic_tools
    )

    thinking_steps: list[str] = []
    display_blocks: list[dict] = []

    iteration = start_iteration
    while iteration <= MAX_ITERATIONS:
        display_blocks_before = len(display_blocks)
        try:
            response, llm_call_id = call_llm_with_tools(
                system_prompt=system_prompt,
                messages=messages,
                tools=anthropic_tools,
                db=db,
                thread_id=task.id,
                call_type="tool_use",
            )
        except Exception as exc:
            err_msg = str(exc).lower()
            if "prompt is too long" in err_msg or "too many tokens" in err_msg:
                logger.warning(
                    "Prompt too long in tool loop (iteration %d): %s", iteration, exc
                )
                text = (
                    "I've gathered quite a bit of data but the conversation has become too long "
                    "for me to process in one go. Please try starting a new conversation for "
                    "follow-up questions."
                )
                db.add(
                    Message(
                        thread_id=task.id,
                        role="assistant",
                        content=text,
                        display_blocks=display_blocks or None,
                    )
                )
                task.status = "completed"
                task.thinking_steps = thinking_steps or None
                db.commit()
                return _build_response(
                    task,
                    db,
                    text,
                    thinking_steps=thinking_steps,
                    display_blocks=display_blocks,
                )
            raise

        # Check stop reason
        if response.stop_reason == "end_turn":
            # LLM is done — extract text and return
            text = _extract_text(response)
            db.add(
                Message(
                    thread_id=task.id,
                    role="assistant",
                    content=text,
                    display_blocks=display_blocks or None,
                )
            )
            task.status = "completed" if task.status == "in_progress" else task.status
            task.thinking_steps = thinking_steps or None
            db.commit()
            return _build_response(
                task,
                db,
                text,
                thinking_steps=thinking_steps,
                display_blocks=display_blocks,
            )

        if response.stop_reason == "tool_use":
            # The LLM is calling a tool. Cancel the streaming conversation
            # message. Don't re-emit the reasoning text as a thinking event —
            # the frontend already captured it via the token stream.
            reasoning = _extract_text(response)
            if reasoning:
                cleaned = reasoning.lstrip()
                if cleaned.startswith("[Tool]"):
                    cleaned = cleaned[6:].lstrip()
            _emit_event({"type": "stream_cancel"})

            pending_writes: list[ToolCall] = []

            # --- Phase A: Categorize blocks ---
            read_only_blocks: list[tuple] = []
            write_blocks: list[tuple] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                connector, action = _parse_tool_name(block.name)
                meta = tool_meta.get(block.name, {})
                method = meta.get("method", "POST")
                if _is_read_only(method):
                    read_only_blocks.append((block, connector, action, method))
                else:
                    write_blocks.append((block, connector, action, method))

            # --- Phase B: Create ToolCall records for read-only blocks ---
            read_only_tcs: dict[str, ToolCall] = {}
            for block, connector, action, method in read_only_blocks:
                tc = ToolCall(
                    id=block.id,
                    thread_id=task.id,
                    llm_call_id=llm_call_id,
                    iteration=iteration,
                    tool_name=block.name,
                    connector_name=connector,
                    action=action,
                    method=method,
                    input_params=block.input,
                    status="executed",
                )
                db.add(tc)
                read_only_tcs[block.id] = tc
                readable = action.replace("_", " ")
                thinking_steps.append(
                    _ts_step(f"Fetching {readable} from {connector}…")
                )
                _emit_event(
                    {
                        "type": "thinking",
                        "text": f"Fetching {readable} from {connector}…",
                    }
                )

            if read_only_tcs:
                db.flush()

            # --- Phase C: Execute read-only tools ---
            execution_results: dict[str, dict] = {}

            if len(read_only_blocks) >= 2:
                # Parallel execution — commit so worker threads see the TC rows
                db.commit()
                event_cb = getattr(_thread_local, "event_callback", None)
                max_workers = min(len(read_only_blocks), 8)

                t0 = time.time()
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {
                        executor.submit(
                            _execute_tool_call_in_thread,
                            block.id,
                            event_cb,
                        ): block.id
                        for block, _, _, _ in read_only_blocks
                    }
                    for future in futures:
                        block_id = futures[future]
                        try:
                            _, result = future.result()
                            execution_results[block_id] = result
                        except Exception as exc:
                            execution_results[block_id] = {"error": str(exc)}

                elapsed = int((time.time() - t0) * 1000)
                logger.info(
                    "Parallel execution of %d read-only tools completed in %dms",
                    len(read_only_blocks),
                    elapsed,
                )

                # Refresh ToolCall objects so main session sees thread-committed data
                for tc in read_only_tcs.values():
                    db.refresh(tc)

            elif len(read_only_blocks) == 1:
                # Single tool — execute directly, no threading overhead
                block, connector, action, method = read_only_blocks[0]
                tc = read_only_tcs[block.id]
                execution_results[block.id] = _execute_tool_call(
                    tc, db, config_db=config_db
                )

            # --- Phase D: Post-process read-only results ---
            read_only_tool_results: dict[str, dict] = {}
            for block, connector, action, method in read_only_blocks:
                tc = read_only_tcs[block.id]
                result = execution_results[block.id]

                # Check for document content block (e.g., resume PDF)
                doc_block = None
                if (
                    isinstance(tc.result_payload, dict)
                    and "_document" in tc.result_payload
                ):
                    doc_block = tc.result_payload.pop("_document")
                    from sqlalchemy.orm.attributes import flag_modified

                    flag_modified(tc, "result_payload")
                    db.flush()

                tool_def = _find_tool_def(connector, action, db, config_db=config_db)
                summary_fields = tool_def.get("summary_fields") if tool_def else None

                # Transform already applied in _execute_tool_call — just slim for LLM context
                slimmed = _slim_tool_result(
                    result,
                    block.id,
                    summary_fields=summary_fields,
                    search_available=search_available,
                )
                # Inject search tool on first truncation
                if '"_too_large": true' in slimmed and not search_available:
                    search_def = _build_search_tool_schema()
                    if search_def:
                        anthropic_tools.append(search_def)
                        tool_meta["norm__search_tool_result"] = {
                            "method": "GET",
                            "connector": "norm",
                            "action": "search_tool_result",
                        }
                        search_available = True

                # Only store slimmed_content if actual slimming occurred
                raw_serialized = json.dumps(result)
                tc.slimmed_content = slimmed if slimmed != raw_serialized else None
                db.flush()
                if doc_block:
                    read_only_tool_results[block.id] = {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": [
                            {"type": "text", "text": slimmed},
                            doc_block,
                        ],
                    }
                else:
                    read_only_tool_results[block.id] = {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": slimmed,
                    }
                logger.info(
                    "Phase D: %s.%s → LLM gets %d chars",
                    connector,
                    action,
                    len(slimmed),
                )

                # Build display block if tool has a display_component
                if tool_def:
                    wd_config = tool_def.get("working_document")
                    if wd_config and tc.result_payload:
                        doc = _upsert_working_document(
                            db,
                            task.id,
                            connector,
                            wd_config,
                            tc.result_payload,
                            tc.input_params,
                        )
                        component = tool_def.get("display_component")
                        if component:
                            props = dict(tool_def.get("display_props") or {})
                            if tc.venue_id:
                                props["activeVenueId"] = tc.venue_id
                            display_blocks.append(
                                {
                                    "component": component,
                                    "data": {"working_document_id": doc.id},
                                    "props": props,
                                }
                            )
                    else:
                        block_data = _build_display_block(tool_def, tc.result_payload)
                        if block_data:
                            display_blocks.append(block_data)

            # --- Phase E: Process write blocks (unchanged logic) ---
            write_tool_results: dict[str, dict] = {}
            for block, connector, action, method in write_blocks:
                if test_mode:
                    tc = ToolCall(
                        id=block.id,
                        thread_id=task.id,
                        llm_call_id=llm_call_id,
                        iteration=iteration,
                        tool_name=block.name,
                        connector_name=connector,
                        action=action,
                        method=method,
                        input_params=block.input,
                        status="executed",
                    )
                    db.add(tc)
                    db.flush()
                    simulated = {
                        "simulated": True,
                        "message": f"Would execute: {action} on {connector}",
                        "input_params": block.input,
                    }
                    tc.result_payload = simulated
                    db.flush()
                    write_tool_results[block.id] = {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(simulated),
                    }
                    thinking_steps.append(
                        _ts_step(
                            f"[test] Would execute {action} on {connector} (simulated)"
                        )
                    )
                    _emit_event(
                        {
                            "type": "thinking",
                            "text": f"[test] Would execute {action} on {connector} (simulated)",
                        }
                    )
                    continue

                tool_def = _find_tool_def(connector, action, db, config_db=config_db)
                wd_config = tool_def.get("working_document") if tool_def else None

                if wd_config:
                    # Working document mode: create a doc from input params, skip approval
                    tc = ToolCall(
                        id=block.id,
                        thread_id=task.id,
                        llm_call_id=llm_call_id,
                        iteration=iteration,
                        tool_name=block.name,
                        connector_name=connector,
                        action=action,
                        method=method,
                        input_params=block.input,
                        status="executed",
                    )
                    db.add(tc)
                    db.flush()

                    thinking_steps.append(
                        _ts_step(
                            f"Preparing {action.replace('_', ' ')} on {connector}…"
                        )
                    )
                    _emit_event(
                        {
                            "type": "thinking",
                            "text": f"Preparing {action.replace('_', ' ')} on {connector}…",
                        }
                    )
                    doc = _upsert_working_document(
                        db,
                        task.id,
                        connector,
                        wd_config,
                        block.input or {},
                        block.input,
                    )
                    component = tool_def.get("display_component") if tool_def else None
                    if component:
                        display_blocks.append(
                            {
                                "component": component,
                                "data": {"working_document_id": doc.id},
                                "props": tool_def.get("display_props") or {},
                            }
                        )

                    write_tool_results[block.id] = {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(
                            {
                                "status": "draft_created",
                                "message": f"Draft {wd_config.get('doc_type', 'document')} created. The user can review and edit it in the UI, then submit when ready.",
                                "working_document_id": doc.id,
                            }
                        ),
                    }
                else:
                    # Standard approval flow (no working document)
                    tc = ToolCall(
                        id=block.id,
                        thread_id=task.id,
                        llm_call_id=llm_call_id,
                        iteration=iteration,
                        tool_name=block.name,
                        connector_name=connector,
                        action=action,
                        method=method,
                        input_params=block.input,
                        status="pending_approval",
                    )
                    db.add(tc)
                    db.flush()
                    pending_writes.append(tc)

                    if tool_def and tool_def.get("display_component"):
                        display_blocks.append(
                            {
                                "component": tool_def["display_component"],
                                "data": block.input or {},
                                "props": tool_def.get("display_props") or {},
                            }
                        )

                    write_tool_results[block.id] = {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(
                            {
                                "status": "pending_approval",
                                "message": "This write operation requires user approval before execution.",
                            }
                        ),
                    }

            # --- Phase F: Assemble tool_results in original block order ---
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if block.id in read_only_tool_results:
                    tool_results.append(read_only_tool_results[block.id])
                elif block.id in write_tool_results:
                    tool_results.append(write_tool_results[block.id])

            # Capture intermediate LLM text (its reasoning before tool calls).
            # Don't emit as a thinking event — the text was already streamed
            # as token events. Prefix with [reasoning] so the Activity tab can
            # distinguish it from short status thinking steps.
            intermediate_text = _extract_text(response)
            if intermediate_text and intermediate_text != "Done.":
                thinking_steps.append(_ts_step(f"[reasoning] {intermediate_text}"))

            if pending_writes:
                # Serialize the assistant response content for state storage
                assistant_content = [_serialize_block(b) for b in response.content]
                messages.append({"role": "assistant", "content": assistant_content})

                task.agent_loop_state = {
                    "messages": messages,
                    "iteration": iteration,
                }
                task.pending_tool_call_ids = [tc.id for tc in pending_writes]
                task.status = "awaiting_tool_approval"

                # Build structured approval display block
                tool_call_summaries = []
                for tc in pending_writes:
                    tool_call_summaries.append(
                        {
                            "id": tc.id,
                            "action": tc.action,
                            "connector_name": tc.connector_name,
                            "method": tc.method,
                            "summary": _human_readable_summary(
                                tc.action, tc.connector_name, tc.input_params
                            ),
                            "input_params": tc.input_params,
                        }
                    )

                display_blocks.append(
                    {
                        "component": "tool_approval",
                        "data": {
                            "thread_id": task.id,
                            "tool_calls": tool_call_summaries,
                            "status": "pending",
                        },
                        "props": {},
                    }
                )

                summaries = [s["summary"] for s in tool_call_summaries]
                approval_text = "I'd like to:\n\n" + "\n".join(
                    f"- {s}" for s in summaries
                )

                db.add(
                    Message(
                        thread_id=task.id,
                        role="assistant",
                        content=approval_text,
                        display_blocks=display_blocks or None,
                    )
                )
                task.thinking_steps = thinking_steps or None
                db.commit()
                return _build_response(
                    task,
                    db,
                    approval_text,
                    thinking_steps=thinking_steps,
                    display_blocks=display_blocks,
                )

            # Don't count search-only iterations toward the limit
            only_searches = all(
                block.name == "norm__search_tool_result"
                for block in response.content
                if block.type == "tool_use"
            )
            if only_searches and not pending_writes:
                iteration -= 1

            # Display-only tools (e.g. render_chart) don't produce data
            # the LLM needs.  If the LLM already emitted a substantive
            # answer alongside these tool calls, treat as end_turn —
            # skip the extra LLM call that would just say "Done.".
            new_display_blocks = len(display_blocks) - display_blocks_before
            if not pending_writes and reasoning and new_display_blocks > 0:
                # At least one display-only tool ran.  The LLM's text
                # is the real answer — strip [Tool] prefix if present.
                answer = reasoning.lstrip()
                if answer.startswith("[Tool]"):
                    answer = answer[len("[Tool]") :].lstrip()
                # Only early-exit when the text is a real answer
                # (not just a short tool-call explanation).
                if len(answer) > 120:
                    db.add(
                        Message(
                            thread_id=task.id,
                            role="assistant",
                            content=answer,
                            display_blocks=display_blocks or None,
                        )
                    )
                    task.status = (
                        "completed" if task.status == "in_progress" else task.status
                    )
                    task.thinking_steps = thinking_steps or None
                    db.commit()
                    return _build_response(
                        task,
                        db,
                        answer,
                        thinking_steps=thinking_steps,
                        display_blocks=display_blocks,
                    )

            # All tools were read-only — feed results back and continue loop
            assistant_content = [_serialize_block(b) for b in response.content]
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})
            iteration += 1
            continue

        else:
            # Unexpected stop reason — treat as end_turn
            text = _extract_text(response)
            db.add(
                Message(
                    thread_id=task.id,
                    role="assistant",
                    content=text,
                    display_blocks=display_blocks or None,
                )
            )
            task.thinking_steps = thinking_steps or None
            db.commit()
            return _build_response(
                task,
                db,
                text,
                thinking_steps=thinking_steps,
                display_blocks=display_blocks,
            )

    # Max iterations reached — give the LLM one final chance to summarise
    _emit_event(
        {
            "type": "thinking",
            "text": "Reached tool call limit — summarising findings...",
        }
    )
    messages.append(
        {
            "role": "user",
            "content": (
                "You have used all available tool calls for this turn. "
                "Please present your best answer using the data you have already collected. "
                "Be specific — include numbers, names, and dates from the tool results. "
                "End with: 'I can keep researching if you'd like, or we can look at something else.'"
            ),
        }
    )
    try:
        final_response = call_llm_with_tools(
            system_prompt,
            messages,
            tools=[],  # No tools — forces text-only response
            db=db,
            thread_id=task.id,
            call_type="tool_use",
        )
        text = _extract_text(final_response)
    except Exception:
        logger.exception("Final summary LLM call failed after max iterations")
        text = (
            "I've done some research but ran out of tool calls before finishing. "
            "Send a follow-up message and I'll continue where I left off."
        )

    db.add(
        Message(
            thread_id=task.id,
            role="assistant",
            content=text,
            display_blocks=display_blocks or None,
        )
    )
    task.thinking_steps = thinking_steps or None
    db.commit()
    return _build_response(
        task, db, text, thinking_steps=thinking_steps, display_blocks=display_blocks
    )


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


def _execute_tool_call_in_thread(tc_id: str, event_callback) -> tuple[str, dict]:
    """Execute a tool call in a worker thread with its own DB session.

    Returns (tc_id, result_dict).
    """
    from app.db.engine import SessionLocal, _ConfigSessionLocal

    set_event_callback(event_callback)
    thread_db = SessionLocal()
    thread_config_db = _ConfigSessionLocal()
    try:
        tc = thread_db.query(ToolCall).filter(ToolCall.id == tc_id).first()
        if not tc:
            return (tc_id, {"error": f"ToolCall not found: {tc_id}"})
        result = _execute_tool_call(tc, thread_db, config_db=thread_config_db)
        thread_db.commit()
        return (tc_id, result)
    except Exception as exc:
        thread_db.rollback()
        return (tc_id, {"error": str(exc)})
    finally:
        thread_db.close()
        thread_config_db.close()


def _execute_tool_call(
    tc: ToolCall, db: Session, config_db: Session | None = None
) -> dict:
    """Execute a tool call against the connector spec and record the result."""
    from app.db.models import ConnectorSpec
    from app.connectors.spec_executor import execute_spec

    _cdb = config_db
    if _cdb is None:
        raise RuntimeError(
            "config_db is required — check that config_db is passed through the call chain"
        )
    spec = (
        _cdb.query(ConnectorSpec)
        .filter(
            ConnectorSpec.connector_name == tc.connector_name,
        )
        .first()
    )

    if not spec:
        tc.status = "failed"
        tc.error_message = f"Connector spec not found: {tc.connector_name}"
        db.flush()
        return {"error": tc.error_message}

    # Find the matching tool definition
    tool_def = None
    for t in spec.tools or []:
        if t.get("action") == tc.action:
            tool_def = t
            break

    # Check for internal handler first — works for any execution_mode
    from app.agents.internal_tools import get_handler

    handler = get_handler(tc.connector_name, tc.action)

    if not handler and not tool_def:
        tc.status = "failed"
        tc.error_message = f"Tool not found: {tc.action} in {tc.connector_name}"
        db.flush()
        return {"error": tc.error_message}

    # Check for consolidator config on the tool definition
    if not handler and tool_def and tool_def.get("consolidator_config"):
        from app.agents.internal_tools import execute_consolidator

        def handler(params, db_sess, tid):
            return execute_consolidator(
                tool_def["consolidator_config"], params, db_sess, tid
            )

    if handler or spec.execution_mode == "internal":
        t0 = time.time()
        try:
            result = handler(tc.input_params or {}, db, tc.thread_id)
            tc.duration_ms = int((time.time() - t0) * 1000)
            payload = result.get("data")
            # Set venue_id from handler result if available (for display block props)
            if (
                isinstance(payload, dict)
                and payload.get("venue_id")
                and not tc.venue_id
            ):
                tc.venue_id = payload["venue_id"]
            # Preserve _chart_props on result_payload so _build_display_block can find them
            if isinstance(payload, dict) and "_chart_props" in result:
                payload = {**payload, "_chart_props": result["_chart_props"]}
            # Apply response transform for internal tools too
            if tool_def:
                transform_config = tool_def.get("response_transform")
                if transform_config and transform_config.get("enabled") and payload:
                    from app.connectors.response_transform import (
                        apply_response_transform,
                    )

                    wrapped = (
                        {"data": payload}
                        if isinstance(payload, list)
                        else (
                            payload if isinstance(payload, dict) else {"data": payload}
                        )
                    )
                    transformed = apply_response_transform(wrapped, transform_config)
                    payload = (
                        transformed.get("data", transformed)
                        if isinstance(transformed, dict)
                        else transformed
                    )
            tc.result_payload = payload
            tc.status = "executed" if result.get("success") else "failed"
            tc.error_message = result.get("error")
            tc.rendered_request = {
                "mode": "internal",
                "handler": f"{tc.connector_name}.{tc.action}",
            }
            db.flush()
            return {
                "success": result.get("success"),
                "data": payload,
                "error": result.get("error"),
            }
        except Exception as exc:
            tc.duration_ms = int((time.time() - t0) * 1000)
            tc.status = "failed"
            tc.error_message = str(exc)
            db.flush()
            return {"error": str(exc)}

    # Get credentials — venue-aware lookup
    config_row = _resolve_venue_config(tc.connector_name, tc.input_params or {}, db)
    credentials = config_row.config if config_row else {}
    resolved_venue_id = config_row.venue_id if config_row else None
    tc.venue_id = resolved_venue_id

    # Strip venue params before passing to spec executor (they're not API fields)
    params_for_spec = dict(tc.input_params or {})
    params_for_spec.pop("venue", None)
    params_for_spec.pop("venue_name", None)
    params_for_spec.pop("venue_id", None)

    t0 = time.time()
    try:
        result, rendered = execute_spec(
            spec,
            tool_def,
            params_for_spec,
            credentials,
            db,
            tc.thread_id,
            venue_id=resolved_venue_id,
        )
        tc.duration_ms = int((time.time() - t0) * 1000)
        tc.rendered_request = rendered.to_audit_dict()

        # Apply response transform BEFORE storing — the DB stores only transformed data
        payload = result.response_payload

        # Resolve venue timezone for datetime field options (|tz, |dow)
        venue_tz_name = None
        if resolved_venue_id:
            from app.db.models import Venue

            venue_obj = db.query(Venue).filter(Venue.id == resolved_venue_id).first()
            if venue_obj and venue_obj.timezone:
                venue_tz_name = venue_obj.timezone

        if tool_def:
            transform_config = tool_def.get("response_transform")
            if transform_config and transform_config.get("enabled"):
                from app.connectors.response_transform import apply_response_transform

                wrapped = (
                    {"data": payload}
                    if isinstance(payload, list)
                    else (payload if isinstance(payload, dict) else {"data": payload})
                )
                transformed = apply_response_transform(
                    wrapped, transform_config, venue_timezone=venue_tz_name
                )
                payload = (
                    transformed.get("data", transformed)
                    if isinstance(transformed, dict)
                    else transformed
                )

        tc.result_payload = payload
        tc.status = "executed" if result.success else "failed"
        tc.error_message = result.error_message
        db.flush()

        return {
            "success": result.success,
            "data": payload,
            "reference": result.reference,
            "error": result.error_message,
        }
    except Exception as exc:
        tc.duration_ms = int((time.time() - t0) * 1000)
        tc.status = "failed"
        tc.error_message = str(exc)
        db.flush()
        return {"error": str(exc)}


def _resolve_venue_config(connector_name: str, input_params: dict, db: Session):
    """Select the correct ConnectorConfig based on venue in tool params.

    Falls back to venue_id=NULL config for platform connectors.
    """
    from app.db.models import ConnectorConfig
    from app.services.venue_service import resolve_venue_id

    venue_name = input_params.get("venue") or input_params.get("venue_name")
    venue_id = input_params.get("venue_id")

    if venue_name and not venue_id:
        venue_id = resolve_venue_id(venue_name, db)

    if venue_id:
        config = (
            db.query(ConnectorConfig)
            .filter(
                ConnectorConfig.connector_name == connector_name,
                ConnectorConfig.venue_id == venue_id,
                ConnectorConfig.enabled == "true",
            )
            .first()
        )
        if config:
            return config

    # Fall back to venue-agnostic config (platform connectors only)
    return (
        db.query(ConnectorConfig)
        .filter(
            ConnectorConfig.connector_name == connector_name,
            ConnectorConfig.venue_id.is_(None),
            ConnectorConfig.enabled == "true",
        )
        .first()
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_messages(
    task: Thread, new_message: str, context: dict | None = None, db=None
) -> list[dict]:
    """Build the messages list from task conversation history + new message."""
    from app.agents.context_builder import build_conversation_messages

    return build_conversation_messages(
        list(task.messages),
        new_message,
        context=context,
        thread=task,
        db=db,
    )


def _build_tool_meta(anthropic_tools: list[dict], db: Session) -> dict:
    """Build a lookup from tool name -> {method, connector, action}."""
    meta = {}
    for tool in anthropic_tools:
        name = tool["name"]
        connector, action = _parse_tool_name(name)
        # Extract method from description prefix like "[GET] ..."
        desc = tool.get("description", "")
        method = "POST"
        if desc.startswith("["):
            method = desc.split("]")[0].strip("[").strip()
        meta[name] = {
            "method": method,
            "connector": connector,
            "action": action,
        }
    return meta


def _parse_tool_name(name: str) -> tuple[str, str]:
    """Parse 'connector__action' into (connector, action)."""
    parts = name.split("__", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "unknown", name


MAX_TOOL_RESULT_CHARS = 30_000  # ~7-8k tokens — with search tool active
MAX_TOOL_RESULT_CHARS_NO_SEARCH = (
    40_000  # ~10k tokens per result — room for multiple results + history
)


def _truncate_tool_result(content: str) -> str:
    """Simple character-level truncation — safety net for already-processed results."""
    if len(content) <= MAX_TOOL_RESULT_CHARS:
        return content
    return content[:MAX_TOOL_RESULT_CHARS] + "\n\n[... truncated — result too large]"


def _build_search_tool_schema() -> dict:
    """Build the Anthropic tool schema for norm__search_tool_result.

    Called dynamically when a tool result is truncated — avoids including
    the search tool in every request's tool list.
    """
    return {
        "name": "norm__search_tool_result",
        "description": (
            "[GET] Search, sort, or find top items in a large tool result. "
            "Use query for text search, sort_by for numeric sorting, or both."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_call_id": {
                    "type": "string",
                    "description": "The tool_use ID of the tool call to search",
                },
                "query": {
                    "type": "string",
                    "description": "Search keyword (fuzzy matching)",
                },
                "sort_by": {
                    "type": "string",
                    "description": "Field name to sort by (e.g. 'amount')",
                },
                "sort_order": {
                    "type": "string",
                    "description": "'desc' (default) or 'asc'",
                },
                "top_n": {
                    "type": "integer",
                    "description": "Max results to return (default 20)",
                },
                "fields": {
                    "type": "string",
                    "description": "Comma-separated field names to include in results",
                },
            },
            "required": ["tool_call_id"],
            "additionalProperties": False,
        },
    }


def _truncate_nested_arrays(obj, max_items: int = 3):
    """Recursively truncate nested arrays to max_items for preview/sample purposes."""
    if isinstance(obj, dict):
        return {k: _truncate_nested_arrays(v, max_items) for k, v in obj.items()}
    if isinstance(obj, list):
        truncated = [
            _truncate_nested_arrays(item, max_items) for item in obj[:max_items]
        ]
        if len(obj) > max_items:
            truncated.append(f"... {len(obj) - max_items} more items")
        return truncated
    return obj


def _slim_tool_result(
    raw_result: dict,
    tool_call_id: str,
    summary_fields: list[str] | None = None,
    max_chars: int = MAX_TOOL_RESULT_CHARS,
    search_available: bool = True,
) -> str:
    """Slim a large tool result for the LLM context.

    If summary_fields is set, extracts only those fields from each item.
    If search_available is True, tells the LLM to use the search tool for
    very large results. Otherwise falls back to simple truncation.
    """
    serialized = json.dumps(raw_result)
    if len(serialized) <= max_chars:
        return serialized

    data = (
        _unwrap_array(raw_result)
        if isinstance(raw_result, dict)
        else (raw_result if isinstance(raw_result, list) else None)
    )

    # Only handle array-of-objects (most common large result pattern)
    if not isinstance(data, list) or len(data) == 0 or not isinstance(data[0], dict):
        return serialized[:max_chars] + "\n\n[... truncated — result too large]"

    if summary_fields:
        # Strategy 1: Slim to configured summary_fields (truncate nested arrays)
        slim_items = [
            _truncate_nested_arrays(
                {k: item.get(k) for k in summary_fields if k in item}
            )
            for item in data
        ]
        summary = {
            "_slimmed": True,
            "_total_items": len(data),
            "_fields_available": list(data[0].keys()),
            "_showing_fields": summary_fields,
            "_tool_call_id": tool_call_id,
            "success": raw_result.get("success"),
            "data": slim_items,
        }
        result = json.dumps(summary)
        if len(result) <= max_chars:
            return result
        # Still too large even after slimming — fall through to strategy 2

    # Strategy 2: Too large — show sample item (with nested arrays truncated) and tell LLM to search
    return json.dumps(
        {
            "_too_large": True,
            "_total_items": len(data),
            "_fields_available": list(data[0].keys()),
            "_sample_item": _truncate_nested_arrays(data[0]),
            "_tool_call_id": tool_call_id,
            "success": raw_result.get("success"),
            "message": (
                f"Result contains {len(data)} items. Use norm__search_tool_result with tool_call_id='{tool_call_id}' to search or sort:\n"
                f"- Text search: query='keyword' (fuzzy matching, keep to core keyword e.g. 'corona' not 'corona beer')\n"
                f"- Sort by value: sort_by='amount', sort_order='desc' (or 'asc')\n"
                f"- Top N: top_n=5 to limit results\n"
                f"- Combine: query='keyword', sort_by='amount', top_n=10"
            ),
        }
    )


def _unwrap_array(payload: dict | list, max_depth: int = 8) -> list | None:
    """Find the largest array of dicts in a tool result by recursing into nested structures.

    Handles flat ({"data": [...]}), nested ({"data": {"lines": [...]}}),
    and deeply nested consolidator results ({"data": {"step_name": {"data": [{rosteredShifts: [...]}]}}}).
    Also recurses into list items to find large arrays nested inside single-item wrappers.
    """
    if max_depth <= 0:
        return None
    if isinstance(payload, list) and len(payload) > 0 and isinstance(payload[0], dict):
        # This is an array of dicts — but check if items contain larger nested arrays
        best: list | None = payload
        for item in payload[:3]:  # only check first few items
            nested = _unwrap_array(item, max_depth - 1)
            if nested and len(nested) > len(best):
                best = nested
        return best
    if not isinstance(payload, dict):
        return None

    best = None
    for val in payload.values():
        if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
            # Found an array of dicts — but recurse into it to find larger nested arrays
            candidate = _unwrap_array(val, max_depth - 1)
            if candidate and (best is None or len(candidate) > len(best)):
                best = candidate
        elif isinstance(val, dict):
            nested = _unwrap_array(val, max_depth - 1)
            if nested and (best is None or len(nested) > len(best)):
                best = nested
    return best


def _bigram_similarity(a: str, b: str) -> float:
    """Character bigram similarity (Jaccard index). Returns 0.0-1.0."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    a_bigrams = {a[i : i + 2] for i in range(len(a) - 1)}
    b_bigrams = {b[i : i + 2] for i in range(len(b) - 1)}
    if not a_bigrams or not b_bigrams:
        return 1.0 if a == b else 0.0
    intersection = a_bigrams & b_bigrams
    union = a_bigrams | b_bigrams
    return len(intersection) / len(union)


def _fuzzy_match_text(query_word: str, text: str, threshold: float = 0.45) -> bool:
    """Check if query_word fuzzy-matches anywhere in text."""
    q = query_word.lower()
    t = text.lower()
    if q in t:
        return True
    for word in t.split():
        if len(word) < 2:
            continue
        if _bigram_similarity(q, word) >= threshold:
            return True
    return False


def _search_tool_result(
    tool_call_id: str,
    query: str,
    fields: str | None,
    db: Session,
    thread_id: str | None = None,
    sort_by: str | None = None,
    sort_order: str | None = None,
    top_n: int | None = None,
) -> dict:
    """Search, sort, and filter a stored tool call's result payload.

    Supports fuzzy text search (query), numeric sorting (sort_by/sort_order),
    and result limiting (top_n). These can be combined or used independently.
    """
    tc = db.query(ToolCall).filter(ToolCall.id == tool_call_id).first()

    # Fallback: if exact ID not found, find the most recent GET tool call
    # for this thread (the LLM sometimes hallucinates the ID)
    if (not tc or not tc.result_payload) and thread_id:
        tc = (
            db.query(ToolCall)
            .filter(
                ToolCall.thread_id == thread_id,
                ToolCall.method == "GET",
                ToolCall.status == "executed",
                ToolCall.connector_name != "norm",
            )
            .order_by(ToolCall.created_at.desc())
            .first()
        )

    if not tc or not tc.result_payload:
        return {"error": "Tool call not found or has no stored result"}

    data = _unwrap_array(tc.result_payload)
    limit = top_n or 20

    if data is None:
        if query and query.lower() in json.dumps(tc.result_payload).lower():
            return {"matches": [tc.result_payload], "total_matches": 1}
        return {"matches": [], "total_matches": 0}

    # Apply field selection helper
    def _select_fields(item: dict) -> dict:
        if fields:
            field_list = [f.strip() for f in fields.split(",")]
            return _truncate_nested_arrays(
                {k: item.get(k) for k in field_list if k in item}
            )
        return _truncate_nested_arrays(item)

    # If query is provided, do fuzzy text matching first
    if query and query.strip():
        query_words = [w for w in query.lower().split() if len(w) >= 2]
        if query_words:
            scored: list[tuple[float, dict]] = []
            for item in data:
                text_values = [
                    str(v)
                    for v in item.values()
                    if isinstance(v, (str, int, float)) and v
                ]
                combined = " ".join(text_values)
                match_count = sum(
                    1 for qw in query_words if _fuzzy_match_text(qw, combined)
                )
                if match_count > 0:
                    score = match_count / len(query_words)
                    scored.append((score, item))

            # Sort by relevance, then by sort_by if provided
            if sort_by:
                descending = (sort_order or "desc").lower() != "asc"
                scored.sort(
                    key=lambda x: (
                        float(x[1].get(sort_by, 0))
                        if isinstance(x[1].get(sort_by), (int, float))
                        else 0
                    ),
                    reverse=descending,
                )
            else:
                scored.sort(key=lambda x: x[0], reverse=True)

            matches = [_select_fields(item) for _, item in scored[:limit]]
            return {"matches": matches, "total_matches": len(scored)}

    # No query — pure sort/filter mode
    if sort_by:
        descending = (sort_order or "desc").lower() != "asc"
        # Filter to items that have the sort field as a number
        sortable = [
            item
            for item in data
            if isinstance(item.get(sort_by), (int, float)) and item.get(sort_by, 0) != 0
        ]
        sortable.sort(key=lambda x: float(x.get(sort_by, 0)), reverse=descending)
        matches = [_select_fields(item) for item in sortable[:limit]]
        return {"matches": matches, "total_matches": len(sortable)}

    # No query and no sort — return nothing useful
    return {"matches": [], "total_matches": 0}


def _is_read_only(method: str) -> bool:
    """Return True if the HTTP method is read-only."""
    return method.upper() == "GET"


def _extract_text(response) -> str:
    """Extract text content from an Anthropic response."""
    parts = []
    for block in response.content:
        if block.type == "text":
            parts.append(block.text)
    return "\n".join(parts) if parts else "Done."


def _upsert_working_document(
    db: Session,
    thread_id: str,
    connector_name: str,
    wd_config: dict,
    result_payload: dict | list,
    input_params: dict | None,
) -> "WorkingDocument":  # noqa: F821
    """Create or update a working document from a tool response."""
    from app.db.models import WorkingDocument

    doc_type = wd_config.get("doc_type", "unknown")
    sync_mode = wd_config.get("sync_mode", "auto")

    # Build external_ref from input params and ref_fields config
    ref_fields = wd_config.get("ref_fields", [])
    external_ref = {}
    if input_params:
        for f in ref_fields:
            if f in input_params:
                external_ref[f] = input_params[f]

    # Look for existing doc with same thread + doc_type
    doc = (
        db.query(WorkingDocument)
        .filter(
            WorkingDocument.thread_id == thread_id,
            WorkingDocument.doc_type == doc_type,
        )
        .first()
    )

    if doc:
        doc.data = result_payload
        doc.external_ref = external_ref or doc.external_ref
        doc.sync_status = "synced"
        doc.sync_error = None
        doc.pending_ops = []
        doc.version += 1
    else:
        doc = WorkingDocument(
            thread_id=thread_id,
            doc_type=doc_type,
            connector_name=connector_name,
            sync_mode=sync_mode,
            data=result_payload,
            external_ref=external_ref or None,
            sync_status="synced",
        )
        db.add(doc)

    db.flush()
    return doc


def _find_tool_def(
    connector_name: str, action: str, db: Session, config_db: Session | None = None
) -> dict | None:
    """Look up a tool definition from the ConnectorSpec in the database."""
    from app.db.models import ConnectorSpec

    _cdb = config_db
    if _cdb is None:
        raise RuntimeError(
            "config_db is required — check that config_db is passed through the call chain"
        )
    spec = (
        _cdb.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == connector_name)
        .first()
    )
    if not spec:
        return None
    for t in spec.tools or []:
        if t.get("action") == action:
            return t
    return None


def _build_display_block(tool_def: dict, result_payload: dict | None) -> dict | None:
    """Build a display block dict if the tool has a display_component configured."""
    component = tool_def.get("display_component")
    if not component or not result_payload:
        return None
    props = dict(tool_def.get("display_props") or {})
    # Allow tools to pass dynamic props (e.g., chart configuration)
    if isinstance(result_payload, dict) and "_chart_props" in result_payload:
        props.update(result_payload.pop("_chart_props"))
    return {
        "component": component,
        "data": result_payload,
        "props": props,
    }


def _serialize_block(block) -> dict:
    """Serialize an Anthropic content block for re-use in message history.

    Only include fields that the API accepts — model_dump() on streaming
    response blocks can include internal extras (e.g. parsed_output) that
    cause 400 errors when the block is sent back as assistant message content.
    """
    block_type = getattr(block, "type", None)
    if block_type == "text":
        return {"type": "text", "text": block.text}
    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    # Fallback: use model_dump but strip any unknown extras
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return {"type": "text", "text": str(block)}


def _build_response(
    task: Thread,
    db: Session,
    text: str,
    thinking_steps: list[str] | None = None,
    display_blocks: list[dict] | None = None,
) -> dict:
    """Build the API response dict from a task.

    Returns a lightweight payload suitable for SSE streaming. Heavy debug
    fields (full prompts, raw responses, result payloads) are omitted here
    and available on demand via GET /threads/{thread_id}.
    """
    db.refresh(task)

    conversation = [
        {
            "role": m.role,
            "text": m.content,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "display_blocks": m.display_blocks,
        }
        for m in sorted(task.messages, key=lambda x: x.created_at)
    ]

    tool_calls = []
    for tc in sorted(task.tool_calls, key=lambda x: x.created_at):
        # result_payload is already transformed (applied in _execute_tool_call)
        payload = tc.result_payload

        # Cap result_payload to avoid large SSE events
        if payload is not None:
            payload_json = json.dumps(payload, default=str)
            if len(payload_json) > 10_000:
                if isinstance(payload, list):
                    payload = {
                        "_truncated": True,
                        "_total_items": len(payload),
                        "_preview": _truncate_nested_arrays(payload[:2]),
                    }
                elif isinstance(payload, dict):
                    data = payload.get("data")
                    if (
                        isinstance(data, list)
                        and len(json.dumps(data, default=str)) > 8_000
                    ):
                        payload = {
                            **payload,
                            "data": {
                                "_truncated": True,
                                "_total_items": len(data),
                                "_preview": _truncate_nested_arrays(data[:2]),
                            },
                        }
        tool_calls.append(
            {
                "id": tc.id,
                "iteration": tc.iteration,
                "tool_name": tc.tool_name,
                "connector_name": tc.connector_name,
                "action": tc.action,
                "method": tc.method,
                "input_params": tc.input_params,
                "status": tc.status,
                "result_payload": payload,
                "slimmed_content": tc.slimmed_content,
                "error_message": tc.error_message,
                "duration_ms": tc.duration_ms,
                "created_at": tc.created_at.isoformat() if tc.created_at else None,
            }
        )

    # Only include fields the activity timeline needs — heavy fields
    # (system_prompt, user_prompt, raw_response, tools_provided) are
    # available via GET /threads/{id} if the user expands a specific call.
    llm_calls = [
        {
            "id": lc.id,
            "call_type": lc.call_type,
            "model": lc.model,
            "status": lc.status,
            "error_message": lc.error_message,
            "duration_ms": lc.duration_ms,
            "input_tokens": lc.input_tokens,
            "output_tokens": lc.output_tokens,
            "created_at": lc.created_at.isoformat() if lc.created_at else None,
        }
        for lc in sorted(task.llm_calls, key=lambda x: x.created_at)
    ]

    result = {
        "id": task.id,
        "domain": task.domain,
        "intent": task.intent,
        "title": task.title,
        "message": text,
        "status": task.status,
        "extracted_fields": task.extracted_fields,
        "missing_fields": task.missing_fields,
        "clarification_question": task.clarification_question,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "conversation": conversation,
        "tool_calls": tool_calls,
        "llm_calls": llm_calls,
    }
    if thinking_steps:
        result["thinking_steps"] = thinking_steps
    if display_blocks:
        result["display_blocks"] = display_blocks
    return result
