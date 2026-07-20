"""One agent asking another a question.

Norm routes a turn to exactly one agent (`supervisor.handle_message`), so an
agent that needs something outside its domain has no way to ask. The alternative
— bolting another domain's connector tools onto its own binding — inherits none
of that domain's prompt knowledge and bloats the caller's tool list.

Modelled on Claude Code's subagents, with the three properties that make them
work kept intact:

  * **Isolation.** The child gets its target's system prompt plus the question,
    and *nothing* of the parent's conversation (`messages_override`). Only a
    summary comes back, so a verbose sub-run costs the parent a few hundred
    tokens rather than its whole context.
  * **Decentralised.** No orchestrator. Any agent granted the tool can consult
    any allowed target; who-may-call-whom is an AgentConnectorBinding row.
  * **Read-only.** Enforced by the tool set the child is handed, not by asking
    it nicely — the same way Claude's Explore agent is read-only.

Deliberately *not* modelled on Claude: sub-agent writes. Claude allows them
because its approval prompt is synchronous — the call blocks in-process, a human
clicks, the call returns. Norm's approval is a persisted state machine
(`thread.status = "awaiting_tool_approval"` + `agent_loop_state`, resumed by a
later HTTP request) built for exactly one level. A writing child would have to
suspend the parent too, and resume both in order. Until that exists, a child that
cannot write cannot get stuck. Cross-domain writes stay the parent's job.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# How deep a delegation chain may go. Norm has five domain agents; a chain
# longer than this is a loop, not a plan. At the cap the child is handed no
# delegate tool at all (Claude's technique) rather than erroring mid-run, so it
# answers with what it has instead of failing.
MAX_DELEGATION_DEPTH = 2

# Ceiling on delegations across one root thread's whole tree, so a fan-out loop
# can't quietly burn tokens.
MAX_DELEGATIONS_PER_ROOT = 10

# A child is a full tool loop nested inside the parent's turn. Left at the
# default 10 iterations, one user message could stack two 10-iteration loops and
# blow the request budget. A consultation that needs more than this is the wrong
# shape for delegation.
CHILD_MAX_ITERATIONS = 5

DELEGATE_ACTION = "delegate_to_agent"


class DelegationError(Exception):
    """A delegation that must not run. The message goes back to the model."""


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------


def _chain(thread, db: Session) -> list:
    """This thread and its delegation ancestors, nearest first."""
    from app.db.models import Thread

    out = [thread]
    seen = {thread.id}
    cur = thread
    # Bounded by construction, but guard anyway: a corrupt cycle in the data
    # must not spin here.
    for _ in range(MAX_DELEGATION_DEPTH + 2):
        if not cur.parent_thread_id or cur.parent_thread_id in seen:
            break
        cur = db.query(Thread).filter(Thread.id == cur.parent_thread_id).first()
        if not cur:
            break
        seen.add(cur.id)
        out.append(cur)
    return out


def root_of(thread, db: Session):
    """The user-facing thread at the top of a delegation chain."""
    return _chain(thread, db)[-1]


def _delegation_count(root, db: Session) -> int:
    """How many sub-runs already exist beneath this root."""
    from app.db.models import Thread

    seen = {root.id}
    frontier = [root.id]
    total = 0
    while frontier:
        kids = db.query(Thread.id).filter(Thread.parent_thread_id.in_(frontier)).all()
        ids = [k[0] for k in kids if k[0] not in seen]
        if not ids:
            break
        seen.update(ids)
        total += len(ids)
        frontier = ids
    return total


# ---------------------------------------------------------------------------
# Read-only tool filtering
# ---------------------------------------------------------------------------


def is_read_only_tool(tool_def: dict) -> bool:
    """Whether a spec tool only reads.

    Method is *not* the test. Norm uses GET to mean "runs without approval",
    which is a different question: `create_purchase_order`, `send_report_email`
    and `review_and_receive_invoices` are all GET and all change something. The
    `read_only` flag is set explicitly per action
    (scripts/sync_read_only_flags.py).

    Absent means False. A new action is not consultable until someone says it is
    — the cost of that is a sub-agent answering "I can't see that", never a
    sub-agent spending money.
    """
    if tool_def.get("read_only") is not True:
        return False
    return (tool_def.get("method") or "GET").upper() == "GET"


def read_only_actions(config_db: Session) -> set[str]:
    """Every action currently marked read-only, as bare action names.

    Bare names because that is the unit `tool_filter` and the per-agent binding
    allowlist already work in (prompt_builder line ~600).
    """
    from app.db.config_models import ConnectorSpec

    allowed: set[str] = set()
    for spec in config_db.query(ConnectorSpec).all():
        for tool in spec.tools or []:
            if is_read_only_tool(tool):
                allowed.add(tool.get("action"))
    allowed.discard(None)
    return allowed


def filter_to_read_only(anthropic_tools: list[dict], config_db: Session) -> list[dict]:
    """Drop everything the child must not be able to do.

    Also drops the delegate tool itself; whether the child may delegate onward
    is decided by depth in build_child_tools, not inherited by accident.
    """
    allowed = read_only_actions(config_db)
    out = []
    for t in anthropic_tools:
        action = t["name"].split("__", 1)[-1]
        if action == DELEGATE_ACTION:
            continue
        if action in allowed:
            out.append(t)
    return out


# ---------------------------------------------------------------------------
# Target resolution + guards
# ---------------------------------------------------------------------------


def resolve_target(target: str, config_db: Session) -> tuple[str, object | None]:
    """Parse "agent" or "agent/playbook" into (agent_slug, Playbook|None).

    A playbook target is the tighter option: it swaps in a slim prompt and its
    own tool_filter, so the child sees a handful of tools instead of a domain's
    whole surface.
    """
    from app.agents.registry import get_agent, registered_domains
    from app.db.config_models import Playbook

    raw = (target or "").strip()
    if not raw:
        raise DelegationError("target is required")

    slug, _, playbook_slug = raw.partition("/")
    slug = slug.strip()

    if not get_agent(slug):
        known = ", ".join(sorted(registered_domains()))
        raise DelegationError(f"Unknown agent '{slug}'. Available: {known}")

    playbook = None
    if playbook_slug:
        playbook = (
            config_db.query(Playbook)
            .filter(
                Playbook.slug == playbook_slug.strip(),
                Playbook.agent_slug == slug,
                Playbook.enabled == True,  # noqa: E712
            )
            .first()
        )
        if not playbook:
            raise DelegationError(
                f"Unknown playbook '{playbook_slug}' for agent '{slug}'"
            )
    return slug, playbook


def check_guards(parent, target_slug: str, db: Session) -> None:
    """Refuse a delegation that would loop or run away. Raises DelegationError."""
    chain = _chain(parent, db)

    if (parent.delegation_depth or 0) + 1 > MAX_DELEGATION_DEPTH:
        raise DelegationError(
            f"Delegation depth limit ({MAX_DELEGATION_DEPTH}) reached — "
            "answer with what you have rather than consulting further."
        )

    # A -> B -> A is a loop dressed up as a question. Compare against every
    # domain already in the chain, including the caller itself.
    domains = [t.domain for t in chain if t.domain]
    if target_slug in domains:
        raise DelegationError(
            f"'{target_slug}' is already in this delegation chain "
            f"({' -> '.join(reversed(domains))}) — that would loop."
        )

    root = chain[-1]
    if _delegation_count(root, db) >= MAX_DELEGATIONS_PER_ROOT:
        raise DelegationError(
            f"This conversation has already used its {MAX_DELEGATIONS_PER_ROOT} "
            "delegations."
        )


def build_child_tools(
    agent, parent_depth: int, db: Session, config_db: Session, user_id, playbook
) -> tuple[str, list[dict]]:
    """The child's prompt and its (read-only) tool set."""
    system_prompt, tools = agent.get_tool_definitions(
        db,
        user_id=user_id,
        config_db=config_db,
        tool_filter=(playbook.tool_filter if playbook else None),
    )
    tools = filter_to_read_only(tools, config_db)
    if not system_prompt:
        system_prompt = f"You are the {agent.domain} agent for Norm."

    system_prompt += (
        "\n\n## You are being consulted by another agent\n"
        "Another Norm agent has asked you the question below. Answer it directly "
        "and completely from the data you can read — state the numbers and the "
        "period they cover. You cannot change anything, and there is no user to "
        "ask, so if something is ambiguous say what you assumed and answer "
        "anyway. Reply with the answer itself, not a preamble."
    )
    return system_prompt, tools


# ---------------------------------------------------------------------------
# Running the child
# ---------------------------------------------------------------------------


class _SuppressChildEvents:
    """Keep the child's tool loop off the parent's SSE stream.

    The child runs on the parent's thread, so it inherits
    `tool_loop._thread_local.event_callback` and would stream its own thinking
    steps into the parent's UI — contradicting "the parent shows one tool chip".
    Swap the callback out for the duration and put it back afterwards.
    """

    def __init__(self):
        self._saved = None

    def __enter__(self):
        from app.agents import tool_loop

        self._saved = getattr(tool_loop._thread_local, "event_callback", None)
        tool_loop._thread_local.event_callback = None
        return self

    def __exit__(self, *exc):
        from app.agents import tool_loop

        tool_loop._thread_local.event_callback = self._saved
        return False


def delegate(
    parent,
    target: str,
    question: str,
    context: str | None,
    db: Session,
    config_db: Session,
) -> dict:
    """Run `target` against `question` in an isolated child thread.

    Returns {"summary", "agent", "child_thread_id"}. Raises DelegationError for
    anything the caller should be told about rather than crash on.
    """
    from app.agents.registry import get_agent
    from app.agents.tool_loop import run_tool_loop
    from app.db.models import Message, Thread

    target_slug, playbook = resolve_target(target, config_db)
    check_guards(parent, target_slug, db)

    agent = get_agent(target_slug)
    depth = (parent.delegation_depth or 0) + 1

    system_prompt, tools = build_child_tools(
        agent, parent.delegation_depth or 0, db, config_db, parent.user_id, playbook
    )
    if not tools:
        raise DelegationError(
            f"'{target_slug}' has no read-only tools available, so it cannot be "
            "consulted."
        )

    child = Thread(
        user_id=parent.user_id,
        venue_id=parent.venue_id,
        domain=target_slug,
        intent=f"{target_slug}.delegated",
        status="in_progress",
        raw_prompt=question,
        title=f"[Consulted] {question[:60]}",
        extracted_fields={},
        missing_fields=[],
        parent_thread_id=parent.id,
        delegation_depth=depth,
        playbook_id=playbook.id if playbook else None,
        tags=["delegated"],
    )
    db.add(child)
    db.flush()

    # The child's entire context: the question, plus whatever the parent chose
    # to pass down. No parent history — that isolation is the point.
    brief = question if not context else f"{question}\n\n[Known already]\n{context}"
    db.add(Message(thread_id=child.id, role="user", content=brief))
    db.flush()

    logger.info(
        "Delegating: %s -> %s (depth %d, %d read-only tools)",
        parent.domain,
        target_slug,
        depth,
        len(tools),
    )

    with _SuppressChildEvents():
        result = run_tool_loop(
            brief,
            child,
            db,
            system_prompt,
            tools,
            config_db=config_db,
            messages_override=[{"role": "user", "content": brief}],
            max_iterations=CHILD_MAX_ITERATIONS,
        )

    # Only the final message crosses back. _build_response carries every tool
    # result on the child thread, which would blow past MAX_TOOL_RESULT_CHARS
    # and undo the context saving that makes delegation worth doing.
    summary = (result or {}).get("message") or ""
    if not summary.strip():
        summary = f"The {target_slug} agent returned no answer."

    return {
        "summary": summary,
        "agent": target_slug,
        "playbook": playbook.slug if playbook else None,
        "child_thread_id": child.id,
    }
