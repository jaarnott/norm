"""Supervisor — takes a user's message to the Norm agent.

There is no routing step. Until Sep 2026 a Haiku router classified every new
message to one of seven domain agents and asked again on every follow-up
whether to switch. Once Apps v3 gave every conversation the full entitled tool
union and one Norm prompt, that choice changed only a label and a tone line:
in the 30 days before it went, 50 of 51 follow-up verdicts were "continue"
(~1.6s each, for nothing), and a cross-App question ("the chefs at La Zeppa and
the open kitchen roles") scored "unknown" and got a capability menu instead of
an answer. What the router still did that mattered — naming the venue and the
thread — is done here without a model in front of the agent.

1. A follow-up stays in its thread and goes to the Norm agent, always. An
   automated task's conversation keeps its task identity; its "Run Now" runs
   the task's prompt under the task's own tool scope.
2. A new message: a connect request shows the connect card; anything else goes
   to the Norm agent, with the venue taken from the message when it names one
   of the user's venues, and a title drafted while the agent works.
"""

import logging
import re

from sqlalchemy.orm import Session

from app.agents.registry import norm_agent
from app.db.models import Message, Thread, Venue

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
    """Process a user message: continue its thread, or start one."""
    _cdb = config_db
    if _cdb is None:
        raise RuntimeError(
            "config_db is required — check that config_db is passed through the call chain"
        )

    # Quota gate — block before any LLM call if tokens exhausted
    from app.services.billing_service import check_quota_for_user

    check_quota_for_user(db, user_id)

    thread = (
        db.query(Thread).filter(Thread.id == thread_id).first() if thread_id else None
    )
    if thread is not None:
        return _continue_thread(message, thread, db, _cdb, user_id, page_context)
    return _new_thread(message, db, _cdb, user_id, venue_id, page_context)


# ------------------------------------------------------------ follow-ups ----


def _continue_thread(
    message: str,
    thread: Thread,
    db: Session,
    config_db: Session,
    user_id: str | None,
    page_context: dict | None,
) -> dict:
    """A follow-up. It stays in this thread, whatever it says — there is no
    other agent to hand it to, and the conversation (a pending approval, an
    automated task's identity, the venue) lives on this row."""
    venue_id = thread.venue_id
    venue = db.query(Venue).filter(Venue.id == venue_id).first() if venue_id else None

    # An automated task's conversation is ABOUT that task. Resolve it up front
    # so every message in the thread keeps that identity — without it the agent
    # has no task id, so "add my email to this" reaches for create_automated_task
    # and silently leaves a duplicate draft while the real task is unchanged.
    automated_task_ctx = None
    if thread.intent and thread.intent.endswith(".automated_conversation"):
        from app.db.models import AutomatedTask

        at = (
            db.query(AutomatedTask)
            .filter(AutomatedTask.conversation_thread_id == thread.id)
            .first()
        )
        if at:
            if " ".join(message.split()) == " ".join((at.prompt or "").split()):
                return _run_task_now(message, thread, at, venue, db, config_db, user_id)
            schedule = at.schedule_type or "manual"
            cfg = at.schedule_config or {}
            if cfg.get("hour") is not None:
                schedule += (
                    f" at {int(cfg['hour']):02d}:{int(cfg.get('minute') or 0):02d}"
                )
            automated_task_ctx = {
                "id": at.id,
                "title": at.title,
                "prompt": at.prompt,
                "status": at.status,
                "schedule": schedule,
            }

    if thread.intent == "venue_clarification":
        # A venue picker left open by the retired router: the answer is just
        # the next message of an ordinary conversation now. Re-label the
        # thread so it serialises as one.
        thread.intent = f"{thread.domain}.tool_use"
        thread.status = "in_progress"
        db.flush()

    return norm_agent().handle_message(
        message,
        db,
        user_id,
        thread.id,
        venue_id=venue_id,
        venue_name=venue.name if venue else None,
        venue_timezone=venue.timezone if venue else None,
        config_db=config_db,
        page_context=page_context,
        automated_task=automated_task_ctx,
    )


def _run_task_now(
    message: str,
    thread: Thread,
    at,
    venue: Venue | None,
    db: Session,
    config_db: Session,
    user_id: str | None,
) -> dict:
    """ "Run Now" from a task's conversation: its prompt, unattended, under the
    task's own tool scope — never the full union (default_tool_filter)."""
    from app.services.entitlements import agent_entitled, org_id_for_user

    logger.info("Automated task Run Now detected (task=%s)", at.id[:12])
    if not agent_entitled(at.agent_slug, org_id_for_user(user_id, db), db, config_db):
        # Hierarchy v2: a retired team member's task must not run — its tools
        # are gone from the union, so it would execute near-toolless and
        # half-do the job.
        logger.info(
            "Run Now skipped: member %s not hired (task=%s)", at.agent_slug, at.id[:12]
        )
        skip = (
            "This task belongs to a team member that isn't hired right now. "
            "Re-hire them on the team page to run it."
        )
        db.add(Message(thread_id=thread.id, role="assistant", content=skip))
        db.commit()
        db.refresh(thread)
        return {
            "id": thread.id,
            "domain": thread.domain,
            "intent": thread.intent,
            "title": thread.title,
            "message": skip,
            "status": "skipped_unhired",
            "created_at": thread.created_at.isoformat(),
            "updated_at": thread.updated_at.isoformat(),
        }

    from app.agents.tool_loop import run_tool_loop
    from app.services.agent_config_service import default_tool_filter

    system_prompt, anthropic_tools = norm_agent().get_tool_definitions(
        db,
        user_id=user_id,
        active_venue_name=venue.name if venue else None,
        venue_timezone=venue.timezone if venue else None,
        config_db=config_db,
        tool_filter=at.tool_filter or default_tool_filter(at.agent_slug, config_db),
    )
    return run_tool_loop(
        message, thread, db, system_prompt, anthropic_tools, config_db=config_db
    )


# ---------------------------------------------------------- new threads ----


def _new_thread(
    message: str,
    db: Session,
    config_db: Session,
    user_id: str | None,
    venue_id: str | None,
    page_context: dict | None,
) -> dict:
    # "Connect BambooHR" shows the connect card, deterministically, rather
    # than depending on the model choosing to call show_connect.
    connect_target = _detect_connect_intent(message, config_db)
    if connect_target:
        return _create_connect_response(message, connect_target, db, user_id)

    venue = _resolve_venue(message, venue_id, db, user_id)

    from app.services import thread_titles

    title_job = thread_titles.start(message, db)
    result = norm_agent().handle_message(
        message,
        db,
        user_id,
        venue_id=venue.id if venue else None,
        venue_name=venue.name if venue else None,
        venue_timezone=venue.timezone if venue else None,
        config_db=config_db,
        page_context=page_context,
    )

    thread_obj = (
        db.query(Thread).filter(Thread.id == result["id"]).first()
        if result.get("id")
        else None
    )
    if thread_obj is not None:
        if not thread_obj.title:
            thread_obj.title = thread_titles.finish(
                title_job, message, thread_obj.id, db
            )
            db.commit()
        result["title"] = thread_obj.title
    return result


def _resolve_venue(
    message: str, venue_id: str | None, db: Session, user_id: str | None
) -> Venue | None:
    """The venue a new thread is about: the one the request named, the user's
    only venue, or the one the message names. None otherwise — the agent sees
    the user's venues and every venue-scoped tool takes a venue, so it asks
    or works across them, as the router's "all"/"unclear" answers used to."""
    if venue_id:
        return db.query(Venue).filter(Venue.id == venue_id).first()
    from app.services.venue_service import get_user_venues

    venues = get_user_venues(db, user_id)
    if len(venues) == 1:
        return venues[0]
    return _venue_named_in(message, venues)


def _plain(s: str) -> str:
    s = s.lower().replace("'", "").replace("’", "").replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def _venue_named_in(message: str, venues: list[Venue]) -> Venue | None:
    """The ONE venue whose name appears in the message (case, punctuation and
    a leading "The" ignored), as whole words. Two named = a cross-venue ask:
    no single venue."""
    text = f" {_plain(message)} "
    hits = []
    for v in venues:
        name = _plain(v.name or "")
        names = {name, name.removeprefix("the ")}
        if any(n and f" {n} " in text for n in names):
            hits.append(v)
    return hits[0] if len(hits) == 1 else None


# -------------------------------------------------------- connect cards ----

# A connect/reconnect request is cross-cutting and wants a UI, not an answer:
# detecting it deterministically is what makes "connect BambooHR" show the
# connect card. A few obvious aliases on top of each connector's own name.
_CONNECT_VERBS = (
    "connect",
    "reconnect",
    "re-connect",
    "reauthorize",
    "reauthorise",
    "authorize",
    "authorise",
    "link up",
    "hook up",
)
_CONNECT_ALIASES = {
    "loadedhub": ("loaded hub", "loaded", "loadedhub"),
    "bamboohr": ("bamboo hr", "bamboohr", "bamboo"),
    "microsoft_outlook": ("outlook", "microsoft"),
    "gmail": ("gmail", "google mail"),
}


def _detect_connect_intent(message: str, config_db: Session) -> str | None:
    """Return the connector the user wants to connect, or None.

    Requires an explicit connect verb so it never hijacks an ordinary request,
    and resolves the connector from the enabled specs' name/display name plus a
    small alias map.
    """
    text = " ".join(message.lower().split())
    if not any(v in text for v in _CONNECT_VERBS):
        return None

    from app.db.config_models import ConnectionSpec

    best = None
    for spec in config_db.query(ConnectionSpec).filter(ConnectionSpec.enabled == True):  # noqa: E712
        # Skip Norm-internal plumbing — not a user-connectable system.
        if spec.connector_name == "norm" or spec.category == "_platform":
            continue
        tokens = {spec.connector_name.lower(), (spec.display_name or "").lower()}
        tokens |= set(_CONNECT_ALIASES.get(spec.connector_name, ()))
        for tok in tokens:
            tok = tok.strip()
            if len(tok) >= 4 and tok in text:
                # Prefer the longest match so "bamboo hr" beats "bamboo".
                if best is None or len(tok) > best[1]:
                    best = (spec.connector_name, len(tok))
    return best[0] if best else None


def _create_connect_response(
    message: str,
    connector_name: str,
    db: Session,
    user_id: str | None = None,
) -> dict:
    """Answer a connect request with the in-conversation connect card. The
    thread is an ordinary Norm thread: a follow-up in it ("ok, check again")
    goes to the agent like any other."""
    from app.agents.norm import NORM_DOMAIN

    display_blocks = [
        {"component": "connector_connect", "data": {"connector_name": connector_name}}
    ]
    answer = "Sure — here's the connection panel."

    thread = Thread(
        user_id=user_id,
        intent=f"{NORM_DOMAIN}.tool_use",
        domain=NORM_DOMAIN,
        status="completed",
        raw_prompt=message,
        title=f"Connect {connector_name}",
        extracted_fields={},
        missing_fields=[],
    )
    db.add(thread)
    db.flush()
    db.add(Message(thread_id=thread.id, role="user", content=message))
    db.add(
        Message(
            thread_id=thread.id,
            role="assistant",
            content=answer,
            display_blocks=display_blocks,
        )
    )
    db.commit()
    db.refresh(thread)

    return {
        "id": thread.id,
        "domain": thread.domain,
        "intent": thread.intent,
        "title": thread.title,
        "message": message,
        "status": "completed",
        "created_at": thread.created_at.isoformat(),
        "updated_at": thread.updated_at.isoformat(),
        "conversation": [
            {
                "role": m.role,
                "text": m.content,
                "display_blocks": m.display_blocks,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in sorted(thread.messages, key=lambda x: x.created_at)
        ],
    }
