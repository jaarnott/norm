"""Playbook workflow tools.

A curated playbook becomes one MCP tool taking natural language. Where a direct
read tool would let Claude compose data itself, a workflow tool hands the whole
request to Norm's own agent + tool loop — so Norm's playbook instructions, its
draft creation, and its human approval gate all stay in the loop. Claude states
intent; Norm decides what actually happens.

Modelled on task_scheduler.execute_task_now — the existing, proven headless
invocation of an agent. _emit_event no-ops without a callback, so running the
loop outside an HTTP/SSE context is safe.

The input schema is deliberately natural language (`request` + optional
`venue`). Typed per-playbook parameters would mean hand-authoring a schema in a
second place — exactly the "two places" failure the brief forbids. The
playbook's own `description` (the router-matching sentence) is reused as the
tool description; it's already written for exactly this job.
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy.orm import Session

from app.mcp import links
from app.mcp.principal import McpPrincipal

logger = logging.getLogger(__name__)

# MCP clients time out; the loop can run 10 iterations. If it exceeds this,
# return a "still running, watch in Norm" result rather than blocking.
WORKFLOW_TIMEOUT_S = 90


def execute_playbook_tool(
    playbook_slug: str,
    request: str,
    venue_id: str | None,
    principal: McpPrincipal,
    db: Session,
    config_db: Session,
) -> dict:
    """Run a curated playbook for one MCP request. Returns an MCP-shaped payload.

    Outcomes (all isError=False — a draft or a pending approval is a legitimate
    result, not a failure):
      - draft_created:     a WorkingDocument to review, with an open-in-Norm link
      - pending_approval:  a write awaiting human approval in Norm
      - completed:         finished; summary + thread link
      - running:           exceeded the timeout; loop continues, watch in Norm
    """
    from app.agents.registry import get_agent
    from app.agents.tool_loop import run_tool_loop
    from app.db.config_models import Playbook
    from app.db.models import Message, Thread, Venue

    pb = (
        config_db.query(Playbook)
        .filter(Playbook.slug == playbook_slug, Playbook.enabled == True)  # noqa: E712
        .first()
    )
    if pb is None:
        return {
            "error": f"Unknown or disabled workflow: {playbook_slug}",
            "code": "NOT_FOUND",
        }

    agent = get_agent(pb.agent_slug)
    if agent is None:
        return {"error": "Workflow agent unavailable", "code": "INTERNAL_ERROR"}

    venue = db.query(Venue).filter(Venue.id == venue_id).first() if venue_id else None

    # Fresh thread per invocation (run isolation, as task_scheduler does).
    thread = Thread(
        user_id=principal.user_id,
        venue_id=venue_id,
        domain=pb.agent_slug,
        intent=f"{pb.agent_slug}.mcp_playbook",
        status="in_progress",
        raw_prompt=request,
        title=f"[MCP] {pb.display_name}",
        extracted_fields={},
        missing_fields=[],
    )
    db.add(thread)
    db.flush()
    db.add(Message(thread_id=thread.id, role="user", content=request))
    # Commit so the worker thread's own session can load the thread. From here
    # the request session is done with this workflow.
    db.commit()
    thread_id = thread.id

    # Prompt + tools are plain data (str + list[dict] + dict) and safe to hand
    # to the worker thread; compute them on the request session up front.
    system_prompt, anthropic_tools = agent.get_tool_definitions(
        db,
        active_venue_name=venue.name if venue else None,
        venue_timezone=venue.timezone if venue else None,
        user_id=principal.user_id,
        config_db=config_db,
        playbook=pb,
    )
    if not system_prompt:
        system_prompt = f"You are the {pb.agent_slug} agent for Norm."
    context = agent.build_context(db, principal.user_id)

    # Run the loop on a DEDICATED session in a daemon thread, bounded by the
    # timeout. Python can't kill a thread, and run_tool_loop can't share the
    # request session, so on timeout we return "running" and let the worker
    # finish on its own session — it commits independently, and the user watches
    # progress in Norm via the link. This is why WORKFLOW_TIMEOUT_S exists.
    holder: dict = {}

    def _worker():
        from app.db.engine import SessionLocal, _ConfigSessionLocal

        wdb = SessionLocal()
        wcdb = _ConfigSessionLocal()
        try:
            wthread = wdb.query(Thread).filter(Thread.id == thread_id).first()
            result = run_tool_loop(
                request,
                wthread,
                wdb,
                system_prompt,
                anthropic_tools,
                context=context,
                config_db=wcdb,
            )
            wdb.refresh(wthread)
            holder["payload"] = _map_outcome(wdb, wthread, result)
        except Exception:
            wdb.rollback()
            logger.exception("mcp_playbook_failed", extra={"mcp_tool": playbook_slug})
            holder["payload"] = {
                "error": "The workflow could not be completed.",
                "code": "INTERNAL_ERROR",
            }
        finally:
            wdb.close()
            wcdb.close()

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    worker.join(WORKFLOW_TIMEOUT_S)

    if worker.is_alive():
        # Still running — hand back a link and let it finish in the background.
        return {
            "status": "running",
            "thread_id": thread_id,
            "open_in_norm": links.thread_link(thread_id),
            "note": "This is taking a little while. It will keep running in Norm — "
            "open the link to watch progress and see the result.",
        }

    return holder.get(
        "payload",
        {"error": "The workflow could not be completed.", "code": "INTERNAL_ERROR"},
    )


def _order_clarification(doc) -> dict | None:
    """A clarification request for an order draft that resolved nothing, or None.

    ``create_purchase_order`` builds a working document even when it matched no
    stock item — an ambiguous name ("corona" → Corona Extra, Corona 0%, …) or
    one it couldn't find. Reporting that as ``draft_created`` is the bug behind
    the empty card: the model reads "draft created", says "done", and renders a
    PO editor with zero lines. When nothing resolved, this returns a
    ``needs_input`` outcome instead — carrying the candidate products — so the
    model asks the user which one, the way it should, and no empty editor
    renders. A *partially* resolved order (some lines matched) is a real draft
    and returns None, so it still renders with what it has.
    """
    data = doc.data or {}
    if doc.doc_type not in ("order", "purchase_order"):
        return None
    if data.get("lines") or data.get("order_lines"):
        return None  # something matched — a real (if partial) draft

    needs = data.get("needs_selection") or []
    failed = (data.get("resolution_report") or {}).get("failed") or []
    if not needs and not failed:
        return None  # empty for some other reason — leave it to draft_created

    clarify = [
        {
            "requested": a.get("query"),
            "quantity": a.get("quantity"),
            "options": [
                c.get("name") for c in (a.get("candidates") or []) if c.get("name")
            ][:8],
        }
        for a in needs
    ]
    unfindable = [f.get("name") for f in failed if f.get("name")]

    parts: list[str] = []
    for c in clarify:
        opts = ", ".join(c["options"]) if c["options"] else "several products"
        parts.append(f'"{c["requested"]}" matches {opts}')
    if unfindable:
        parts.append("couldn't find: " + ", ".join(unfindable))
    message = (
        "Before this order can be drafted it needs one thing cleared up — "
        + "; ".join(parts)
        + ". Which did you mean?"
    )

    return {
        "status": "needs_input",
        "thread_id": doc.thread_id,
        "clarify": clarify,
        "unfindable": unfindable,
        "summary": message,
        "note": "Nothing has been drafted yet. Ask the user which product they "
        "mean, then create the order again naming that specific item. Do not "
        "tell the user the order was created.",
        "open_in_norm": links.thread_link(doc.thread_id),
    }


def _map_outcome(db, thread, result: dict) -> dict:
    """Map final thread state to an MCP workflow outcome payload."""
    from app.db.models import WorkingDocument

    doc = (
        db.query(WorkingDocument)
        .filter(WorkingDocument.thread_id == thread.id)
        .order_by(WorkingDocument.created_at.desc())
        .first()
    )
    if doc is not None:
        clarification = _order_clarification(doc)
        if clarification is not None:
            return clarification
        return {
            "status": "draft_created",
            "working_document_id": doc.id,
            "doc_type": doc.doc_type,
            "summary": result.get("message", ""),
            "open_in_norm": links.working_document_link(doc.id, thread.id),
            "note": "A draft is waiting in Norm for you to review and approve. "
            "Nothing has been submitted.",
        }

    if thread.status == "awaiting_tool_approval":
        return {
            "status": "pending_approval",
            "thread_id": thread.id,
            "summary": result.get("message", ""),
            "open_in_norm": links.thread_link(thread.id),
            "note": "This action needs your approval in Norm before it runs.",
        }

    return {
        "status": "completed",
        "thread_id": thread.id,
        "summary": result.get("message", ""),
        "open_in_norm": links.thread_link(thread.id),
    }
