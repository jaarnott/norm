"""Build agent system prompts dynamically from active connector specs."""

import datetime
import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _collect_tools(domain: str, db: Session) -> list[dict]:
    """Collect all enabled tools from connector specs bound to a domain agent.

    Returns a list of dicts with keys: action, connector, required_fields,
    field_mapping, method, description.
    """
    from app.db.models import AgentConnectorBinding, ConnectorConfig, ConnectorSpec

    bindings = (
        db.query(AgentConnectorBinding)
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
            db.query(ConnectorSpec)
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
                .count() > 0
            )
            if not has_config:
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


def build_dynamic_prompt(domain: str, db: Session) -> str | None:
    """Return the DB-stored system prompt if connector specs are bound.

    Returns None if no connector specs are bound, signalling the caller to
    use the DB-stored prompt directly via agent_config_service.
    """
    tools = _collect_tools(domain, db)
    if not tools:
        return None

    # Tools are bound — return the DB prompt (the admin manages it in Settings)
    from app.services.agent_config_service import get_system_prompt
    return get_system_prompt(domain, db) or None


def build_tool_definitions(domain: str, db: Session, active_venue_name: str | None = None, venue_timezone: str | None = None) -> tuple[str, list[dict]]:
    """Build a system prompt AND Anthropic-format tool definitions for the agentic loop.

    Returns (system_prompt, anthropic_tools) where anthropic_tools is a list
    of tool dicts in the format expected by the Anthropic tool-use API.

    Returns ("", []) if no tools are bound.
    """
    tools = _collect_tools(domain, db)
    if not tools:
        return "", []

    # System prompt comes directly from the DB — the admin manages the full
    # prompt in the Settings UI. Supports {{today}} placeholder.
    from app.services.agent_config_service import get_system_prompt
    system_prompt = get_system_prompt(domain, db)
    if not system_prompt:
        system_prompt = f"You are the {domain} agent for Norm, a hospitality operations platform."
    system_prompt = system_prompt.replace('{{today}}', datetime.date.today().isoformat())

    # Add automated tasks guidance if those tools are available
    has_automated_tasks = any(t.get("action") == "create_automated_task" for t in tools)
    if has_automated_tasks:
        system_prompt += """

## Automated Tasks
You can create automated tasks that run on a schedule or on demand. When a user asks you to do something regularly (e.g., "check candidates every day", "send me a weekly sales report"), use the `create_automated_task` tool to set it up.

- Use `create_automated_task` when the user wants something done regularly or automatically
- Set `agent_slug` to your domain (e.g., "hr", "procurement", "reports")
- Write a clear, self-contained `prompt` — it will be sent to the agent on each run without any conversation context
- Choose the right `schedule_type`: "daily", "weekly", "monthly", or "manual" (trigger only)
- Set `schedule_config` with appropriate time fields: `{hour, minute}` for daily, `{day_of_week, hour, minute}` for weekly
- The task is created as a draft — the user can test it and then activate it
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
Use `norm__search_tool_result` with the `tool_call_id` and a short search `query` to find matching items.
Keep your search query to just the core keyword — for example, if the user asks for "corona beer boxes", search for "corona".
The search uses fuzzy matching so it handles misspellings and partial matches. It returns up to 20 results ranked by relevance.
"""

    # Add venue guidance when multiple venues exist
    from app.services.venue_service import get_user_venues
    from app.db.models import ConnectorConfig
    user_venues = get_user_venues(db)
    if len(user_venues) > 1:
        # Build per-venue connector availability
        venue_lines = []
        for v in user_venues:
            configs = db.query(ConnectorConfig).filter(
                ConnectorConfig.venue_id == v.id,
                ConnectorConfig.enabled == "true",
            ).all()
            connector_names = [c.connector_name for c in configs]
            if connector_names:
                venue_lines.append(f"- {v.name} (connected to: {', '.join(connector_names)})")
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
                    offset = now.strftime('%z')
                    offset_fmt = f"{offset[:3]}:{offset[3:]}"
                    today_in_tz = now.strftime('%Y-%m-%d')
                    tz_info = f" (timezone: {venue_timezone}, currently UTC{offset_fmt})"
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

## Venue Context
The user has access to multiple venues:
{venue_detail}

When querying data, include the venue name in the "venue" parameter of each tool call.
- If the user specifies a venue, use that venue name exactly
- If the user doesn't specify and there are multiple venues, ask which one they mean
- Only call tools for venues that have the relevant connector configured
- If the user asks about a venue with no connectors, tell them it needs to be set up in Settings > Venues
- For cross-venue queries, only include venues that have the relevant connector
"""

    # Always add parallel tool-use guidance
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
        all_fields = list(tool["required_fields"]) + list(tool.get("optional_fields") or [])
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
        is_external = tool["connector"] != "norm" and not tool["connector"].startswith("norm_")
        if is_external and len(user_venues) > 1:
            configured_venues = [
                v.name for v in user_venues
                if db.query(ConnectorConfig).filter(
                    ConnectorConfig.connector_name == tool["connector"],
                    ConnectorConfig.venue_id == v.id,
                    ConnectorConfig.enabled == "true",
                ).count() > 0
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

        anthropic_tools.append({
            "name": tool_name,
            "description": desc_full,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": tool["required_fields"],
                "additionalProperties": False,
            },
        })

    logger.info(
        "Built %d Anthropic tool definitions for domain=%s",
        len(anthropic_tools),
        domain,
    )
    return system_prompt, anthropic_tools
