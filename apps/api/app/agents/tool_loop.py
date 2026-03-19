"""Agentic tool-use loop engine.

Drives multi-turn conversations where the LLM can invoke connector tools
(read-only auto-execute, write tools pause for approval) in a loop of up
to MAX_ITERATIONS before returning a response to the user.
"""

import json
import logging
import threading
import time

from sqlalchemy.orm import Session

from app.db.models import Task, Message, ToolCall, LlmCall

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 10

# Thread-local storage for streaming events to the client during the tool loop
_thread_local = threading.local()


def set_event_callback(callback):
    """Set the event callback for the current thread."""
    _thread_local.event_callback = callback


def _emit_event(event: dict):
    """Emit an event to the client if a callback is set."""
    cb = getattr(_thread_local, 'event_callback', None)
    if cb:
        logger.debug("Emitting SSE event: type=%s", event.get("type"))
        cb(event)
    else:
        logger.debug("No event callback set, skipping event: type=%s", event.get("type"))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_tool_loop(
    message: str,
    task: Task,
    db: Session,
    system_prompt: str,
    anthropic_tools: list[dict],
    context: dict | None = None,
) -> dict:
    """Run the agentic tool loop for a user message.

    Returns a result dict suitable for the API response.
    """
    # Build initial messages list from conversation history
    messages = _build_messages(task, message, context)

    return _execute_loop(messages, task, db, system_prompt, anthropic_tools, start_iteration=1)


def resume_tool_loop(
    task: Task,
    db: Session,
    system_prompt: str,
    anthropic_tools: list[dict],
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
    pending_ids = task.pending_tool_call_ids or []
    tool_results_content = []

    for tc_id in pending_ids:
        tc = db.query(ToolCall).filter(ToolCall.id == tc_id).first()
        if not tc:
            continue

        if tc.status == "approved":
            # Execute the approved write tool
            result = _execute_tool_call(tc, db)
            tool_results_content.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": _slim_tool_result(result, tc.id),
            })
        elif tc.status == "rejected":
            tool_results_content.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": json.dumps({"status": "rejected", "message": "User rejected this action."}),
            })

    # Inject results into conversation
    if tool_results_content:
        messages.append({"role": "user", "content": tool_results_content})

    # Clear pending state
    task.pending_tool_call_ids = None
    task.agent_loop_state = None
    db.flush()

    return _execute_loop(messages, task, db, system_prompt, anthropic_tools, start_iteration=iteration + 1)


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------

def _execute_loop(
    messages: list[dict],
    task: Task,
    db: Session,
    system_prompt: str,
    anthropic_tools: list[dict],
    start_iteration: int = 1,
) -> dict:
    """Run the agentic loop up to MAX_ITERATIONS."""
    from app.interpreter.llm_interpreter import call_llm_with_tools

    # Build a lookup from tool name -> tool metadata
    tool_meta = _build_tool_meta(anthropic_tools, db)

    thinking_steps: list[str] = []
    display_blocks: list[dict] = []

    for iteration in range(start_iteration, MAX_ITERATIONS + 1):
        response, llm_call_id = call_llm_with_tools(
            system_prompt=system_prompt,
            messages=messages,
            tools=anthropic_tools,
            db=db,
            task_id=task.id,
            call_type="tool_use",
        )

        # Check stop reason
        if response.stop_reason == "end_turn":
            # LLM is done — extract text and return
            text = _extract_text(response)
            db.add(Message(task_id=task.id, role="assistant", content=text, display_blocks=display_blocks or None))
            task.status = "completed" if task.status == "in_progress" else task.status
            task.thinking_steps = thinking_steps or None
            db.commit()
            return _build_response(task, db, text, thinking_steps=thinking_steps, display_blocks=display_blocks)

        if response.stop_reason == "tool_use":
            # The LLM is calling a tool. If it streamed text without the
            # [Tool] prefix, the frontend may have routed it to conversation.
            # Emit stream_cancel so the frontend clears any accidental
            # streaming message.
            _emit_event({"type": "stream_cancel"})

            # Process tool calls
            tool_results = []
            pending_writes: list[ToolCall] = []

            for block in response.content:
                if block.type != "tool_use":
                    continue

                # Handle synthetic search tool
                if block.name == "norm__search_tool_result":
                    search_result = _search_tool_result(
                        block.input.get("tool_call_id", ""),
                        block.input.get("query", ""),
                        block.input.get("fields"),
                        db,
                    )
                    thinking_steps.append(f"Searching previous result for '{block.input.get('query', '')}'…")
                    _emit_event({"type": "thinking", "text": f"Searching previous result for '{block.input.get('query', '')}'…"})
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": _truncate_tool_result(json.dumps(search_result)),
                    })
                    continue

                connector, action = _parse_tool_name(block.name)
                meta = tool_meta.get(block.name, {})
                method = meta.get("method", "POST")

                if _is_read_only(method):
                    # Auto-execute read-only tool
                    tc = ToolCall(
                        id=block.id,
                        task_id=task.id,
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

                    readable = action.replace("_", " ")
                    thinking_steps.append(f"Fetching {readable} from {connector}…")
                    _emit_event({"type": "thinking", "text": f"Fetching {readable} from {connector}…"})
                    result = _execute_tool_call(tc, db)

                    # Look up tool def for summary_fields
                    tool_def = _find_tool_def(connector, action, db)
                    summary_fields = tool_def.get("summary_fields") if tool_def else None
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": _slim_tool_result(result, block.id, summary_fields=summary_fields),
                    })

                    # Build display block if tool has a display_component
                    if tool_def:
                        wd_config = tool_def.get("working_document")
                        if wd_config and tc.result_payload:
                            # Create/update a working document and reference it in the display block
                            doc = _upsert_working_document(
                                db, task.id, connector, wd_config,
                                tc.result_payload, tc.input_params,
                            )
                            component = tool_def.get("display_component")
                            if component:
                                display_blocks.append({
                                    "component": component,
                                    "data": {"working_document_id": doc.id},
                                    "props": tool_def.get("display_props") or {},
                                })
                        else:
                            block_data = _build_display_block(tool_def, tc.result_payload)
                            if block_data:
                                display_blocks.append(block_data)
                else:
                    # Check if this write tool has a working_document config
                    tool_def = _find_tool_def(connector, action, db)
                    wd_config = tool_def.get("working_document") if tool_def else None

                    if wd_config:
                        # Working document mode: create a doc from input params, skip approval
                        tc = ToolCall(
                            id=block.id,
                            task_id=task.id,
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

                        thinking_steps.append(f"Preparing {action.replace('_', ' ')} on {connector}…")
                        _emit_event({"type": "thinking", "text": f"Preparing {action.replace('_', ' ')} on {connector}…"})
                        doc = _upsert_working_document(
                            db, task.id, connector, wd_config,
                            block.input or {}, block.input,
                        )
                        component = tool_def.get("display_component") if tool_def else None
                        if component:
                            display_blocks.append({
                                "component": component,
                                "data": {"working_document_id": doc.id},
                                "props": tool_def.get("display_props") or {},
                            })

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps({
                                "status": "draft_created",
                                "message": f"Draft {wd_config.get('doc_type', 'document')} created. The user can review and edit it in the UI, then submit when ready.",
                                "working_document_id": doc.id,
                            }),
                        })
                    else:
                        # Standard approval flow (no working document)
                        tc = ToolCall(
                            id=block.id,
                            task_id=task.id,
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
                            display_blocks.append({
                                "component": tool_def["display_component"],
                                "data": block.input or {},
                                "props": tool_def.get("display_props") or {},
                            })

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps({
                                "status": "pending_approval",
                                "message": "This write operation requires user approval before execution.",
                            }),
                        })

            # Capture intermediate LLM text (its reasoning before tool calls).
            # Don't emit as a thinking event — the text was already streamed
            # as token events. Prefix with [reasoning] so the Activity tab can
            # distinguish it from short status thinking steps.
            intermediate_text = _extract_text(response)
            if intermediate_text and intermediate_text != "Done.":
                thinking_steps.append(f"[reasoning] {intermediate_text}")

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

                # Build a description of what the agent wants to do
                desc_parts = []
                for tc in pending_writes:
                    desc_parts.append(f"**{tc.action}** on {tc.connector_name} with: {json.dumps(tc.input_params)}")
                approval_text = "I'd like to perform the following actions:\n\n" + "\n".join(f"- {d}" for d in desc_parts) + "\n\nPlease approve or reject."

                db.add(Message(task_id=task.id, role="assistant", content=approval_text, display_blocks=display_blocks or None))
                task.thinking_steps = thinking_steps or None
                db.commit()
                return _build_response(task, db, approval_text, thinking_steps=thinking_steps, display_blocks=display_blocks)

            # All tools were read-only — feed results back and continue loop
            assistant_content = [_serialize_block(b) for b in response.content]
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})

        else:
            # Unexpected stop reason — treat as end_turn
            text = _extract_text(response)
            db.add(Message(task_id=task.id, role="assistant", content=text, display_blocks=display_blocks or None))
            task.thinking_steps = thinking_steps or None
            db.commit()
            return _build_response(task, db, text, thinking_steps=thinking_steps, display_blocks=display_blocks)

    # Max iterations reached
    text = "I've gathered what I can. Let me know if you need anything else or want me to continue."
    db.add(Message(task_id=task.id, role="assistant", content=text, display_blocks=display_blocks or None))
    task.thinking_steps = thinking_steps or None
    db.commit()
    return _build_response(task, db, text, thinking_steps=thinking_steps, display_blocks=display_blocks)


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _execute_tool_call(tc: ToolCall, db: Session) -> dict:
    """Execute a tool call against the connector spec and record the result."""
    from app.db.models import ConnectorSpec, ConnectorConfig
    from app.connectors.spec_executor import execute_spec

    spec = db.query(ConnectorSpec).filter(
        ConnectorSpec.connector_name == tc.connector_name,
    ).first()

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

    if not tool_def:
        tc.status = "failed"
        tc.error_message = f"Tool not found: {tc.action} in {tc.connector_name}"
        db.flush()
        return {"error": tc.error_message}

    # Get credentials
    config_row = db.query(ConnectorConfig).filter(
        ConnectorConfig.connector_name == tc.connector_name,
    ).first()
    credentials = config_row.config if config_row else {}

    t0 = time.time()
    try:
        result, rendered = execute_spec(
            spec, tool_def, tc.input_params or {}, credentials, db, tc.task_id,
        )
        tc.duration_ms = int((time.time() - t0) * 1000)
        tc.rendered_request = rendered.to_audit_dict()
        tc.result_payload = result.response_payload
        tc.status = "executed" if result.success else "failed"
        tc.error_message = result.error_message
        db.flush()

        return {
            "success": result.success,
            "data": result.response_payload,
            "reference": result.reference,
            "error": result.error_message,
        }
    except Exception as exc:
        tc.duration_ms = int((time.time() - t0) * 1000)
        tc.status = "failed"
        tc.error_message = str(exc)
        db.flush()
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_messages(task: Task, new_message: str, context: dict | None = None) -> list[dict]:
    """Build the messages list from task conversation history + new message."""
    messages: list[dict] = []

    # Include existing conversation (excluding the very latest user msg we're about to add)
    for msg in sorted(task.messages, key=lambda m: m.created_at):
        messages.append({"role": msg.role, "content": msg.content})

    # Add context if provided
    content = new_message
    if context:
        context_parts = []
        for key, value in context.items():
            if key == "open_task":
                continue
            if value:
                label = key.upper().replace("_", " ")
                context_parts.append(f"{label}: {json.dumps(value)}")
        if context_parts:
            content = new_message + "\n\n[Context]\n" + "\n".join(context_parts)

    messages.append({"role": "user", "content": content})
    return messages


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


MAX_TOOL_RESULT_CHARS = 30_000  # ~7-8k tokens — keeps context budget in check


def _truncate_tool_result(content: str) -> str:
    """Simple character-level truncation — safety net for already-processed results."""
    if len(content) <= MAX_TOOL_RESULT_CHARS:
        return content
    return content[:MAX_TOOL_RESULT_CHARS] + "\n\n[... truncated — result too large]"


def _slim_tool_result(
    raw_result: dict,
    tool_call_id: str,
    summary_fields: list[str] | None = None,
    max_chars: int = MAX_TOOL_RESULT_CHARS,
) -> str:
    """Slim a large tool result for the LLM context.

    If summary_fields is set, extracts only those fields from each item.
    Otherwise, tells the LLM to use the search tool.
    """
    serialized = json.dumps(raw_result)
    if len(serialized) <= max_chars:
        return serialized

    data = raw_result.get("data")

    # Only handle array-of-objects (most common large result pattern)
    if not isinstance(data, list) or len(data) == 0 or not isinstance(data[0], dict):
        return serialized[:max_chars] + "\n\n[... truncated — result too large]"

    if summary_fields:
        # Strategy 1: Slim to configured summary_fields
        slim_items = [{k: item.get(k) for k in summary_fields if k in item} for item in data]
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

    # Strategy 2: Don't show data, tell Claude to search
    return json.dumps({
        "_too_large": True,
        "_total_items": len(data),
        "_fields_available": list(data[0].keys()),
        "_tool_call_id": tool_call_id,
        "success": raw_result.get("success"),
        "message": (
            f"Result contains {len(data)} items and is too large to display. "
            f"Use norm__search_tool_result with tool_call_id '{tool_call_id}' "
            f"and a search keyword to find specific items."
        ),
    })


def _search_tool_result(tool_call_id: str, query: str, fields: str | None, db: Session) -> dict:
    """Search through a stored tool call's result payload by keyword."""
    tc = db.query(ToolCall).filter(ToolCall.id == tool_call_id).first()
    if not tc or not tc.result_payload:
        return {"error": "Tool call not found or has no stored result"}

    data = tc.result_payload
    if isinstance(data, dict):
        data = data.get("data", data)

    if not isinstance(data, list):
        if query.lower() in json.dumps(data).lower():
            return {"matches": [data], "total_matches": 1}
        return {"matches": [], "total_matches": 0}

    query_lower = query.lower()
    matches = []
    for item in data:
        if query_lower in json.dumps(item).lower():
            if fields:
                field_list = [f.strip() for f in fields.split(",")]
                matches.append({k: item.get(k) for k in field_list if k in item})
            else:
                matches.append(item)

    return {"matches": matches, "total_matches": len(matches)}


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
    task_id: str,
    connector_name: str,
    wd_config: dict,
    result_payload: dict | list,
    input_params: dict | None,
) -> "WorkingDocument":
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

    # Look for existing doc with same task + doc_type
    doc = db.query(WorkingDocument).filter(
        WorkingDocument.task_id == task_id,
        WorkingDocument.doc_type == doc_type,
    ).first()

    if doc:
        doc.data = result_payload
        doc.external_ref = external_ref or doc.external_ref
        doc.sync_status = "synced"
        doc.sync_error = None
        doc.pending_ops = []
        doc.version += 1
    else:
        doc = WorkingDocument(
            task_id=task_id,
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


def _find_tool_def(connector_name: str, action: str, db: Session) -> dict | None:
    """Look up a tool definition from the ConnectorSpec in the database."""
    from app.db.models import ConnectorSpec
    spec = db.query(ConnectorSpec).filter(ConnectorSpec.connector_name == connector_name).first()
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
    return {
        "component": component,
        "data": result_payload,
        "props": tool_def.get("display_props") or {},
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
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    # Fallback: use model_dump but strip any unknown extras
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return {"type": "text", "text": str(block)}


def _build_response(task: Task, db: Session, text: str, thinking_steps: list[str] | None = None, display_blocks: list[dict] | None = None) -> dict:
    """Build the API response dict from a task.

    Returns a lightweight payload suitable for SSE streaming. Heavy debug
    fields (full prompts, raw responses, result payloads) are omitted here
    and available on demand via GET /tasks/{task_id}.
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
            "error_message": tc.error_message,
            "duration_ms": tc.duration_ms,
            "created_at": tc.created_at.isoformat() if tc.created_at else None,
        }
        for tc in sorted(task.tool_calls, key=lambda x: x.created_at)
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
            "tools_provided": lc.tools_provided,
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
