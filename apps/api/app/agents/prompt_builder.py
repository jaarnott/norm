"""Build agent system prompts dynamically from active connector specs."""

import datetime
import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _collect_tools(
    db: Session,
    user_id: str | None = None,
    config_db: Session | None = None,
) -> list[dict]:
    """Collect all enabled tools from all connector specs.

    Returns a deduplicated list of dicts with keys: action, connector,
    required_fields, field_mapping, method, description.

    config_db is used for AgentConnectorBinding and ConnectorSpec queries.
    db is used for ConnectorConfig (credentials) queries.
    """
    from app.db.models import AgentConnectorBinding, ConnectorConfig, ConnectorSpec

    _cdb = config_db
    if _cdb is None:
        raise RuntimeError(
            "config_db is required — check that config_db is passed through the call chain"
        )

    bindings = (
        _cdb.query(AgentConnectorBinding)
        .filter(
            AgentConnectorBinding.enabled == True,  # noqa: E712
        )
        .all()
    )

    if not bindings:
        return []

    tools: list[dict] = []
    for binding in bindings:
        spec = (
            _cdb.query(ConnectorSpec)
            .filter(
                ConnectorSpec.connector_name == binding.connector_name,
            )
            .first()
        )
        if not spec:
            continue

        # Gate on having an active connection — except internal specs which need no credentials
        if spec.execution_mode != "internal":
            has_config = (
                db.query(ConnectorConfig)
                .filter(
                    ConnectorConfig.connector_name == binding.connector_name,
                    ConnectorConfig.enabled == "true",
                )
                .count()
                > 0
            )
            if not has_config:
                continue

        # User-scoped email connectors: require per-user ConnectorConfig
        _USER_SCOPED_CONNECTORS = {"gmail", "microsoft_outlook"}
        if spec.connector_name in _USER_SCOPED_CONNECTORS:
            if not user_id:
                continue
            has_user_config = (
                db.query(ConnectorConfig)
                .filter(
                    ConnectorConfig.connector_name == spec.connector_name,
                    ConnectorConfig.user_id == user_id,
                )
                .count()
                > 0
            )
            if not has_user_config:
                continue

        # Build an enabled-action set from binding capabilities
        enabled_actions: set[str] | None = None
        if binding.capabilities:
            enabled_actions = {
                cap["action"]
                for cap in binding.capabilities
                if cap.get("enabled", True)
            }

        for tool in spec.tools or []:
            action = tool.get("action", "")
            if enabled_actions is not None and action not in enabled_actions:
                continue

            tools.append(
                {
                    "action": action,
                    "connector": spec.connector_name,
                    "required_fields": tool.get("required_fields", []),
                    "optional_fields": tool.get("optional_fields", []),
                    "field_mapping": tool.get("field_mapping", {}),
                    "field_descriptions": tool.get("field_descriptions", {}),
                    "field_schema": tool.get("field_schema", {}),
                    "method": tool.get("method", "POST"),
                    "description": tool.get("description", ""),
                }
            )

    # Deduplicate — same connector+action may appear in multiple agent bindings
    seen: set[tuple[str, str]] = set()
    unique_tools: list[dict] = []
    for t in tools:
        key = (t["connector"], t["action"])
        if key not in seen:
            seen.add(key)
            unique_tools.append(t)
    return unique_tools


def build_dynamic_prompt(
    domain: str, db: Session, config_db: Session | None = None
) -> str | None:
    """Return the DB-stored system prompt if connector specs are bound.

    Returns None if no connector specs are bound, signalling the caller to
    use the DB-stored prompt directly via agent_config_service.
    """
    _cdb = config_db
    if _cdb is None:
        raise RuntimeError(
            "config_db is required — check that config_db is passed through the call chain"
        )
    tools = _collect_tools(db, config_db=_cdb)
    if not tools:
        return None

    # Tools are bound — return the DB prompt (the admin manages it in Settings)
    from app.services.agent_config_service import get_system_prompt

    return get_system_prompt(domain, _cdb) or None


def build_tool_definitions(
    domain: str,
    db: Session,
    active_venue_name: str | None = None,
    venue_timezone: str | None = None,
    user_id: str | None = None,
    config_db: Session | None = None,
    page_context: dict | None = None,
    playbook=None,
    tool_filter: list[str] | None = None,
) -> tuple[str, list[dict]]:
    """Build a system prompt AND Anthropic-format tool definitions for the agentic loop.

    Returns (system_prompt, anthropic_tools) where anthropic_tools is a list
    of tool dicts in the format expected by the Anthropic tool-use API.

    tool_filter takes priority over playbook.tool_filter, which takes priority
    over the agent's default (derived from its connector bindings).

    Returns ("", []) if no tools are bound.
    """
    _cdb = config_db
    if _cdb is None:
        raise RuntimeError(
            "config_db is required — check that config_db is passed through the call chain"
        )
    tools = _collect_tools(db, user_id=user_id, config_db=_cdb)
    if not tools:
        return "", []

    # System prompt comes directly from the DB — the admin manages the full
    # prompt in the Settings UI. Supports {{today}} placeholder.
    from app.services.agent_config_service import get_system_prompt

    system_prompt = get_system_prompt(domain, _cdb)
    if not system_prompt:
        system_prompt = (
            f"You are the {domain} agent for Norm, a hospitality operations platform."
        )
    try:
        from zoneinfo import ZoneInfo

        tz = (
            ZoneInfo(venue_timezone) if venue_timezone else ZoneInfo("Pacific/Auckland")
        )
        now_local = datetime.datetime.now(tz)
        today_str = f"{now_local.strftime('%A')} {now_local.strftime('%Y-%m-%d')}"
    except Exception:
        today_str = datetime.date.today().isoformat()
    system_prompt = system_prompt.replace("{{today}}", today_str)

    # Pre-load venues (needed by both playbook and standard modes for tool definitions)
    from app.services.venue_service import get_user_venues
    from app.db.models import ConnectorConfig

    user_venues = get_user_venues(db)

    # --- PLAYBOOK MODE: slim, focused prompt ---
    if playbook:
        # Build venue context line
        venue_line = ""
        if active_venue_name:
            tz_detail = ""
            if venue_timezone:
                try:
                    from zoneinfo import ZoneInfo as _ZI

                    _tz = _ZI(venue_timezone)
                    _now = datetime.datetime.now(_tz)
                    _off = _now.strftime("%z")
                    tz_detail = (
                        f" (timezone: {venue_timezone}, UTC{_off[:3]}:{_off[3:]})"
                    )
                except Exception:
                    tz_detail = f" (timezone: {venue_timezone})"
            venue_line = f"\n\n## Active Venue\n**{active_venue_name}**{tz_detail}. Use this as the default venue for all tool calls."

        system_prompt = f"""You are the {domain} agent for Norm, a hospitality operations platform.
Today's date is {today_str}.

## Playbook: {playbook.display_name}
{playbook.instructions}

## Rules
- Only present data returned by tool calls. Never fabricate or estimate data.
- Use markdown tables for tabular data. Be concise.
- Start tool calls with "[Tool] " prefix explaining what you're doing.
- Use date formats exactly as shown in each tool's field description.{venue_line}
"""
    else:
        # --- STANDARD MODE: full prompt with all guidance sections ---

        # Inject automated tasks guidance if those tools are available
        has_automated_tasks = any(
            t.get("action") == "create_automated_task" for t in tools
        )
        if has_automated_tasks:
            system_prompt += """

## Automated Tasks
When a user asks to do something regularly or automatically, first execute the request so they can see the result, then offer to save it as an automated task.
Call `create_automated_task` with `intent` (describe what to do — be specific with names and venues). The `agent_slug` is auto-detected from the current agent. Schedule and prompt are auto-generated.
After creating a task, tell the user which agent/domain it was created under (from the response's `agent_slug`) so they can find it in the Tasks page.
"""

        # Add chart visualization guidance if render_chart tool is available
        has_render_chart = any(t.get("action") == "render_chart" for t in tools)
        if has_render_chart:
            system_prompt += """

## Chart Visualization
Default to **markdown tables** for data. Use `render_chart` when the user asks for a chart or the data clearly benefits from visualization.
- Provide `source_tool_call_id` (the tool_use ID from the conversation) and a `title`. The chart type, axes, and series are auto-detected from the data — you don't need to specify them.
- For computed or synthesized results, use a markdown table instead (render_chart only works with data from a single tool call).
"""

        # Add email capability guidance
        has_system_email = any(t["connector"] == "norm_email" for t in tools)
        has_gmail = any(t["connector"] == "gmail" for t in tools)
        has_outlook = any(t["connector"] == "microsoft_outlook" for t in tools)

        if has_system_email or has_gmail or has_outlook:
            from app.db.models import ConnectorConfig as CC, User as UserModel

            # Look up the current user's email
            current_user_email = None
            if user_id:
                u = db.query(UserModel).filter(UserModel.id == user_id).first()
                if u:
                    current_user_email = u.email

            email_lines = ["\n\n## Email Capabilities"]
            if current_user_email:
                email_lines.append(
                    f"The current user's email address is **{current_user_email}**. Use this when they ask to send something to themselves or 'to me'."
                )
            if has_system_email:
                email_lines.append(
                    "- **Report email** (`norm_email__send_report_email`): Send your response as a formatted email. Just provide `to` and `subject`."
                )
            if has_gmail and user_id:
                cfg = (
                    db.query(CC)
                    .filter(CC.connector_name == "gmail", CC.user_id == user_id)
                    .first()
                )
                addr = (
                    (cfg.oauth_metadata or {}).get("email", "connected Gmail")
                    if cfg
                    else "connected Gmail"
                )
                email_lines.append(
                    f"- **Gmail** (`gmail__send_email`): Send from **{addr}**. Use for outreach, purchase orders, and correspondence that should come from the user."
                )
            if has_outlook and user_id:
                cfg = (
                    db.query(CC)
                    .filter(
                        CC.connector_name == "microsoft_outlook", CC.user_id == user_id
                    )
                    .first()
                )
                addr = (
                    (cfg.oauth_metadata or {}).get("email", "connected Outlook")
                    if cfg
                    else "connected Outlook"
                )
                email_lines.append(
                    f"- **Outlook** (`microsoft_outlook__send_email`): Send from **{addr}**. Use for outreach, purchase orders, and correspondence that should come from the user."
                )
            email_lines.append(
                "\nUse report email to send data and results from Norm. Use the user's connected account (Gmail/Outlook) when the email should appear to come from them personally."
            )
            system_prompt += "\n".join(email_lines)

        # Add venue guidance when multiple venues exist
        if len(user_venues) > 1:
            # Build per-venue connector availability
            venue_lines = []
            for v in user_venues:
                configs = (
                    db.query(ConnectorConfig)
                    .filter(
                        ConnectorConfig.venue_id == v.id,
                        ConnectorConfig.enabled == "true",
                    )
                    .all()
                )
                connector_names = [c.connector_name for c in configs]
                if connector_names:
                    venue_lines.append(
                        f"- {v.name} (connected to: {', '.join(connector_names)})"
                    )
                else:
                    venue_lines.append(f"- {v.name} (no connectors configured)")
            venue_detail = "\n".join(venue_lines)

            if active_venue_name:
                tz_info = ""
                if venue_timezone:
                    try:
                        from zoneinfo import ZoneInfo

                        tz = ZoneInfo(venue_timezone)
                        now = datetime.datetime.now(tz)
                        offset = now.strftime("%z")
                        offset_fmt = f"{offset[:3]}:{offset[3:]}"
                        today_in_tz = now.strftime("%Y-%m-%d")
                        tz_info = (
                            f" (timezone: {venue_timezone}, currently UTC{offset_fmt})"
                        )
                        tz_info += f"\nToday's date in this timezone is {today_in_tz}. When making API calls that require dates or datetimes, use the offset {offset_fmt} (URL-encoded as %2B{offset[1:3]}:{offset[3:]} for positive offsets)."
                    except Exception:
                        tz_info = f" (timezone: {venue_timezone})"

                system_prompt += f"""

## Active Venue
The user's active venue is **{active_venue_name}**{tz_info}. Use this as the default venue for all tool calls.
Do NOT ask the user which venue — use {active_venue_name} by default unless the user explicitly asks about a different venue.

Other available venues:
{venue_detail}

- Always include the venue name in the "venue" parameter of each tool call
- Only call tools for venues that have the relevant connector configured
- For cross-venue queries, include only venues that have the relevant connector
"""
            else:
                system_prompt += f"""

## Venues
The user has access to multiple venues:
{venue_detail}

- Always include the venue name in the "venue" parameter of each tool call
- If the user specifies a venue, use that venue name exactly
- If the user asks about "all venues" or wants to compare venues, make tool calls for each relevant venue in parallel
- If the request clearly needs a venue but none is specified, ask which one
- Only call tools for venues that have the relevant connector configured
- For cross-venue queries, only include venues that have the relevant connector
"""

        # Add page context so the agent knows what the user is viewing
        if page_context:
            _page_labels = {
                "roster": "Roster",
                "hiring": "Hiring",
                "orders": "Orders",
                "saved-reports": "Saved Reports",
                "tasks-hr": "HR Automated Tasks",
                "tasks-procurement": "Procurement Automated Tasks",
                "tasks-reports": "Reports Automated Tasks",
            }
            page_label = _page_labels.get(
                page_context["page_id"], page_context["page_id"]
            )
            system_prompt += f"""

## Current Page Context
The user is currently viewing the **{page_label}** page.
Their question likely relates to what they see on this page. Prioritize answers relevant to this context.
"""

        # Always add parallel tool-use guidance (standard mode only)
        system_prompt += """

## Tool Use
When you need to retrieve multiple independent pieces of data (e.g., sales data for several months, stock levels for several suppliers), call ALL the tools in a single response rather than one at a time. This executes them in parallel and is much faster. Only call tools sequentially when a later call depends on the result of an earlier one.
"""

    anthropic_tools: list[dict] = []
    seen_names: set[str] = set()
    for tool in tools:
        # Build tool name: connector__action (double underscore for easy parsing)
        tool_name = f"{tool['connector']}__{tool['action']}"
        if tool_name in seen_names:
            continue
        seen_names.add(tool_name)

        # Build properties from required_fields + optional_fields
        properties: dict = {}
        field_descs = tool.get("field_descriptions") or {}
        field_schemas = tool.get("field_schema") or {}
        all_fields = list(tool["required_fields"]) + list(
            tool.get("optional_fields") or []
        )
        for field in all_fields:
            # Use explicit schema if provided (supports nested objects/arrays)
            if field in field_schemas:
                prop = {**field_schemas[field]}
                if "description" not in prop:
                    prop["description"] = field_descs.get(field, field)
                properties[field] = prop
                continue

            api_name = tool["field_mapping"].get(field, field)
            desc_parts = []
            hint = field_descs.get(field, "")
            if hint:
                desc_parts.append(hint)
            if api_name != field:
                desc_parts.append(f"Maps to API field: {api_name}")
            prop = {
                "type": "string",
                "description": ". ".join(desc_parts) if desc_parts else field,
            }
            properties[field] = prop

        # Inject venue parameter for external connectors when multiple venues exist
        is_external = tool["connector"] != "norm" and not tool["connector"].startswith(
            "norm_"
        )
        if is_external and len(user_venues) > 1:
            configured_venues = [
                v.name
                for v in user_venues
                if db.query(ConnectorConfig)
                .filter(
                    ConnectorConfig.connector_name == tool["connector"],
                    ConnectorConfig.venue_id == v.id,
                    ConnectorConfig.enabled == "true",
                )
                .count()
                > 0
            ]
            if configured_venues:
                properties["venue"] = {
                    "type": "string",
                    "description": f"Venue name. Available for: {', '.join(configured_venues)}.",
                    "enum": configured_venues,
                }

        method = tool["method"].upper()
        desc = tool.get("description") or tool["action"].replace("_", " ")
        desc_full = f"[{method}] {desc}"

        anthropic_tools.append(
            {
                "name": tool_name,
                "description": desc_full,
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": tool["required_fields"],
                    "additionalProperties": False,
                },
            }
        )

    # Unified tool filtering — priority: explicit tool_filter > playbook > agent default
    _ALWAYS_INCLUDE = {"resolve_dates"}  # search_tool_result injected on truncation
    active_filter = tool_filter or (
        playbook.tool_filter
        if playbook and getattr(playbook, "tool_filter", None)
        else None
    )
    if active_filter:
        allowed = set(active_filter) | _ALWAYS_INCLUDE
        anthropic_tools = [
            t
            for t in anthropic_tools
            if t["name"].split("__", 1)[-1] in allowed or t["name"] in allowed
        ]
    else:
        # Agent default: derive allowed actions from this agent's connector bindings
        from app.db.models import AgentConnectorBinding

        agent_bindings = (
            _cdb.query(AgentConnectorBinding)
            .filter(
                AgentConnectorBinding.agent_slug == domain,
                AgentConnectorBinding.enabled == True,  # noqa: E712
            )
            .all()
        )
        if agent_bindings:
            agent_actions: set[str] = set()
            for ab in agent_bindings:
                for cap in ab.capabilities or []:
                    if cap.get("enabled", True):
                        agent_actions.add(cap["action"])
            if agent_actions:
                allowed = agent_actions | _ALWAYS_INCLUDE
                anthropic_tools = [
                    t
                    for t in anthropic_tools
                    if t["name"].split("__", 1)[-1] in allowed or t["name"] in allowed
                ]

    logger.info(
        "Built %d Anthropic tool definitions for domain=%s (playbook=%s)",
        len(anthropic_tools),
        domain,
        playbook.slug if playbook else None,
    )
    return system_prompt, anthropic_tools
