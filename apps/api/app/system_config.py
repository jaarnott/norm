"""Declarative system configuration for connector specs, agents, and bindings.

These definitions are the source of truth for system-level configuration.
They are synced to the database on every deploy (app startup) so that code
changes to connector specs, agent definitions, and bindings are always
reflected in production without manual API calls.

User/environment configuration (credentials, custom prompts, enabled flags
set by admins) is never overwritten by the sync.
"""

# ---------------------------------------------------------------------------
# Connector Specs — define what integrations exist and their available tools
# ---------------------------------------------------------------------------

CONNECTOR_SPECS: list[dict] = [
    {
        "connector_name": "norm",
        "display_name": "Norm",
        "category": "internal",
        "execution_mode": "internal",
        "auth_type": "none",
        "tools": [
            {
                "action": "search_tool_result",
                "method": "GET",
                "description": (
                    "Search through a previous tool call's full result by keyword. "
                    "Use when a result was too large or slimmed and you need to "
                    "find specific items."
                ),
                "required_fields": ["tool_call_id", "query"],
                "optional_fields": ["fields"],
                "field_descriptions": {
                    "tool_call_id": "The _tool_call_id from the slimmed/large result",
                    "query": "Search keyword (case-insensitive match across all field values)",
                    "fields": "Optional: comma-separated field names to return. Omit for all fields.",
                },
            },
        ],
    },
    {
        "connector_name": "norm_reports",
        "display_name": "Norm Reports",
        "category": "reports",
        "execution_mode": "internal",
        "auth_type": "none",
        "tools": [
            {
                "action": "render_chart",
                "method": "GET",
                "description": (
                    "Render data as a visual chart by referencing a prior tool call."
                ),
                "required_fields": [
                    "title",
                    "chart_type",
                    "x_axis_key",
                    "series",
                    "source_tool_call_id",
                ],
                "optional_fields": [
                    "x_axis_label",
                    "orientation",
                    "select_fields",
                    "field_labels",
                ],
                "field_descriptions": {
                    "source_tool_call_id": (
                        "The tool_use ID of the GET tool call whose data to visualize."
                    ),
                    "title": "Chart title",
                    "chart_type": "bar, line, pie, stacked_bar, scatter, bubble, or table",
                    "x_axis_key": "Field name from the data for x-axis",
                    "series": "Array of {key, label} objects for data series to plot",
                    "select_fields": "Array of field names to include from the raw data",
                    "field_labels": "Object mapping raw field names to display labels",
                },
                "field_schema": {
                    "series": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "key": {"type": "string"},
                                "label": {"type": "string"},
                            },
                        },
                    },
                    "select_fields": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "field_labels": {
                        "type": "object",
                    },
                },
                "display_component": "chart",
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Agent Configs — define the known agent slugs and their default metadata
# ---------------------------------------------------------------------------

AGENT_CONFIGS: list[dict] = [
    {
        "agent_slug": "router",
        "display_name": "Router",
        "description": "Routes user messages to the appropriate specialist agent.",
    },
    {
        "agent_slug": "procurement",
        "display_name": "Procurement",
        "description": "Handles ordering, supplier queries, and procurement workflows.",
    },
    {
        "agent_slug": "hr",
        "display_name": "HR",
        "description": "Handles employee onboarding, leave, and HR administration.",
    },
    {
        "agent_slug": "reports",
        "display_name": "Reports",
        "description": "Generates data visualizations and analytical reports.",
    },
]


# ---------------------------------------------------------------------------
# Agent ↔ Connector Bindings — wire agents to their connector specs
#
# capabilities list defines which tools from the spec are enabled by default.
# Set enabled=True for tools that should be on for new deployments.
# ---------------------------------------------------------------------------

AGENT_BINDINGS: list[dict] = [
    # Every agent gets the norm search tool
    {
        "agent_slug": "router",
        "connector_name": "norm",
        "capabilities": [
            {
                "action": "search_tool_result",
                "label": "Search through a previous tool call's full result by keyword.",
                "enabled": True,
            },
        ],
    },
    {
        "agent_slug": "procurement",
        "connector_name": "norm",
        "capabilities": [
            {
                "action": "search_tool_result",
                "label": "Search through a previous tool call's full result by keyword.",
                "enabled": True,
            },
        ],
    },
    {
        "agent_slug": "hr",
        "connector_name": "norm",
        "capabilities": [
            {
                "action": "search_tool_result",
                "label": "Search through a previous tool call's full result by keyword.",
                "enabled": True,
            },
        ],
    },
    {
        "agent_slug": "reports",
        "connector_name": "norm",
        "capabilities": [
            {
                "action": "search_tool_result",
                "label": "Search through a previous tool call's full result by keyword.",
                "enabled": True,
            },
        ],
    },
    # Reports agent gets the charting tool
    {
        "agent_slug": "reports",
        "connector_name": "norm_reports",
        "capabilities": [
            {
                "action": "render_chart",
                "label": "Render data as a visual chart",
                "enabled": True,
            },
        ],
    },
]
