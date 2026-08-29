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

    ``tools`` is the raw JSON list from ConnectionSpec.tools.
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


# Display components that belong to the platform itself rather than to any
# marketplace app — the non-app-owned half of DisplayBlockRenderer's REGISTRY.
# App-owned components are validated against the catalog instead, so a new
# app component needs no edit here; this list only grows with platform chrome.
PLATFORM_COMPONENTS = frozenset(
    {
        "generic_table",
        "criteria_editor",
        "automated_task_preview",
        "automated_task_board",
        "chart",
        "report_builder",
        "saved_reports_board",
        "apps_dashboard",
        "app_runner",
        "tool_approval",
        "venue_picker",
        "dashboard_view",
        "mcp_embed",
        "connector_connect",
    }
)


def check_display_components(
    connector_name: str, tools: list | None, known_components: set[str]
) -> list[ConfigIssue]:
    """Every tool's ``display_component`` must name a component that exists.

    The field is free text; ``tool_loop`` emits a display block with whatever
    it says, and a name the web registry lacks renders the user a blank —
    silently. Known = platform chrome + every component an app composition
    declares (the catalog is the registry's server-side mirror).
    """
    issues: list[ConfigIssue] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        component = tool.get("display_component")
        if component and component not in known_components:
            issues.append(
                ConfigIssue(
                    severity="error",
                    where=f"{connector_name}.{tool.get('action') or '<no action>'}",
                    problem=(
                        f"display_component '{component}' is not platform "
                        "chrome and no app composition declares it — the "
                        "display block renders blank"
                    ),
                    fix=(
                        "Fix the component name, or declare the component in "
                        "its app's catalog composition "
                        "(sync_marketplace_catalog.py)."
                    ),
                )
            )
    return issues


def check_component_api_row(
    component_key: str,
    connector_name: str,
    action_name: str,
    known_components: set[str],
    known_connectors: set[str],
) -> list[ConfigIssue]:
    """A component_api row must resolve at load time.

    These rows are the HTTP door a page component calls when it mounts. Each
    row is self-contained (its own path_template and method — action_name is
    just the row's name, never a spec tool), but ``execute_component_action``
    still 404s without a connection spec for the row's connector. An unknown
    component key is the other direction: config serving a component that
    exists nowhere (two of these were found as untracked DB rows during the
    marketplace inventory).
    """
    where = f"component_api.{component_key}.{action_name}"
    issues: list[ConfigIssue] = []
    if connector_name not in known_connectors:
        issues.append(
            ConfigIssue(
                severity="error",
                where=where,
                problem=(
                    f"row names connector '{connector_name}', which has no "
                    "connection spec — the component's call fails on load"
                ),
                fix="Fix the connector name, or delete the row.",
            )
        )
    if component_key not in known_components:
        issues.append(
            ConfigIssue(
                severity="error",
                where=where,
                problem=(
                    f"component '{component_key}' is not platform chrome and "
                    "no app composition declares it"
                ),
                fix=(
                    "Declare the component in its app's catalog composition "
                    "(sync_marketplace_catalog.py), or delete the row."
                ),
            )
        )
    return issues


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


def check_binding_actions(
    agent_slug: str,
    connector_name: str,
    capabilities: list | None,
    spec_actions: set[str] | None,
    engine_only_actions: set[str] | None = None,
) -> list[ConfigIssue]:
    """Every enabled capability on an enabled binding must resolve to an
    agent-visible tool on the bound connector's spec.

    Both failure modes are SILENT — ``_collect_tools`` intersects the spec's
    tools with the capability set, so a capability naming nothing simply
    contributes nothing and the agent loses the tool without any error. This
    is how the executive chef lost its recipe write on 29 Aug 2026: the Cook
    Brothers App consolidated 114 tools down to 45, the re-discovered spec no
    longer had ``kitchen_loadedhub_update_recipe``, and the binding capability
    kept pointing at the old name in every environment until a human noticed
    Save was broken.
    """
    issues: list[ConfigIssue] = []
    where = f"binding.{agent_slug}.{connector_name}"

    if spec_actions is None:
        issues.append(
            ConfigIssue(
                severity="error",
                where=where,
                problem=(
                    f"binding names connector '{connector_name}', which has no "
                    "connection spec — the agent gets nothing from it"
                ),
                fix=("Restore the spec, or delete the binding in Settings → Agents."),
            )
        )
        return issues

    for cap in capabilities or []:
        if not isinstance(cap, dict):
            continue  # shape errors are check_binding_capabilities' job
        action = cap.get("action")
        if not action or not cap.get("enabled", True):
            continue
        if action not in spec_actions:
            issues.append(
                ConfigIssue(
                    severity="error",
                    where=where,
                    problem=(
                        f"capability '{action}' matches no tool on the "
                        f"'{connector_name}' spec (renamed or removed) — the "
                        "agent silently loses it"
                    ),
                    fix=(
                        "Point the capability at the current action name, or "
                        "remove it in Settings → Agents."
                    ),
                )
            )
        elif engine_only_actions and action in engine_only_actions:
            issues.append(
                ConfigIssue(
                    severity="error",
                    where=where,
                    problem=(
                        f"capability '{action}' points at an engine-only "
                        "backend agents can never see — it is silently dropped"
                    ),
                    fix=(
                        "Replace it with the consolidator that superseded it, "
                        "or remove it in Settings → Agents."
                    ),
                )
            )
    return issues


def check_playbook_tool_filter(
    playbook_slug: str,
    tool_filter: list | None,
    known_actions: set[str],
    engine_only_actions: set[str] | None = None,
) -> list[ConfigIssue]:
    """Every action a playbook's tool_filter names must exist on some spec
    AND be agent-visible.

    A stale name silently strips the tool from the agent, so the playbook's
    instructions reference a capability the agent no longer has. An
    engine_only action is just as fatal and MORE deceptive: it exists on the
    spec, so the old exists-somewhere check passed while the filter entry
    silently dropped — how the sales playbooks lost their data tools when
    the raw reads were demoted to consolidator backends (prod thread
    b9bda2c1, 23 Aug 2026: "I don't have a budget data source").
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
        elif engine_only_actions and bare in engine_only_actions:
            issues.append(
                ConfigIssue(
                    severity="error",
                    where=f"playbook.{playbook_slug}",
                    problem=(
                        f"tool_filter names '{entry}', an engine-only backend "
                        "agents can never see — the entry is silently dropped"
                    ),
                    fix=(
                        "Replace it with the consolidator that superseded it "
                        "in Settings → Playbooks."
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
    from app.db.config_models import AgentConnectionBinding, ConnectionSpec, Playbook
    from app.db.models import Connection
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
        engine_only_actions: set[str] = set()
        actions_by_connector: dict[str, set[str]] = {}
        engine_only_by_connector: dict[str, set[str]] = {}
        specs = config_db.query(ConnectionSpec).all()
        for spec in specs:
            issues.extend(
                check_connector_tools(
                    spec.connector_name, spec.execution_mode, spec.tools
                )
            )
            actions_by_connector.setdefault(spec.connector_name, set())
            engine_only_by_connector.setdefault(spec.connector_name, set())
            for tool in spec.tools or []:
                if isinstance(tool, dict) and tool.get("action"):
                    known_actions.add(tool["action"])
                    actions_by_connector[spec.connector_name].add(tool["action"])
                    if tool.get("engine_only"):
                        engine_only_actions.add(tool["action"])
                        engine_only_by_connector[spec.connector_name].add(
                            tool["action"]
                        )

        for playbook in config_db.query(Playbook).all():
            issues.extend(
                check_playbook_tool_filter(
                    playbook.slug,
                    playbook.tool_filter,
                    known_actions,
                    engine_only_actions,
                )
            )

        for binding in config_db.query(AgentConnectionBinding).all():
            issues.extend(
                check_binding_capabilities(
                    binding.agent_slug, binding.connector_name, binding.capabilities
                )
            )
            if binding.enabled:
                issues.extend(
                    check_binding_actions(
                        binding.agent_slug,
                        binding.connector_name,
                        binding.capabilities,
                        actions_by_connector.get(binding.connector_name),
                        engine_only_by_connector.get(binding.connector_name),
                    )
                )

        for row in db.query(Connection).all():
            issues.extend(
                check_model_selection(row.connector_name, row.config, allowed)
            )

        # Component drift rides the marketplace catalog (the server-side
        # mirror of the web display registry). An empty catalog means this
        # environment hasn't seeded the marketplace — skip rather than flag
        # every app component (the dark-launch property again).
        from app.db.config_models import ComponentApiConfig, MarketplaceApp

        catalog_rows = config_db.query(MarketplaceApp).all()
        if catalog_rows:
            known_components = set(PLATFORM_COMPONENTS)
            for app_row in catalog_rows:
                for comp_entry in (app_row.composition or {}).get("components") or []:
                    if isinstance(comp_entry, dict) and comp_entry.get("key"):
                        known_components.add(comp_entry["key"])
            for spec in specs:
                issues.extend(
                    check_display_components(
                        spec.connector_name, spec.tools, known_components
                    )
                )
            for capi in config_db.query(ComponentApiConfig).all():
                issues.extend(
                    check_component_api_row(
                        capi.component_key,
                        capi.connector_name,
                        capi.action_name,
                        known_components,
                        set(actions_by_connector),
                    )
                )

        # MCP capability drift: every enabled row must still resolve to a real,
        # read-only connector action or an enabled playbook.
        from app.db.config_models import McpCapability
        from app.mcp.projection import MCP_DENYLIST
        from app.mcp.scopes import MCP_SCOPES

        tool_def_by_key: dict = {}
        for spec in specs:
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
