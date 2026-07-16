"""Validate the configuration that lives in the database rather than the repo.

Norm is config-driven: connector specs, agent prompts and model selections are
data, edited through the Settings UI and stored in the config/main databases.
That is the platform's strength — a new integration needs no deploy — but it
also means **no test, type checker or code review can see that config**. Every
production incident so far has come from exactly that blind spot:

  * A Claude model id sat in ``connector_configs`` after the model was retired.
    Every agent call 404'd for months; the code default was fine, so nothing in
    the repo looked wrong.
  * ``get_stock_on_hand_for_item`` was left on the legacy ``steps`` consolidator
    format when the executor for it was deleted. The commit shipped with green
    tests, because the stale config was a JSON blob in a database row.
  * The architecture doc describes ``function_code`` consolidators while the
    database still holds a ``steps`` one.

These checks close the gap. They are **pure functions over plain rows**, so CI
can unit-test them without a live config DB (CI points CONFIG_DATABASE_URL at a
throwaway Postgres with zero rows — querying it there would prove nothing).
The same functions are then run against the real databases at runtime via
``POST /internal/validate-config``, which is the only place that can catch
config edited after deploy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class ConfigIssue:
    """A single problem found in database-held configuration."""

    severity: str  # "error" — broken now, or will break when called
    where: str  # e.g. "loadedhub.get_stock_on_hand_for_item"
    problem: str
    fix: str

    def to_dict(self) -> dict:
        return asdict(self)


def check_connector_tools(
    connector_name: str, execution_mode: str, tools: list | None
) -> list[ConfigIssue]:
    """Validate the tools array of a single connector spec.

    ``tools`` is the raw JSON list from ConnectorSpec.tools.
    """
    issues: list[ConfigIssue] = []

    for tool in tools or []:
        if not isinstance(tool, dict):
            issues.append(
                ConfigIssue(
                    severity="error",
                    where=connector_name,
                    problem=f"tool entry is {type(tool).__name__}, expected an object",
                    fix="Fix the tools JSON in Settings → Connectors.",
                )
            )
            continue

        action = tool.get("action")
        where = f"{connector_name}.{action or '<no action>'}"

        if not action:
            issues.append(
                ConfigIssue(
                    severity="error",
                    where=connector_name,
                    problem="tool has no 'action' name",
                    fix="Give the tool an action name in Settings → Connectors.",
                )
            )
            continue

        consolidator = tool.get("consolidator_config")
        if consolidator:
            # The legacy `steps` executor was deleted (see the 2026-04-06
            # "Remove legacy consolidator code" commit) — anything still on that
            # format now errors the moment an agent calls it.
            if isinstance(consolidator, dict) and not consolidator.get("function_code"):
                issues.append(
                    ConfigIssue(
                        severity="error",
                        where=where,
                        problem=(
                            "consolidator has no function_code"
                            + (
                                " (still on the legacy 'steps' format)"
                                if consolidator.get("steps")
                                else ""
                            )
                        ),
                        fix=(
                            "Port the consolidator to function_code — the legacy "
                            "steps executor no longer exists, so this tool fails "
                            "whenever it is called."
                        ),
                    )
                )
            # A consolidator legitimately has no URL of its own.
            continue

        if execution_mode == "template" and not tool.get("path_template"):
            issues.append(
                ConfigIssue(
                    severity="error",
                    where=where,
                    problem="template-mode tool has no path_template",
                    fix=(
                        "Add a path_template, or give the tool a "
                        "consolidator_config if it composes other tools."
                    ),
                )
            )

    return issues


def check_model_selection(
    connector_name: str, config: dict | None, allowed_models: list[str]
) -> list[ConfigIssue]:
    """Validate stored Claude model selections against the models we can call.

    A model id that is no longer served makes every agent call 404. The stored
    selection overrides the code default, so a current default in config.py is
    no protection at all.
    """
    issues: list[ConfigIssue] = []

    for key in ("interpreter_model", "router_model"):
        selected = (config or {}).get(key)
        if selected and selected not in allowed_models:
            issues.append(
                ConfigIssue(
                    severity="error",
                    where=f"{connector_name}.{key}",
                    problem=f"'{selected}' is not a currently available model",
                    fix=(
                        "Pick a current model in Settings → Connectors → "
                        f"Anthropic. Available: {', '.join(allowed_models)}."
                    ),
                )
            )

    return issues


def validate_config(db=None, config_db=None) -> dict:
    """Run every check against the live databases. Returns a summary dict.

    This is the half that CI cannot do: CI has an empty config database, and
    config can be edited through the Settings UI long after deploy.
    """
    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.db.config_models import ConnectorSpec
    from app.db.models import ConnectorConfig
    from app.routers.connectors import AVAILABLE_MODELS

    owns_db = db is None
    owns_config_db = config_db is None
    if owns_db:
        db = SessionLocal()
    if owns_config_db:
        config_db = _ConfigSessionLocal()

    allowed = [m["id"] for m in AVAILABLE_MODELS]
    issues: list[ConfigIssue] = []

    try:
        for spec in config_db.query(ConnectorSpec).all():
            issues.extend(
                check_connector_tools(
                    spec.connector_name, spec.execution_mode, spec.tools
                )
            )

        for row in db.query(ConnectorConfig).all():
            issues.extend(
                check_model_selection(row.connector_name, row.config, allowed)
            )

        return {
            "ok": not issues,
            "issue_count": len(issues),
            "issues": [i.to_dict() for i in issues],
        }
    finally:
        if owns_db:
            db.close()
        if owns_config_db:
            config_db.close()
