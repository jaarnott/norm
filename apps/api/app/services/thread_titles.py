"""A new thread's title, drafted while the agent works on the reply.

The router used to return a title with its routing verdict, which put a model
call in front of every new conversation. Now the title is a side job: started
before the agent runs, collected after it answers (it is nearly always done by
then), and replaced by the opening words of the message if it isn't.
"""

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="thread-title")

_SYSTEM = (
    "Write a title of 3 to 6 words for a conversation that opens with the "
    "user's message below, as it would appear in a list of conversations. "
    "Plain words — no quotes, no trailing punctuation. Reply with the title only."
)

#: How long to wait for the title once the reply is done.
_WAIT_S = 3.0
_FALLBACK_CHARS = 60


def start(message: str, db: Session) -> Future | None:
    """Begin drafting a title; None when no API key is configured."""
    from app.services.models import router_model
    from app.services.secrets import get_api_key

    api_key = get_api_key("anthropic", "api_key", db) or ""
    if not api_key:
        return None
    model = router_model(db)
    return _POOL.submit(_draft, message, api_key, model)


def _draft(message: str, api_key: str, model: str) -> dict:
    import anthropic

    t0 = time.time()
    response = anthropic.Anthropic(api_key=api_key).messages.create(
        model=model,
        max_tokens=30,
        system=_SYSTEM,
        messages=[{"role": "user", "content": message[:2000]}],
    )
    usage = response.usage
    return {
        "title": response.content[0].text.strip().strip("\"'").rstrip("."),
        "model": model,
        "duration_ms": int((time.time() - t0) * 1000),
        "input_tokens": usage.input_tokens if usage else None,
        "output_tokens": usage.output_tokens if usage else None,
    }


def fallback(message: str) -> str:
    """The opening words of the message, cut at a word boundary."""
    text = " ".join(message.split())
    if len(text) <= _FALLBACK_CHARS:
        return text
    return text[:_FALLBACK_CHARS].rsplit(" ", 1)[0] + "…"


def finish(job: Future | None, message: str, thread_id: str, db: Session) -> str:
    """The drafted title (logged as an LlmCall on the thread), else the fallback."""
    if job is None:
        return fallback(message)
    try:
        out = job.result(timeout=_WAIT_S)
    except Exception as exc:  # noqa: BLE001 — a title is never worth failing a turn
        logger.info("Thread title not drafted (%s) — using the message", exc)
        return fallback(message)

    from app.db.models import LlmCall

    db.add(
        LlmCall(
            thread_id=thread_id,
            call_type="title",
            model=out["model"],
            system_prompt=_SYSTEM,
            user_prompt=message[:2000],
            raw_response=out["title"],
            status="success",
            duration_ms=out["duration_ms"],
            input_tokens=out["input_tokens"],
            output_tokens=out["output_tokens"],
        )
    )
    return out["title"][:120] or fallback(message)
