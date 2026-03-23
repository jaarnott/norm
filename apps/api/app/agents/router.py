"""LLM routing layer — classifies a message to a domain.

Uses Haiku for speed (~500ms).
"""

import json
import logging
import time

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def classify(message: str, domains: list[str], db: Session | None = None) -> dict:
    """Classify a message into a domain.

    Returns {"domain": str, "confidence": float, "llm_call_id": str | None}.
    """
    from app.services.secrets import get_api_key

    api_key = get_api_key("anthropic", "api_key", db) or ""
    return _llm_classify(message, domains, api_key, db)


def _llm_classify(
    message: str, domains: list[str], api_key: str, db: Session | None = None
) -> dict:
    import anthropic

    if not db:
        raise RuntimeError("Router requires a DB session to load its system prompt")

    from app.services.agent_config_service import get_system_prompt

    prompt_template = get_system_prompt("router", db)
    if not prompt_template:
        raise RuntimeError(
            "Router system prompt is not configured. Set it in Settings > Router."
        )

    # Use .replace() instead of .format() — the prompt contains literal JSON
    # braces that would break Python's string formatting.
    domain_list = "\n".join(f"- {d}" for d in domains)
    system = prompt_template.replace("{domains}", domain_list)

    # Inject venue context so the router can extract venue from the message
    from app.services.venue_service import get_user_venues

    venues = get_user_venues(db)
    if len(venues) > 1:
        venue_names = [v.name for v in venues]
        system += f"\n\nAvailable venues: {', '.join(venue_names)}"
        system += '\nInclude "venue" in your JSON response with the venue name if the user mentions or implies a specific venue. Use null if no venue is mentioned or it is ambiguous.'

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
                task_id=None,
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
            "llm_call_id": llm_call_id,
        }

    except Exception as exc:
        duration_ms = int((time.time() - t0) * 1000)
        if db is not None:
            from app.db.models import LlmCall

            record = LlmCall(
                task_id=None,
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
