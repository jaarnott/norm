"""LLM routing layer — classifies a message to a domain.

Uses Haiku for speed (~500ms).
"""

import json
import logging
import time

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def classify(
    message: str,
    domains: list[str],
    db: Session | None = None,
    config_db: Session | None = None,
) -> dict:
    """Classify a message into a domain.

    Returns {"domain": str, "confidence": float, "llm_call_id": str | None}.
    db = main DB (for credentials), config_db = config DB (for agent prompts).
    """
    from app.services.secrets import get_api_key

    api_key = get_api_key("anthropic", "api_key", db) or ""
    return _llm_classify(message, domains, api_key, db=db, config_db=config_db)


def classify_followup(
    message: str,
    thread_domain: str,
    thread_playbook_name: str | None,
    recent_summary: str,
    thread_id: str | None = None,
    playbook_tools: list[str] | None = None,
    db: Session | None = None,
    config_db: Session | None = None,
) -> dict:
    """Classify a follow-up message in an existing thread.

    Returns {"action": "continue"|"continue_no_playbook"|"new_thread",
             "domain": str, "playbook": str|None, ...}
    """
    import anthropic
    from app.services.secrets import get_api_key
    from app.config import settings

    api_key = get_api_key("anthropic", "api_key", db) or ""
    _cdb = config_db or db

    model = settings.ROUTER_MODEL

    # Load available playbooks for this domain
    playbook_section = ""
    if _cdb:
        from app.db.config_models import Playbook

        playbooks = (
            _cdb.query(Playbook)
            .filter(Playbook.agent_slug == thread_domain, Playbook.enabled == True)  # noqa: E712
            .all()
        )
        if playbooks:
            pb_lines = [f"- {pb.slug}: {pb.description}" for pb in playbooks]
            playbook_section = "\n\nAvailable playbooks:\n" + "\n".join(pb_lines)

    system = f"""You are a message router for Norm, a hospitality operations platform.
The user is sending a follow-up message in an existing thread.

Current thread: {thread_domain} agent
Recent conversation:
{recent_summary}{playbook_section}

Decide how to handle this follow-up:
a) "continue" — the message continues the current conversation naturally. If a playbook matches this specific message, include its slug.
b) "new_thread" — the message is about a completely different topic that belongs to a different domain agent.

Return ONLY valid JSON:
{{"action": "continue" | "new_thread", "domain": "<domain>", "playbook": "<slug or null>", "reason": "<brief reason>"}}

If a playbook listed above matches this message, include its slug in "playbook".
If no playbook matches, set playbook to null (agent gets full tool access).
If action is "new_thread", set domain to the appropriate domain.
Default to "continue" — only use "new_thread" for clear domain switches (e.g., HR question in a procurement thread)."""

    client = anthropic.Anthropic(api_key=api_key)
    t0 = time.time()

    try:
        response = client.messages.create(
            model=model,
            max_tokens=128,
            system=system,
            messages=[{"role": "user", "content": message}],
        )
        raw = response.content[0].text.strip()
        duration_ms = int((time.time() - t0) * 1000)

        clean = raw
        if clean.startswith("```"):
            lines = clean.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            clean = "\n".join(lines).strip()

        decoder = json.JSONDecoder()
        parsed, _ = decoder.raw_decode(clean)

        if db is not None:
            from app.db.models import LlmCall

            record = LlmCall(
                thread_id=thread_id,
                call_type="routing",
                model=model,
                system_prompt=system,
                user_prompt=message,
                raw_response=raw,
                parsed_response=parsed,
                status="success",
                duration_ms=duration_ms,
                input_tokens=response.usage.input_tokens if response.usage else None,
                output_tokens=response.usage.output_tokens if response.usage else None,
            )
            db.add(record)
            db.flush()

        return {
            "action": parsed.get("action", "continue"),
            "domain": parsed.get("domain", thread_domain),
            "playbook": parsed.get("playbook"),
            "reason": parsed.get("reason", ""),
        }

    except Exception:
        # Default to continue on error
        return {"action": "continue", "domain": thread_domain, "reason": "router error"}


def _llm_classify(
    message: str,
    domains: list[str],
    api_key: str,
    db: Session | None = None,
    config_db: Session | None = None,
) -> dict:
    import anthropic

    _cdb = config_db or db
    if not _cdb:
        raise RuntimeError("Router requires a DB session to load its system prompt")

    from app.services.agent_config_service import get_system_prompt

    prompt_template = get_system_prompt("router", _cdb)
    if not prompt_template:
        raise RuntimeError(
            "Router system prompt is not configured. Set it in Settings > Router."
        )

    # Use .replace() instead of .format() — the prompt contains literal JSON
    # braces that would break Python's string formatting.
    domain_list = "\n".join(f"- {d}" for d in domains)
    system = prompt_template.replace("{domains}", domain_list)

    # Inject playbook options so the router can match a workflow
    from app.db.config_models import Playbook

    if _cdb:
        playbooks = (
            _cdb.query(Playbook)
            .filter(Playbook.enabled == True)  # noqa: E712
            .order_by(Playbook.agent_slug, Playbook.slug)
            .all()
        )
        if playbooks:
            pb_lines = []
            for pb in playbooks:
                pb_lines.append(f"- {pb.agent_slug}/{pb.slug}: {pb.description}")
            system += (
                "\n\nPlaybooks (optional — pick one if the message clearly matches a specific workflow):\n"
                + "\n".join(pb_lines)
                + '\nInclude "playbook" in your JSON response: the full slug (e.g. "weekly_sales_report") if a playbook clearly matches, or null if none match.'
            )

    # Inject venue context so the router can extract venue from the message
    from app.services.venue_service import get_user_venues

    venues = get_user_venues(db)
    if len(venues) > 1:
        venue_names = [v.name for v in venues]
        system += f"\n\nAvailable venues: {', '.join(venue_names)}"
        system += (
            '\nInclude "venue" in your JSON response:'
            "\n- The exact venue name if the user mentions a specific venue"
            '\n- A comma-separated list if they mention multiple specific venues (e.g. "La Zeppa, Mr Murdochs")'
            '\n- "all" if they want data across all venues or to compare venues'
            '\n- "unclear" if the request likely needs a venue but none was mentioned. When unclear, also include "venue_question": a short, friendly question asking which venue (e.g. "Sure! Which venue would you like me to check the roster for?")'
            "\n- Omit the venue field entirely if the request doesn't need a venue (e.g. recipe lookups, general questions)"
        )

    from app.config import settings
    from app.services.secrets import get_api_key as _get_config

    model = _get_config("anthropic", "router_model", db) or settings.ROUTER_MODEL

    client = anthropic.Anthropic(api_key=api_key)
    llm_call_id = None
    t0 = time.time()

    try:
        response = client.messages.create(
            model=model,
            max_tokens=128,
            system=system,
            messages=[{"role": "user", "content": message}],
        )
        raw = response.content[0].text.strip()
        duration_ms = int((time.time() - t0) * 1000)

        # Handle markdown fences
        clean = raw
        if clean.startswith("```"):
            lines = clean.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            clean = "\n".join(lines).strip()

        # Extract just the first JSON object if the model returned extra text
        decoder = json.JSONDecoder()
        parsed, _ = decoder.raw_decode(clean)

        # Extract token usage
        _input_tokens = response.usage.input_tokens if response.usage else None
        _output_tokens = response.usage.output_tokens if response.usage else None

        # Persist LLM call record
        if db is not None:
            from app.db.models import LlmCall

            record = LlmCall(
                thread_id=None,
                call_type="routing",
                model=model,
                system_prompt=system,
                user_prompt=message,
                raw_response=raw,
                parsed_response=parsed,
                status="success",
                duration_ms=duration_ms,
                input_tokens=_input_tokens,
                output_tokens=_output_tokens,
            )
            db.add(record)
            db.flush()
            llm_call_id = record.id

        return {
            "domain": parsed.get("domain", "unknown"),
            "confidence": parsed.get("confidence", 0.5),
            "title": parsed.get("title"),
            "venue": parsed.get("venue"),
            "playbook": parsed.get("playbook"),
            "llm_call_id": llm_call_id,
        }

    except Exception as exc:
        duration_ms = int((time.time() - t0) * 1000)
        if db is not None:
            from app.db.models import LlmCall

            record = LlmCall(
                thread_id=None,
                call_type="routing",
                model=model,
                system_prompt=system,
                user_prompt=message,
                raw_response=None,
                parsed_response=None,
                status="error",
                error_message=str(exc),
                duration_ms=duration_ms,
            )
            db.add(record)
            db.flush()
        raise
