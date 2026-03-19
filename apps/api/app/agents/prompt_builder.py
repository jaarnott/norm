"""Build agent system prompts dynamically from active connector specs."""

import json
import logging
import re

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

        # Gate on having an active connection (enabled ConnectorConfig)
        config_row = (
            db.query(ConnectorConfig)
            .filter(
                ConnectorConfig.connector_name == binding.connector_name,
                ConnectorConfig.enabled == "true",
            )
            .first()
        )
        if not config_row:
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
                    "field_mapping": tool.get("field_mapping", {}),
                    "field_descriptions": tool.get("field_descriptions", {}),
                    "field_schema": tool.get("field_schema", {}),
                    "method": tool.get("method", "POST"),
                    "description": tool.get("description", ""),
                }
            )

    return tools


def build_dynamic_prompt(domain: str, db: Session) -> str | None:
    """Build a system prompt that includes all available tools from bound connector specs.

    Returns None if no connector specs are bound, signalling the caller to
    fall back to the hardcoded / DB-stored prompt.
    """
    tools = _collect_tools(domain, db)
    if not tools:
        return None

    # Build tools block
    tools_lines: list[str] = []
    for tool in tools:
        mapping_hint = ""
        if tool["field_mapping"]:
            mapping_hint = f"\n  Field mapping hints: {json.dumps(tool['field_mapping'])}"
        desc_hint = ""
        if tool.get("field_descriptions"):
            desc_hint = f"\n  Field formats: {json.dumps(tool['field_descriptions'])}"
        tools_lines.append(
            f"- action: {tool['action']}\n"
            f"  connector: {tool['connector']}\n"
            f"  method: {tool['method']}\n"
            f"  required_fields: {json.dumps(tool['required_fields'])}\n"
            f"  description: {tool.get('description') or tool['action'].replace('_', ' ')}"
            + mapping_hint
            + desc_hint
        )

    tools_block = "\n".join(tools_lines)

    prompt = f"""\
You are the {domain} interpretation layer for Norm, a hospitality operations platform.
Your ONLY job is to understand user messages and return structured JSON.
You do NOT execute actions, write to databases, or call external systems.

## Available tools

{tools_block}

## Response schema

You must return valid JSON matching this exact schema:
{{
  "domain": "{domain}",
  "intent": "{domain}.<action>",
  "action": "<one of the available actions above>",
  "connector": "<connector name for the chosen action>",
  "confidence": 0.0-1.0,
  "is_followup": true | false,
  "extracted_fields": {{
    // fields extracted from the user message
  }},
  "candidate_matches": {{
    // fuzzy match candidates, e.g. venue_raw, venue_candidate
  }},
  "missing_fields": ["field1", "field2"],
  "clarification_needed": true | false,
  "clarification_question": "string or null",
  "summary": "brief summary"
}}

## Date awareness

Each user message is prefixed with today's date in `[YYYY-MM-DD]` format. Use this date to resolve relative time references like "today", "this week", "yesterday", "last month", etc.

## Rules

1. Select the best matching **action** based on the user's intent. Set the "action" and "connector" fields accordingly.
2. Match venue names fuzzily. "zeppa" = "La Zeppa".
3. Match other entity names fuzzily where applicable (products, employees, etc.).
4. The "required_fields" listed for each action are the fields you should try to extract.
5. If there is an open task and the message looks like a reply, set is_followup=true.
6. If is_followup=true, only include fields that the NEW message provides or changes.
7. Write natural, concise clarification questions when fields are missing.
8. Set confidence based on how certain you are about the interpretation.
9. Set "intent" to "{domain}.<action>" using the chosen action name.
"""

    logger.info(
        "Built dynamic prompt for domain=%s with %d tools from %d bindings",
        domain,
        len(tools),
        len(bindings := []),  # just for the log count
    )
    return prompt


def build_tool_definitions(domain: str, db: Session) -> tuple[str, list[dict]]:
    """Build a system prompt AND Anthropic-format tool definitions for the agentic loop.

    Returns (system_prompt, anthropic_tools) where anthropic_tools is a list
    of tool dicts in the format expected by the Anthropic tool-use API.

    Returns ("", []) if no tools are bound.
    """
    tools = _collect_tools(domain, db)
    if not tools:
        return "", []

    # Only include domain-specific instructions if the user has explicitly
    # customized the prompt in the Settings UI.  The hardcoded defaults are
    # interpretation prompts ("return JSON only") that conflict with tool-use.
    from app.db.models import AgentConfig
    config_row = db.query(AgentConfig).filter(AgentConfig.agent_slug == domain).first()
    domain_instructions = config_row.system_prompt if config_row and config_row.system_prompt is not None else None

    domain_section = ""
    if domain_instructions and domain_instructions.strip():
        domain_section = f"""
## Domain-specific instructions
{domain_instructions}
"""

    system_prompt = f"""\
You are the {domain} agent for Norm, a hospitality operations platform.
You help users by using the available tools to query data and perform actions.

## Date awareness
Each user message is prefixed with today's date in `[YYYY-MM-DD]` format. Use this date to resolve relative time references like "today", "this week", "yesterday", "last month", etc. When calling tools that require date parameters, calculate the correct dates from this prefix.

## Rules
- Use tools to gather information needed to answer the user's question.
- You may call multiple tools in sequence to build a complete picture.
- For queries that span multiple periods (e.g. "last 4 Sundays", "each day last week"), make separate tool calls for each period, then combine and summarise the results.
- For read-only tools (GET), proceed immediately — they execute automatically.
- For write tools (POST/PUT/DELETE), describe what you plan to do and call the tool — the user will be asked to approve before it executes.
- Always explain what you found or did in clear, natural language.
- If you need more information from the user, ask a clear question.
- Match entity names fuzzily: "zeppa" = "La Zeppa", "jb" = "Jim Beam".
- If there is only ONE venue in the context, use it automatically — do NOT ask the user to choose.
- Prefer action over clarification. For read operations, make reasonable assumptions and proceed. Only ask for clarification when essential info is truly missing for a write operation.
- Be concise and helpful.
- IMPORTANT: When calling tools, use the EXACT format specified in the field description. If it says "ISO 8601 format with timezone (e.g., 2026-03-23T07:00:00+13:00)", use that exact format including the timezone offset. Do NOT use other date formats. The example value in the description shows the correct format — follow it precisely.
- IMPORTANT: When you are about to call a tool, you MUST start your text response with the prefix "[Tool] " followed by a brief explanation of what you are looking up or doing and why. Example: "[Tool] Looking up staff orders for last week to find Arthur's sales data." Do NOT use the [Tool] prefix when giving your final answer to the user.
- Tool results may be slimmed (showing only key fields) or too large to display. Look for `_slimmed: true` or `_too_large: true` in results. Use `norm__search_tool_result` to search the full data by keyword or get complete details for specific items. Never assume an item doesn't exist just because you can't see it — always search first.
{domain_section}
## Formatting
- Write a concise natural language summary of results. Structured data may be displayed separately.
- If presenting tabular data in text, use markdown tables.
- For confirmations, use **bold** labels: **Reference**: ORD-12345.
- Keep summaries brief — highlight counts, key facts, and anything unusual.
- When summarising data across multiple periods, present a comparison table showing each period side by side.
"""

    anthropic_tools: list[dict] = []
    seen_names: set[str] = set()
    for tool in tools:
        # Build tool name: connector__action (double underscore for easy parsing)
        tool_name = f"{tool['connector']}__{tool['action']}"
        if tool_name in seen_names:
            continue
        seen_names.add(tool_name)

        # Build properties from required_fields
        properties: dict = {}
        field_descs = tool.get("field_descriptions") or {}
        field_schemas = tool.get("field_schema") or {}
        for field in tool["required_fields"]:
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
            # Extract example from hint "(e.g., ...)" and add as schema example
            if hint:
                ex_match = re.search(r'\(e\.g\.?,?\s*(.+?)\)\s*$', hint)
                if ex_match:
                    prop["examples"] = [ex_match.group(1).strip()]
            properties[field] = prop

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

    # Add the built-in search tool for large results
    anthropic_tools.append({
        "name": "norm__search_tool_result",
        "description": (
            "[GET] Search through a previous tool call's full result by keyword. "
            "Use when a result was too large or slimmed (_slimmed or _too_large) "
            "and you need to find specific items or get full details."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_call_id": {
                    "type": "string",
                    "description": "The _tool_call_id from the slimmed/large result",
                },
                "query": {
                    "type": "string",
                    "description": "Search keyword (case-insensitive match across all field values)",
                },
                "fields": {
                    "type": "string",
                    "description": "Optional: comma-separated field names to return. Omit for all fields.",
                },
            },
            "required": ["tool_call_id", "query"],
            "additionalProperties": False,
        },
    })

    logger.info(
        "Built %d Anthropic tool definitions for domain=%s",
        len(anthropic_tools),
        domain,
    )
    return system_prompt, anthropic_tools
