"""LLM routing layer — classifies a message to a domain.

Uses Haiku for speed (~500ms).
"""

import json
import logging
import os
import time

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

ROUTER_PROMPT = """\
You are a message router for Norm, a hospitality operations platform.
Given a user message and the list of available domains, classify which domain
should handle this message, and generate a short title for the task.

Available domains:
{domains}

Title guidelines:
- 3-6 words, no articles (a/an/the)
- Describe the user's goal, not the domain (e.g. "Weekly roster request" not "HR query")
- Use sentence case (capitalize first word only)
- No trailing punctuation

Return ONLY valid JSON:
{{"domain": "<domain-slug or unknown>", "confidence": 0.0-1.0, "title": "<short task title>"}}
"""


def classify(message: str, domains: list[str], db: Session | None = None) -> dict:
    """Classify a message into a domain.

    Returns {"domain": str, "confidence": float, "llm_call_id": str | None}.
    """
    from app.services.secrets import get_api_key

    api_key = get_api_key("anthropic", "api_key", db) or ""
    return _llm_classify(message, domains, api_key, db)


def _llm_classify(message: str, domains: list[str], api_key: str, db: Session | None = None) -> dict:
    import anthropic

    domain_desc = "\n".join(f"- {d}" for d in domains)
    if db:
        from app.services.agent_config_service import get_system_prompt as get_db_prompt
        prompt_template = get_db_prompt("router", db)
    else:
        prompt_template = ROUTER_PROMPT
    system = prompt_template.format(domains=domain_desc)
    model = os.environ.get("ROUTER_MODEL", "claude-haiku-4-5-20251001")

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
            lines = [l for l in lines if not l.strip().startswith("```")]
            clean = "\n".join(lines).strip()

        # Extract just the first JSON object if the model returned extra text
        decoder = json.JSONDecoder()
        parsed, _ = decoder.raw_decode(clean)

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
            )
            db.add(record)
            db.flush()
            llm_call_id = record.id

        return {
            "domain": parsed.get("domain", "unknown"),
            "confidence": parsed.get("confidence", 0.5),
            "title": parsed.get("title"),
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
