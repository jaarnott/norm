"""Build agent system prompts dynamically from active connector specs."""

import datetime
import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _collect_tools(
    domain: str,
    db: Session,
    user_id: str | None = None,
    config_db: Session | None = None,
) -> list[dict]:
    """Collect all enabled tools from connector specs bound to a domain agent.

    Returns a list of dicts with keys: action, connector, required_fields,
    field_mapping, method, description.

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
            AgentConnectorBinding.agent_slug == domain,
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

    return tools


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
    tools = _collect_tools(domain, db, config_db=_cdb)
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
) -> tuple[str, list[dict]]:
    """Build a system prompt AND Anthropic-format tool definitions for the agentic loop.

    Returns (system_prompt, anthropic_tools) where anthropic_tools is a list
    of tool dicts in the format expected by the Anthropic tool-use API.

    Returns ("", []) if no tools are bound.
    """
    _cdb = config_db
    if _cdb is None:
        raise RuntimeError(
            "config_db is required — check that config_db is passed through the call chain"
        )
    tools = _collect_tools(domain, db, user_id=user_id, config_db=_cdb)
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
When a user asks to do something regularly or automatically, use `create_automated_task`:
- Set `intent` to describe what the task should do, including specifics from the conversation (venue names, employee names, items, email requests). Be detailed — the intent is used to generate a self-contained task prompt.
- Set `agent_slug` to the appropriate domain ("hr", "procurement", or "reports")
- Set `schedule` if the user specified when it should run (e.g. "daily at 9am", "every monday at 8am")
- The task will be created as a draft for the user to review and activate
"""

        # Add chart visualization guidance if render_chart tool is available
        has_render_chart = any(t.get("action") == "render_chart" for t in tools)
        if has_render_chart:
            system_prompt += """

## Chart Visualization
When presenting data from a tool call, use the `render_chart` tool to create a visual chart.
- Set `source_tool_call_id` to the tool_use ID of the GET tool call whose data you want to visualize. This is the `id` field from the tool_use block in the conversation. The chart pulls data directly from that tool call's stored result — do NOT pass the data yourself.
- Use `select_fields` to pick only the fields needed for the chart (e.g., `["startTime", "invoices"]`). This keeps the chart clean by excluding irrelevant fields.
- Use `field_labels` to give fields readable display names (e.g., `{"startTime": "Date", "invoices": "Sales ($)"}`). Dates are auto-formatted on the frontend but labels make axes and legends clearer.
- Choose the most appropriate chart_type:
  - "bar" for comparing categories or time periods
  - "line" for trends over time
  - "pie" for parts of a whole (< 8 categories)
  - "stacked_bar" for multiple series comparison
  - "scatter" for correlation between two numeric variables
  - "table" when exact numbers matter more than visual patterns
- Set `x_axis_key` to the field name for the x-axis (e.g., "startTime")
- Set `series` to an array of objects with `key` and `label` for each data series
- Always provide a clear, descriptive `title`
- Only use render_chart for data that came from a single tool call. For computed or synthesized results, use a markdown table instead.

## Large Results & Search
When a tool result is too large to display, you'll see `_too_large` with a `_sample_item` showing available fields.
Use `norm__search_tool_result` to search, sort, or find top items in large results:
- **Text search**: provide `query` with a keyword (fuzzy matching). Keep it to the core keyword — e.g. "corona" not "corona beer boxes".
- **Sort by value**: provide `sort_by` with a field name (e.g. "amount") and optionally `sort_order` ("desc" or "asc", default "desc"). No `query` needed.
- **Top N**: provide `top_n` to limit results (default 20). Combine with `sort_by` to get e.g. "top 5 by amount".
- **Combine**: search for a keyword AND sort the matches — e.g. query="Tuesday", sort_by="amount", sort_order="desc", top_n=10.
Example: to find the 5 highest sales periods, use: sort_by="amount", sort_order="desc", top_n=5
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
                    "- **System email** (`norm_email__send_notification`): Send notifications from the platform (e.g., task results, alerts, reports). Uses a template system."
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
                "\nUse system email for automated notifications and alerts. Use the user's connected account when the email should appear to come from them personally."
            )
            if has_automated_tasks:
                email_lines.append(
                    "You can create automated tasks that send emails on a schedule (e.g., daily reports, weekly summaries)."
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

    # Apply playbook tool filter — keep only tools in the filter list
    if playbook and playbook.tool_filter:
        allowed = set(playbook.tool_filter)
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
