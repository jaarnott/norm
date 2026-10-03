"""Approving the writes an agent turn proposes — who may, and what follows.

The agent loop suspends a turn on any write that needs a human
(``tool_loop._execute_loop``: the thread goes ``awaiting_tool_approval`` and a
``tool_approval`` card names the pending calls). This module is everything that
happens to that suspended turn afterwards:

- **Who may decide.** Only the person whose conversation it is, or a platform
  admin. Until Oct 2026 approve/reject took any signed-in user and any thread id
  — another organisation's pending stock write included.
- **Exactly once.** Deciding claims the thread with a conditional update, so a
  double click (or two tabs) cannot run the same write twice.
- **Only at venues the decider can act on.** The model names the venue; an
  approved call aimed at a venue the approver has no access to is declined, not
  run.
- **Superseded by a new message.** Writing a new message instead of deciding
  declines the pending calls and says so in the conversation. Before this, the
  old card stayed live and approving it resumed a transcript the user had
  moved past — or, after a second suspension, approved the new set while the
  first sat ``pending_approval`` for ever (44 such stock writes in production,
  2 Oct 2026).
- **Scheduled runs.** A run that suspends lives on a hidden execution thread
  and its card is copied into the task's conversation. The card names its own
  thread; deciding there updates the copy and posts the outcome back into the
  conversation, which is where the owner reads it.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import Approval, Message, Thread, ToolCall, User

logger = logging.getLogger(__name__)

AWAITING = "awaiting_tool_approval"

#: Intents of threads run by the agent tool loop (chat, task conversations,
#: scheduled runs, Claude workflow runs). Anything else is a legacy domain
#: thread with its own approve/submit flow.
_TOOL_LOOP_SUFFIXES = (
    ".tool_use",
    ".automated_conversation",
    ".automated_task",
    ".mcp_playbook",
)

SUPERSEDED_NOTE = (
    "_Not done — a new message arrived before these changes were approved, "
    "so nothing was changed._"
)


#: Writes that run without a card on rows that don't declare an ``approval``
#: yet. Once labelled, ``approval.default == "auto"`` says the same thing.
FALLBACK_AUTO = frozenset({("norm", "remember")})

#: What the loop does with a call: ``run`` it now (reads, drafts, and tiered
#: writes, which apply the person's level themselves), ``auto``-approve it
#: (a write that doesn't ask), or ``ask`` first (the approval card).
Gate = str


def gate(row: dict | None, method: str | None, connector: str, action: str) -> Gate:
    """Whether a tool call runs now, runs as an auto-approved write, or asks.

    Decided by the tool's declared ``effect``. A row that doesn't declare one
    keeps the old rule, so nothing changes until a tool is labelled: a GET
    runs, ``remember`` is auto-approved, anything else asks.
    """
    from app.connectors import spec_rows

    eff = spec_rows.effect(row)
    if eff is None:
        if (method or "POST").upper() == "GET":
            return "run"
        return "auto" if (connector, action) in FALLBACK_AUTO else "ask"
    if eff in ("read", "draft"):
        return "run"
    policy = spec_rows.approval(row)
    if policy.get("levels"):
        return "run"
    return "auto" if policy.get("default") == "auto" else "ask"


def is_tiered(row: dict | None) -> bool:
    from app.connectors import spec_rows

    return spec_rows.effect(row) == "write" and bool(
        spec_rows.approval(row).get("levels")
    )


def lowest_level(row: dict | None) -> str | None:
    """A tiered tool's first level — the one that writes nothing."""
    from app.connectors import spec_rows

    levels = spec_rows.approval(row).get("levels") or []
    first = levels[0] if levels and isinstance(levels[0], dict) else None
    return first.get("id") if first else None


class AlreadyDecided(Exception):
    """The thread is no longer waiting — someone (or another click) got there first."""


def actionable_thread(db: Session, thread_id: str, user: User) -> Thread | None:
    """The thread, if ``user`` may act on it (services/thread_access.py).

    None both when the thread doesn't exist and when it isn't theirs, so a
    caller can't tell another person's thread id from a made-up one.
    """
    from app.services.thread_access import thread_for

    return thread_for(db, thread_id, user)


def is_tool_loop_thread(thread: Thread) -> bool:
    return bool(thread.intent) and thread.intent.endswith(_TOOL_LOOP_SUFFIXES)


def claim(db: Session, thread: Thread) -> None:
    """Take the decision on a waiting thread, or raise AlreadyDecided.

    One conditional UPDATE: a second, concurrent decision blocks on the row lock
    until the first commits, then matches no row. The status is set to
    ``in_progress`` here; ``tool_loop.resume_tool_loop`` keeps it there.
    """
    claimed = (
        db.query(Thread)
        .filter(Thread.id == thread.id, Thread.status == AWAITING)
        .update({Thread.status: "in_progress"}, synchronize_session=False)
    )
    if claimed != 1:
        raise AlreadyDecided()
    thread.status = "in_progress"


def _pending_calls(db: Session, thread: Thread) -> list[ToolCall]:
    ids = thread.pending_tool_call_ids or []
    if not ids:
        return []
    rows = db.query(ToolCall).filter(ToolCall.id.in_(ids)).all()
    by_id = {tc.id: tc for tc in rows}
    return [by_id[i] for i in ids if i in by_id]


def _venue_of_call(db: Session, tc: ToolCall) -> tuple[str | None, str | None]:
    """(venue_id, venue_name) the call names, resolved under the thread's org."""
    from app.services import caller_scope
    from app.services.venue_service import resolve_venue_id

    params = tc.input_params or {}
    venue_id = params.get("venue_id") or None
    name = params.get("venue") or params.get("venue_name")
    name = name.strip() if isinstance(name, str) and name.strip() else None
    if not venue_id and name:
        with caller_scope.use(caller_scope.for_thread(db, tc.thread_id)):
            venue_id = resolve_venue_id(name, db)
    return venue_id, name


def decide(
    db: Session,
    thread: Thread,
    user: User,
    approve: bool,
    notes: str | None = None,
) -> None:
    """Record the decision on every pending call. Call ``claim`` first.

    Approving marks each call ``approved`` — except one aimed at a venue the
    approver can't access, which is declined with the reason so the model can
    tell the user. Rejecting marks them all ``rejected``. The card (and any copy
    of it in a task conversation) shows the outcome, and an Approval row records
    who decided.
    """
    from app.services.venue_service import user_can_access_venue

    is_admin = getattr(user, "role", None) == "admin"
    calls = _pending_calls(db, thread)
    for tc in calls:
        if tc.status != "pending_approval":
            continue
        if not approve:
            tc.status = "rejected"
            continue
        venue_id, name = _venue_of_call(db, tc)
        if (
            venue_id
            and not is_admin
            and not user_can_access_venue(db, user.id, venue_id)
        ):
            tc.status = "rejected"
            tc.error_message = (
                f"Not run: {user.email} doesn't have access to "
                f"{name or 'that venue'}, so this change was declined."
            )
            continue
        tc.status = "approved"

    set_card_status(
        db, thread, [tc.id for tc in calls], "approved" if approve else "rejected"
    )
    db.add(
        Approval(
            thread_id=thread.id,
            action="tool_calls_approved" if approve else "tool_calls_rejected",
            performed_by=user.email,
            user_id=user.id,
            notes=notes or None,
        )
    )
    db.flush()


def supersede(db: Session, thread: Thread, user_id: str | None) -> bool:
    """Decline a waiting thread's pending calls because a new message arrived.

    Returns True if there was anything to supersede. Does not commit — the
    caller commits it together with the new message.
    """
    if thread.status != AWAITING:
        return False
    calls = _pending_calls(db, thread)
    for tc in calls:
        if tc.status == "pending_approval":
            tc.status = "superseded"
    messages = set_card_status(db, thread, [tc.id for tc in calls], "superseded")
    # The model reads its history from Message rows, not the discarded loop
    # state — so the note goes on the proposal itself, and the next turn knows
    # nothing was done.
    for msg in messages:
        if msg.thread_id == thread.id and SUPERSEDED_NOTE not in (msg.content or ""):
            msg.content = f"{(msg.content or '').rstrip()}\n\n{SUPERSEDED_NOTE}"
    thread.pending_tool_call_ids = None
    thread.agent_loop_state = None
    thread.status = "in_progress"
    user = db.query(User).filter(User.id == user_id).first() if user_id else None
    db.add(
        Approval(
            thread_id=thread.id,
            action="tool_calls_superseded",
            performed_by=user.email if user else "system",
            user_id=user_id,
        )
    )
    return True


def _task_conversation_id(db: Session, thread: Thread) -> str | None:
    """The task conversation a scheduled run's thread posts into, if any."""
    if not (thread.intent or "").endswith(".automated_task"):
        return None
    from app.db.models import AutomatedTask, AutomatedTaskRun

    run = (
        db.query(AutomatedTaskRun)
        .filter(AutomatedTaskRun.thread_id == thread.id)
        .first()
    )
    if run is None:
        return None
    task = (
        db.query(AutomatedTask)
        .filter(AutomatedTask.id == run.automated_task_id)
        .first()
    )
    return task.conversation_thread_id if task else None


def set_card_status(
    db: Session, thread: Thread, tool_call_ids: list[str], status: str
) -> list[Message]:
    """Set the status of the pending card(s) naming these calls.

    Only cards still ``pending`` that name one of ``tool_call_ids`` change — an
    earlier, superseded card keeps saying so. Looks in the thread and, for a
    scheduled run, in the task conversation it was copied into. Returns the
    messages whose card changed.
    """
    wanted = set(tool_call_ids or [])
    if not wanted:
        return []
    thread_ids = [thread.id]
    conversation_id = _task_conversation_id(db, thread)
    if conversation_id:
        thread_ids.append(conversation_id)
    changed: list[Message] = []
    for msg in (
        db.query(Message)
        .filter(Message.thread_id.in_(thread_ids), Message.display_blocks.isnot(None))
        .all()
    ):
        blocks = list(msg.display_blocks or [])
        touched = False
        for block in blocks:
            if block.get("component") != "tool_approval":
                continue
            data = block.get("data") or {}
            if data.get("status", "pending") != "pending":
                continue
            ids = {c.get("id") for c in data.get("tool_calls") or []}
            if ids & wanted:
                data["status"] = status
                block["data"] = data
                touched = True
        if touched:
            msg.display_blocks = blocks
            flag_modified(msg, "display_blocks")
            changed.append(msg)
    return changed


def post_outcome_to_task_conversation(db: Session, thread: Thread) -> None:
    """After a scheduled run's approval resumes, show the result where the owner reads.

    The run's thread is hidden from the thread list; its card was copied into
    the task conversation. Best-effort — it must never fail the decision.
    """
    try:
        conversation_id = _task_conversation_id(db, thread)
        if not conversation_id:
            return
        last = (
            db.query(Message)
            .filter(Message.thread_id == thread.id, Message.role == "assistant")
            .order_by(Message.created_at.desc())
            .first()
        )
        if last is None or not (last.content or "").strip():
            return
        db.add(
            Message(
                thread_id=conversation_id,
                role="assistant",
                content=last.content,
                display_blocks=last.display_blocks or None,
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001 — logging the outcome must not undo it
        db.rollback()
        logger.exception("Could not post approval outcome for %s", thread.id[:12])
