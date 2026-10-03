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


def gate(
    row: dict | None,
    method: str | None,
    connector: str,
    action: str,
    user: User | None = None,
) -> Gate:
    """Whether a tool call runs now, runs as an auto-approved write, or asks.

    Decided by the tool's declared ``effect`` — and, for a write that asks, by
    whether ``user`` always allows it. A row that doesn't declare an effect
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
    if policy.get("default") == "auto":
        return "auto"
    return "auto" if always_allowed(user, f"{connector}.{action}", row) else "ask"


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
    tool_call_ids: list[str] | None = None,
) -> None:
    """Record the decision on every pending call. Call ``claim`` first.

    Approving marks each call ``approved`` — except one the person left
    unticked (``tool_call_ids`` names the ticked ones; None means all), and one
    aimed at a venue the approver can't access, which is declined with the
    reason so the model can tell the user. Rejecting marks them all
    ``rejected``. The card (and any copy of it in a task conversation) shows
    the outcome per row, and an Approval row records who decided.
    """
    from app.services.venue_service import user_can_access_venue

    is_admin = getattr(user, "role", None) == "admin"
    ticked = set(tool_call_ids) if tool_call_ids is not None else None
    calls = _pending_calls(db, thread)
    for tc in calls:
        if tc.status != "pending_approval":
            continue
        if not approve:
            tc.status = "rejected"
            if notes:
                tc.error_message = f"Declined by {user.email}: {notes}"
            continue
        if ticked is not None and tc.id not in ticked:
            tc.status = "rejected"
            tc.error_message = (
                f"Not done: {user.email} approved the others but left this one out."
            )
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
        db,
        thread,
        [tc.id for tc in calls],
        "approved"
        if approve and any(tc.status == "approved" for tc in calls)
        else "rejected",
        call_status={tc.id: tc.status for tc in calls},
    )
    action = "tool_calls_approved" if approve else "tool_calls_rejected"
    if approve and ticked is not None and any(tc.status == "rejected" for tc in calls):
        approved_some = any(tc.status == "approved" for tc in calls)
        action = (
            "tool_calls_partly_approved" if approved_some else "tool_calls_rejected"
        )
    db.add(
        Approval(
            thread_id=thread.id,
            action=action,
            performed_by=user.email,
            user_id=user.id,
            notes=notes or None,
        )
    )
    db.flush()


def supersede(
    db: Session,
    thread: Thread,
    user_id: str | None,
    *,
    note: str = SUPERSEDED_NOTE,
    card_note: str | None = None,
    status_after: str = "in_progress",
) -> bool:
    """Decline a waiting thread's pending calls because something newer came.

    In a conversation, a new message (the defaults). For a scheduled run, a
    newer run of the same task (``supersede_waiting_runs``). Returns True if
    there was anything to supersede. Does not commit — the caller commits it
    with whatever replaced it.
    """
    if thread.status != AWAITING:
        return False
    calls = _pending_calls(db, thread)
    for tc in calls:
        if tc.status == "pending_approval":
            tc.status = "superseded"
    messages = set_card_status(
        db, thread, [tc.id for tc in calls], "superseded", note=card_note
    )
    # The model reads its history from Message rows, not the discarded loop
    # state — so the note goes on the proposal itself, and the next turn knows
    # nothing was done.
    for msg in messages:
        if msg.thread_id == thread.id and note not in (msg.content or ""):
            msg.content = f"{(msg.content or '').rstrip()}\n\n{note}"
    thread.pending_tool_call_ids = None
    thread.agent_loop_state = None
    thread.status = status_after
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


def _update_cards(
    db: Session, thread: Thread, tool_call_ids: list[str], change, only_pending: bool
) -> list[Message]:
    """Apply ``change(data)`` to each approval card naming these calls.

    Looks in the thread and, for a scheduled run, in the task conversation it
    was copied into. Returns the messages whose card changed.
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
            if only_pending and data.get("status", "pending") != "pending":
                continue
            ids = {c.get("id") for c in data.get("tool_calls") or []}
            if ids & wanted:
                change(data)
                block["data"] = data
                touched = True
        if touched:
            msg.display_blocks = blocks
            flag_modified(msg, "display_blocks")
            changed.append(msg)
    return changed


def set_card_status(
    db: Session,
    thread: Thread,
    tool_call_ids: list[str],
    status: str,
    call_status: dict[str, str] | None = None,
    note: str | None = None,
) -> list[Message]:
    """Set the status of the pending card(s) naming these calls, and of each row.

    Only cards still ``pending`` change — an earlier, superseded card keeps
    saying so. ``note`` says why, where the status alone doesn't ("a newer
    run replaced it").
    """

    def change(data: dict) -> None:
        data["status"] = status
        if note:
            data["status_note"] = note
        for entry in data.get("tool_calls") or []:
            entry["status"] = (call_status or {}).get(entry.get("id"), status)

    return _update_cards(db, thread, tool_call_ids, change, only_pending=True)


def set_call_outcomes(db: Session, thread: Thread, outcomes: dict[str, dict]) -> None:
    """After the approved calls ran: what happened to each row of the card.

    ``outcomes`` maps a call id to {"outcome": done | failed | changed,
    "note": str | None}. The card said "Approved" for the whole set; a row whose
    write then failed, or found its data changed, says so.
    """

    def change(data: dict) -> None:
        for entry in data.get("tool_calls") or []:
            if entry.get("id") in outcomes:
                entry.update(outcomes[entry["id"]])

    _update_cards(db, thread, list(outcomes), change, only_pending=False)


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


# ---------------------------------------------------------------------------
# Preferences: what Norm may do without asking this person
# ---------------------------------------------------------------------------
#
# One store, ``User.approval_preferences``, keyed by write tool
# ("connector.action"). It replaced a personal run mode per workflow
# (``users.workflow_modes``) and, for receiving only, a per-venue ladder
# (``venues.invoice_autopilot``) — the same question with two homes. Anyone
# sets their own; nobody sets someone else's.
#
#   {"loadedhub.manage_stock_item": {"always": true},
#    "loadedhub.review_and_receive_invoices":
#        {"level": "autopilot", "options": {"auto_strike_phantom_lines": true}},
#    "loadedhub.reconcile_received_invoices": {"level": "approve_fixes"}}

#: The levels both invoice tools share, least to most trusting.
LEVELS = ("approve_all", "approve_fixes", "autopilot")
RECEIVE_KEY = "loadedhub.review_and_receive_invoices"
RECONCILE_KEY = "loadedhub.reconcile_received_invoices"
#: Consolidator action -> its preference key, for the engine, which runs a
#: consolidator knowing only its action.
LEVELLED_ACTIONS = {
    "review_and_receive_invoices": RECEIVE_KEY,
    "reconcile_received_invoices": RECONCILE_KEY,
}


def _stored(user: User | None) -> dict:
    prefs = getattr(user, "approval_preferences", None)
    return prefs if isinstance(prefs, dict) else {}


def preference(user: User | None, key: str) -> dict:
    entry = _stored(user).get(key)
    return entry if isinstance(entry, dict) else {}


def always_allowed(user: User | None, key: str, row: dict | None) -> bool:
    """Does this person let Norm run this write without asking?

    Only when the tool allows it at all: changing an approval preference
    always asks, so Norm can never raise its own autonomy silently.
    """
    from app.connectors import spec_rows

    if user is None or not spec_rows.approval(row).get("allow_auto"):
        return False
    return preference(user, key).get("always") is True


def level(user: User | None, key: str) -> str:
    """This person's level for a tool with levels; the first (writes nothing)
    when they haven't chosen one."""
    chosen = preference(user, key).get("level")
    return chosen if chosen in LEVELS else LEVELS[0]


def at_most(chosen: str, ceiling: str | None) -> str:
    """The lower of two levels. A run may ask for LESS than the person allows
    (a task's own setting, the model's mode), never more."""
    chosen = chosen if chosen in LEVELS else LEVELS[0]
    if ceiling not in LEVELS:
        return chosen
    return LEVELS[min(LEVELS.index(chosen), LEVELS.index(ceiling))]


def receiving_settings(user: User | None) -> dict:
    """The receiving policy for this person, in the shape the review service
    reads: ``{"mode": level, <switch>: bool, ...}``. No person — a background
    review — is the safest policy."""
    from app.services import venue_autopilot as VA

    entry = preference(user, RECEIVE_KEY)
    return VA.settings_from(
        {"mode": level(user, RECEIVE_KEY), **(entry.get("options") or {})}
    )


def receiving_settings_for_thread(db: Session, thread_id: str | None) -> dict:
    thread = (
        db.query(Thread).filter(Thread.id == thread_id).first() if thread_id else None
    )
    user = (
        db.query(User).filter(User.id == thread.user_id).first()
        if thread is not None and thread.user_id
        else None
    )
    return receiving_settings(user)


def validate(row: dict | None, value) -> dict:
    """A preference value for ``row``'s tool, checked against its policy.

    ``"ask"`` / ``"always"`` for an ordinary write; a level id, or
    ``{"level", "options"}``, for a tool with levels. Raises ValueError.
    """
    from app.connectors import spec_rows

    if spec_rows.effect(row) != "write":
        raise ValueError("only a tool that makes changes has an approval preference")
    policy = spec_rows.approval(row)
    if policy.get("levels"):
        if isinstance(value, str):
            value = {"level": value}
        if not isinstance(value, dict):
            raise ValueError("choose a level")
        ids = [lv.get("id") for lv in policy["levels"] if isinstance(lv, dict)]
        if value.get("level") not in ids:
            raise ValueError(f"level must be one of {', '.join(ids)}")
        known = {
            o.get("id") for o in policy.get("options") or [] if isinstance(o, dict)
        }
        options = value.get("options") or {}
        if not isinstance(options, dict) or set(options) - known:
            raise ValueError(
                "unknown switch: " + ", ".join(sorted(set(options) - known))
            )
        return {
            "level": value["level"],
            "options": {k: True for k, v in options.items() if v is True},
        }
    if policy.get("default") == "auto":
        raise ValueError("Norm does this on its own; there is nothing to choose")
    if value not in ("ask", "always"):
        raise ValueError("choose ask or always")
    if value == "always" and not policy.get("allow_auto"):
        raise ValueError("this one always asks — Norm can't be told to skip it")
    return {"always": value == "always"}


def set_preference(
    db: Session, user: User, key: str, row: dict | None, value, *, via: str
) -> dict:
    """Save one preference for ``user`` and record who changed what, where.

    ``via`` says where it was set: "settings", "card" or "chat".
    """
    clean = validate(row, value)
    prefs = dict(_stored(user))
    before = prefs.get(key)
    if clean == {"always": False}:
        prefs.pop(key, None)  # asking is the default; store nothing
    else:
        prefs[key] = clean
    user.approval_preferences = prefs
    flag_modified(user, "approval_preferences")
    db.add(
        Approval(
            thread_id=None,
            action="approval_preference_set",
            performed_by=user.email,
            user_id=user.id,
            notes=f"{key}: {before} -> {clean} (via {via})",
        )
    )
    db.flush()
    return clean


def describe(user: User | None, key: str, row: dict | None) -> str:
    """This person's setting for a tool, in words."""
    return describe_entry(row, preference(user, key))


def describe_entry(row: dict | None, entry: dict | None) -> str:
    """A stored preference entry for ``row``'s tool, in words."""
    from app.connectors import spec_rows

    entry = entry if isinstance(entry, dict) else {}
    policy = spec_rows.approval(row)
    if policy.get("levels"):
        chosen = entry.get("level") if entry.get("level") in LEVELS else LEVELS[0]
        named = {lv.get("id"): lv.get("label") for lv in policy["levels"]}
        text = named.get(chosen) or chosen
        on = sorted(k for k, v in (entry.get("options") or {}).items() if v is True)
        labels = {o.get("id"): o.get("label") for o in policy.get("options") or []}
        if on and chosen == LEVELS[-1]:
            text += " — may also " + "; ".join(labels.get(o, o) for o in on)
        return text
    if policy.get("default") == "auto":
        return "does it without asking"
    if entry.get("always") is True and policy.get("allow_auto"):
        return "always allowed — no card"
    return "asks first"


def catalog(config_db: Session, apps: set[str] | None = None) -> list[dict]:
    """Every write a person can set a preference for, as ``{key, row, app}``.

    Tools an App claims (exposure is the App Map), labelled ``write``, that
    ask — the automatic ones (remember, a task's summary) have nothing to
    choose. ``apps`` narrows it to the Apps on for someone's organisation.
    """
    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec
    from app.services.entitlements import tool_owners

    owners = tool_owners(config_db)
    out = []
    for spec in config_db.query(ConnectionSpec).all():
        for row in spec_rows.tools(spec):
            key = f"{spec.connector_name}.{row.get('action')}"
            app = owners.get(key)
            if app is None or (apps is not None and app not in apps):
                continue
            if spec_rows.effect(row) != "write":
                continue
            if spec_rows.approval(row).get("default") == "auto":
                continue
            out.append({"key": key, "row": row, "app": app})

    def order(e):
        policy = e["row"].get("approval") or {}
        # Levels first, then the writes a person can change, by name; the
        # ones that always ask last.
        return (
            not policy.get("levels"),
            not policy.get("allow_auto"),
            str(policy.get("label") or e["key"]),
        )

    return sorted(out, key=order)


def find(entries: list[dict], name: str | None) -> dict | None:
    """The catalog entry ``name`` means: its key, its action, or its label."""
    wanted = str(name or "").strip().lower()
    if not wanted:
        return None
    for e in entries:
        label = str((e["row"].get("approval") or {}).get("label") or "").lower()
        if wanted in (e["key"].lower(), str(e["row"].get("action")).lower(), label):
            return e
    return None


def record_always_allowed(
    db: Session, thread: Thread, user: User | None, tc: ToolCall
) -> None:
    """A write that ran without a card because its owner always allows it."""
    db.add(
        Approval(
            thread_id=thread.id,
            action="tool_call_always_allowed",
            performed_by=getattr(user, "email", None) or "system",
            user_id=getattr(user, "id", None),
            notes=f"{tc.connector_name}.{tc.action} ({tc.id})",
        )
    )
    db.flush()


def always_allow_from_card(
    db: Session, config_db: Session, thread: Thread, user: User, keys: list[str]
) -> list[str]:
    """ "Always allow" ticked on a card: save it for the person who approved.

    Only for tools on this card (a card can't switch on anything else), and
    only where the tool allows it. Returns the keys saved.
    """
    from app.agents.tool_loop import _find_tool_def

    on_card = {f"{tc.connector_name}.{tc.action}" for tc in _pending_calls(db, thread)}
    saved = []
    for key in keys or []:
        if key not in on_card:
            continue
        connector, _, action = key.partition(".")
        row = _find_tool_def(connector, action, db, config_db=config_db)
        try:
            set_preference(db, user, key, row, "always", via="card")
        except ValueError:
            continue
        saved.append(key)
    return saved


# ---------------------------------------------------------------------------
# Scheduled runs that wait for a person
# ---------------------------------------------------------------------------
#
# A scheduled run that reaches a write which asks has nobody watching. Until
# Oct 2026 it suspended silently and was recorded as a "success": 20 runs in
# production sat waiting for weeks, their cards copied into conversations
# nobody opened. Now the run says it is waiting, its owner is emailed, the
# task board shows it, and the next run of the same task replaces it.

WAITING = "waiting_for_approval"
RUN_SUPERSEDED_NOTE = (
    "_Not done — a newer run of this task replaced these changes before they "
    "were approved, so nothing was changed._"
)


def _run_for(db: Session, thread: Thread):
    from app.db.models import AutomatedTaskRun

    return (
        db.query(AutomatedTaskRun)
        .filter(AutomatedTaskRun.thread_id == thread.id)
        .first()
    )


def waiting_runs(db: Session, task_id: str) -> list:
    from app.db.models import AutomatedTaskRun

    return (
        db.query(AutomatedTaskRun)
        .filter(
            AutomatedTaskRun.automated_task_id == task_id,
            AutomatedTaskRun.status == WAITING,
        )
        .all()
    )


def supersede_waiting_runs(db: Session, task_id: str) -> int:
    """A new run of a task replaces any earlier run still waiting for approval:
    its proposals were made against data the new run reads afresh."""
    count = 0
    for run in waiting_runs(db, task_id):
        thread = db.query(Thread).filter(Thread.id == run.thread_id).first()
        if thread is not None:
            supersede(
                db,
                thread,
                None,
                note=RUN_SUPERSEDED_NOTE,
                card_note="a newer run replaced it",
                status_after="completed",
            )
        run.status = "superseded"
        count += 1
    if count:
        db.flush()
    return count


def settle_task_run(db: Session, thread: Thread) -> None:
    """After a waiting run's changes are decided: what the run came to.

    ``success`` when anything was approved, ``declined`` when nothing was.
    A run that stopped at ANOTHER card on resuming stays waiting.
    """
    import datetime as _dt

    run = _run_for(db, thread)
    if run is None or run.status != WAITING or thread.status == AWAITING:
        return
    calls = (
        db.query(ToolCall).filter(ToolCall.thread_id == thread.id).all()
        if thread.id
        else []
    )
    approved = any(
        tc.status in ("approved", "executed", "failed") for tc in calls if tc.preview
    )
    run.status = "success" if approved else "declined"
    run.completed_at = _dt.datetime.now(_dt.timezone.utc)
    last = (
        db.query(Message)
        .filter(Message.thread_id == thread.id, Message.role == "assistant")
        .order_by(Message.created_at.desc())
        .first()
    )
    if last is not None and last.content:
        run.result_summary = last.content[:2000]
    db.flush()


def notify_task_waiting(db: Session, task, run, thread: Thread) -> str | None:
    """Email a scheduled task's owner that a run is waiting for them.

    Best-effort: the run is recorded as waiting whether or not this sends.
    Returns the email log id.
    """
    from app.mcp.links import thread_link
    from app.services.email_service import send_system_email

    try:
        owner = db.query(User).filter(User.id == task.created_by).first()
        if owner is None or not owner.email:
            return None
        calls = _pending_calls(db, thread)
        rows = []
        for tc in calls:
            shown = tc.preview or {}
            title = shown.get("title") or tc.action.replace("_", " ")
            rows.append(f"{title}: {shown['target']}" if shown.get("target") else title)
        target = task.conversation_thread_id or thread.id
        return send_system_email(
            "task_waiting",
            [owner.email],
            {
                "user_name": (owner.full_name or "").split(" ")[0] or "there",
                "task_title": task.title,
                "count": len(rows),
                "changes": rows[:10],
                "more": max(0, len(rows) - 10),
                "link": thread_link(target),
            },
            db,
            thread_id=thread.id,
        )
    except Exception:  # noqa: BLE001 — telling them must never undo the run
        logger.exception("Could not email the owner of task %s", str(task.id)[:12])
        return None
