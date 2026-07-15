"""Central model selection.

No Claude model ID should be hardcoded at a call site. Every LLM call resolves
its model through here so the operator's choice in Settings → Anthropic (the
"Agent Model" and "Router Model" selectors) governs the whole app, with the
environment defaults in ``Settings`` as the fallback.

Resolution order for each role:
    1. an explicit ``override`` passed by the caller
    2. the operator's selection, stored on the Anthropic connector config
       (the same value the Settings selector writes) — only when a ``db`` is given
    3. the ``Settings`` default for that role

The two selector keys (``interpreter_model``, ``router_model``) match the
``fields`` defined on the Anthropic platform connector in
``app/routers/connectors.py``.
"""

from sqlalchemy.orm import Session

from app.config import settings


def _resolve(
    db: Session | None, selector_key: str, default: str, override: str | None
) -> str:
    if override:
        return override
    if db is not None:
        from app.services.secrets import get_api_key

        selected = get_api_key("anthropic", selector_key, db)
        if selected:
            return selected
    return default


def agent_model(db: Session | None = None, override: str | None = None) -> str:
    """Primary reasoning model — Settings → "Agent Model" selector.

    Used for interpreting user requests and for generation tasks (playbooks,
    connector specs, tests) that need agent-grade reasoning.
    """
    return _resolve(db, "interpreter_model", settings.LLM_INTERPRETER_MODEL, override)


def router_model(db: Session | None = None, override: str | None = None) -> str:
    """Fast/cheap model for routing and other lightweight steps
    (e.g. conversation summarisation) — Settings → "Router Model" selector.
    """
    return _resolve(db, "router_model", settings.ROUTER_MODEL, override)
