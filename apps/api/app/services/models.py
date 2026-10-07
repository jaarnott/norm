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

A stored selection that names a retired model is ignored in favour of the
current default: these values are persisted per-environment, so a model picked
in the UI long ago outlives its own retirement and would otherwise pin the
environment to an ID the API answers with HTTP 404.
"""

import logging

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

# Snapshot IDs Anthropic has retired — the API returns 404 ``not_found_error``
# for these. Never select one, even if it is what the operator last saved.
# The Haiku full ID ``claude-haiku-4-5-20251001`` is current and NOT retired.
RETIRED_MODEL_IDS = frozenset(
    {
        "claude-opus-4-20250514",
        "claude-sonnet-4-20250514",
        "claude-3-opus-20240229",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-sonnet-20240620",
        "claude-3-7-sonnet-20250219",
        "claude-3-5-haiku-20241022",
    }
)

# Models the defaults have moved past. They still answer, so this is not
# retirement — but a selector that holds one almost always holds it because it
# WAS the default when someone last opened Settings (production's selector was
# saved on 6 Aug 2026 with the then-default Opus 4.8), and leaving it in place
# would quietly pin every environment to the old model after an upgrade. A
# superseded selection resolves to the role's current default, which stays
# overridable per environment through the Settings env var — that is the
# rollback path. Models that are still a deliberate choice (Sonnet 5.5, Haiku
# 4.5) are not in this set.
SUPERSEDED_MODEL_IDS = frozenset(
    {"claude-opus-4-8", "claude-opus-5", "claude-sonnet-5"}
)


def supports_effort(model: str) -> bool:
    """Whether ``model`` accepts ``output_config.effort`` (and so always thinks).

    Opus 4.7+ and Sonnet 5+ take an effort level; Haiku 4.5 and anything older
    reject it with a 400 — those are the only models without it that Norm
    still selects.
    """
    name = model.lower()
    return "haiku" not in name and not name.startswith("claude-3")


def _resolve(
    db: Session | None, selector_key: str, default: str, override: str | None
) -> str:
    if override:
        return override
    if db is not None:
        from app.services.secrets import get_api_key

        selected = get_api_key("anthropic", selector_key, db)
        if selected and selected in SUPERSEDED_MODEL_IDS:
            logger.info(
                "Selector %r holds %r, which the default has moved past; "
                "using %r. Pick a model in Settings → Anthropic to choose "
                "explicitly.",
                selector_key,
                selected,
                default,
            )
        elif selected and selected in RETIRED_MODEL_IDS:
            logger.warning(
                "Ignoring retired model %r saved in the %r selector; "
                "falling back to %r. Re-pick the model in Settings → Anthropic "
                "to clear the stale value.",
                selected,
                selector_key,
                default,
            )
        elif selected:
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
