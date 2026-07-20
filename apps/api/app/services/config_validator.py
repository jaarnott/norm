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

        max_result_chars = tool.get("max_result_chars")
        if max_result_chars is not None and not (
            isinstance(max_result_chars, int)
            and not isinstance(max_result_chars, bool)
            and max_result_chars > 0
        ):
            issues.append(
                ConfigIssue(
                    severity="error",
                    where=where,
                    problem=(
                        f"max_result_chars is {max_result_chars!r}, "
                        "expected a positive integer"
                    ),
                    fix=(
                        "Set a positive integer (clamped to "
                        "HARD_MAX_TOOL_RESULT_CHARS at runtime) or remove it."
                    ),
                )
            )

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
            elif isinstance(consolidator, dict):
                # A syntax error in function_code otherwise surfaces only when
                # an agent calls the tool.
                try:
                    compile(consolidator["function_code"], where, "exec")
                except SyntaxError as exc:
                    issues.append(
                        ConfigIssue(
                            severity="error",
                            where=where,
                            problem=f"function_code has a syntax error: {exc}",
                            fix="Fix the consolidator code in Settings → Connectors.",
                        )
                    )
                # Write actions the consolidator declares must exist on this
                # spec — a typo here means the write is denied at runtime.
                spec_actions = {
                    t.get("action") for t in tools or [] if isinstance(t, dict)
                }
                for declared in consolidator.get("allowed_write_actions") or []:
                    bare = str(declared).split(".", 1)[-1]
                    if bare not in spec_actions:
                        issues.append(
                            ConfigIssue(
                                severity="error",
                                where=where,
                                problem=(
                                    f"allowed_write_actions names '{declared}' "
                                    "which is not a tool on this connector"
                                ),
                                fix="Fix the action name in consolidator_config.",
                            )
                        )
            # A consolidator legitimately has no URL of its own.
            continue

        response_format = tool.get("response_format")
        if response_format not in (None, "binary"):
            issues.append(
                ConfigIssue(
                    severity="error",
                    where=where,
                    problem=f"unknown response_format '{response_format}'",
                    fix='Use "binary" for file downloads, or remove the field.',
                )
            )

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

        issues.extend(_check_stale_aggregates(where, tool))

    return issues


# Words that mean a field summarises the rows beneath it rather than describing
# the object itself.
_AGGREGATE_HINTS = ("total", "sum", "count", "avg", "average")


def _check_stale_aggregates(where: str, tool: dict) -> list[ConfigIssue]:
    """Flag a transform that filters rows but passes a summary field through.

    `apply_response_transform` can drop rows from a nested array
    (`rosteredShifts[].isFromOtherVenue equals false`) while copying a
    top-level field straight across (`totalHours -> totalHours`). The survivors
    and the summary then describe different sets of rows, and nothing errors —
    the payload is simply, quietly wrong.

    That shipped: `get_roster` requested every venue's shifts, filtered them
    down to one venue, and kept LoadedHub's all-venue `totalHours`. A week
    showing 66 shifts worth 146.5 hours reported a total of 332.25. It was only
    noticed because two agents answered the same question differently.

    Whoever hits this next has two honest options — narrow the request so the
    source computes the summary over the right rows (what get_roster now does),
    or stop passing the summary through and let the caller add up the rows.
    """
    transform = tool.get("response_transform")
    if not isinstance(transform, dict) or not transform.get("enabled"):
        return []

    filtered_arrays = sorted(
        {
            f["field"].split("[].", 1)[0]
            for f in transform.get("filters") or []
            if isinstance(f, dict) and "[]." in (f.get("field") or "")
        }
    )
    if not filtered_arrays:
        return []

    fields = transform.get("fields") or {}
    if not isinstance(fields, dict):
        return []

    # A field the transform re-derives from the surviving rows is by definition
    # not stale.
    recomputed = {
        r.get("field") for r in transform.get("recompute") or [] if isinstance(r, dict)
    }

    stale = sorted(
        name
        for name, dest in fields.items()
        if "[]" not in name
        and dest  # "" means the field is dropped, which is safe
        and name not in recomputed
        and any(hint in name.lower() for hint in _AGGREGATE_HINTS)
    )
    if not stale:
        return []

    return [
        ConfigIssue(
            severity="error",
            where=where,
            problem=(
                f"response_transform filters rows out of {', '.join(filtered_arrays)} "
                f"but passes the summary field(s) {', '.join(stale)} through "
                "unchanged, so they describe rows that are no longer there"
            ),
            fix=(
                f"Add a response_transform 'recompute' entry for {stale[0]} "
                "(e.g. {'field': 'totalHours', 'from': 'rows[].totalHours', "
                "'op': 'sum'}), narrow the request so the source totals only the "
                f'rows you keep, or map {stale[0]} to "" and let the caller '
                "sum the rows."
            ),
        )
    ]


def check_binding_capabilities(
    agent_slug: str, connector_name: str, capabilities: list | None
) -> list[ConfigIssue]:
    """Binding capability entries must be dicts with an 'action' key.

    The agents router and prompt_builder index into each entry
    (``cap["action"]``, ``cap.get("enabled")``) — a bare string 500s the
    Agents settings tab AND breaks tool building for every chat with that
    agent. This shipped as a real incident on 17 Jul 2026.
    """
    issues: list[ConfigIssue] = []
    for cap in capabilities or []:
        if not isinstance(cap, dict) or "action" not in cap:
            issues.append(
                ConfigIssue(
                    severity="error",
                    where=f"binding.{agent_slug}.{connector_name}",
                    problem=(
                        f"capability entry {cap!r} is not an object with an "
                        "'action' key"
                    ),
                    fix=(
                        'Rewrite the entry as {"action": ..., "label": ..., '
                        '"enabled": true} in Settings → Agents.'
                    ),
                )
            )
    return issues


def check_playbook_tool_filter(
    playbook_slug: str, tool_filter: list | None, known_actions: set[str]
) -> list[ConfigIssue]:
    """Every action a playbook's tool_filter names must exist on some spec.

    A stale name silently strips the tool from the agent, so the playbook's
    instructions reference a capability the agent no longer has.
    """
    issues: list[ConfigIssue] = []
    for entry in tool_filter or []:
        bare = str(entry).split("__", 1)[-1]
        if bare not in known_actions:
            issues.append(
                ConfigIssue(
                    severity="error",
                    where=f"playbook.{playbook_slug}",
                    problem=f"tool_filter names '{entry}' which no connector defines",
                    fix="Fix or remove the entry in Settings → Playbooks.",
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


def check_mcp_capability(
    cap_kind: str,
    cap_target: str,
    cap_action: str,
    cap_scopes: list | None,
    tool_def: dict | None,
    playbook_enabled: bool | None,
    known_mcp_scopes: set[str],
    denylist: set,
) -> list[ConfigIssue]:
    """Validate one enabled mcp_capabilities row against live config.

    This is the drift guard the write-time validation can't be: a capability
    enabled today can be broken tomorrow by a rename, a method change, or a
    disabled playbook. Same checks the admin endpoint runs on write, run daily
    against the real rows.
    """
    from app.mcp.projection import write_signals

    where = f"mcp.{cap_target}.{cap_action}" if cap_action else f"mcp.{cap_target}"
    issues: list[ConfigIssue] = []

    # Scopes must be real and non-empty.
    unknown = set(cap_scopes or []) - known_mcp_scopes
    if unknown:
        issues.append(
            ConfigIssue(
                severity="error",
                where=where,
                problem=(
                    f"MCP capability grants unknown scope(s): {sorted(unknown)}. "
                    "The tool is exposed but no role can ever call it."
                ),
                fix="Fix the scopes in Settings → MCP, or remove the capability.",
            )
        )
    if not (cap_scopes or []):
        issues.append(
            ConfigIssue(
                severity="error",
                where=where,
                problem="Enabled MCP capability has no scopes — authorized by nothing but holding a token.",
                fix="Assign at least one scope in Settings → MCP, or disable it.",
            )
        )

    if cap_kind == "connector":
        if (cap_target, cap_action) in denylist:
            issues.append(
                ConfigIssue(
                    severity="error",
                    where=where,
                    problem="A conversation-scoped tool is exposed over MCP; it cannot work there.",
                    fix="Disable this capability in Settings → MCP.",
                )
            )
        elif tool_def is None:
            issues.append(
                ConfigIssue(
                    severity="error",
                    where=where,
                    problem=(
                        f"MCP capability points at {cap_target}.{cap_action}, "
                        "which no connector spec defines (renamed or removed)."
                    ),
                    fix="Restore the action, or remove the capability in Settings → MCP.",
                )
            )
        else:
            signals = write_signals(tool_def)
            if signals:
                issues.append(
                    ConfigIssue(
                        severity="error",
                        where=where,
                        problem=(
                            "MCP capability is exposed as a direct read tool but the "
                            f"underlying action now writes: {'; '.join(signals)}."
                        ),
                        fix="Disable it, or expose it via a playbook workflow instead.",
                    )
                )
    elif cap_kind == "playbook":
        if playbook_enabled is None:
            issues.append(
                ConfigIssue(
                    severity="error",
                    where=where,
                    problem=f"MCP capability points at playbook '{cap_target}', which does not exist.",
                    fix="Remove the capability in Settings → MCP.",
                )
            )
        elif not playbook_enabled:
            issues.append(
                ConfigIssue(
                    severity="error",
                    where=where,
                    problem=f"MCP capability exposes playbook '{cap_target}', but that playbook is disabled.",
                    fix="Enable the playbook, or disable the MCP capability.",
                )
            )

    return issues


def validate_config(db=None, config_db=None) -> dict:
    """Run every check against the live databases. Returns a summary dict.

    This is the half that CI cannot do: CI has an empty config database, and
    config can be edited through the Settings UI long after deploy.
    """
    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.db.config_models import AgentConnectorBinding, ConnectorSpec, Playbook
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
        known_actions: set[str] = set()
        for spec in config_db.query(ConnectorSpec).all():
            issues.extend(
                check_connector_tools(
                    spec.connector_name, spec.execution_mode, spec.tools
                )
            )
            for tool in spec.tools or []:
                if isinstance(tool, dict) and tool.get("action"):
                    known_actions.add(tool["action"])

        for playbook in config_db.query(Playbook).all():
            issues.extend(
                check_playbook_tool_filter(
                    playbook.slug, playbook.tool_filter, known_actions
                )
            )

        for binding in config_db.query(AgentConnectorBinding).all():
            issues.extend(
                check_binding_capabilities(
                    binding.agent_slug, binding.connector_name, binding.capabilities
                )
            )

        for row in db.query(ConnectorConfig).all():
            issues.extend(
                check_model_selection(row.connector_name, row.config, allowed)
            )

        # MCP capability drift: every enabled row must still resolve to a real,
        # read-only connector action or an enabled playbook.
        from app.db.config_models import McpCapability
        from app.mcp.projection import MCP_DENYLIST
        from app.mcp.scopes import MCP_SCOPES

        tool_def_by_key: dict = {}
        for spec in config_db.query(ConnectorSpec).all():
            for tool in spec.tools or []:
                if isinstance(tool, dict) and tool.get("action"):
                    tool_def_by_key[(spec.connector_name, tool["action"])] = tool
        playbook_enabled_by_slug = {
            pb.slug: pb.enabled for pb in config_db.query(Playbook).all()
        }
        known_mcp_scopes = set(MCP_SCOPES)
        for cap in (
            config_db.query(McpCapability)
            .filter(McpCapability.enabled == True)  # noqa: E712
            .all()
        ):
            issues.extend(
                check_mcp_capability(
                    cap.kind,
                    cap.target,
                    cap.action,
                    cap.scopes,
                    tool_def_by_key.get((cap.target, cap.action)),
                    playbook_enabled_by_slug.get(cap.target),
                    known_mcp_scopes,
                    MCP_DENYLIST,
                )
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
