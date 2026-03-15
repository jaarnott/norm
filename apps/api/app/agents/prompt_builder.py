"""Build agent system prompts dynamically from active connector specs."""

import json
import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def build_dynamic_prompt(domain: str, db: Session) -> str | None:
    """Build a system prompt that includes all available operations from bound connector specs.

    Returns None if no connector specs are bound, signalling the caller to
    fall back to the hardcoded / DB-stored prompt.
    """
    from app.db.models import AgentConnectorBinding, ConnectorSpec

    bindings = (
        db.query(AgentConnectorBinding)
        .filter(
            AgentConnectorBinding.agent_slug == domain,
            AgentConnectorBinding.enabled == True,  # noqa: E712
        )
        .all()
    )

    if not bindings:
        return None

    # Collect operations across all bound connector specs
    operations: list[dict] = []
    for binding in bindings:
        spec = (
            db.query(ConnectorSpec)
            .filter(
                ConnectorSpec.connector_name == binding.connector_name,
                ConnectorSpec.enabled == True,  # noqa: E712
            )
            .first()
        )
        if not spec:
            continue

        # Build an enabled-action set from binding capabilities
        enabled_actions: set[str] | None = None
        if binding.capabilities:
            enabled_actions = {
                cap["action"]
                for cap in binding.capabilities
                if cap.get("enabled", True)
            }

        for op in spec.operations or []:
            action = op.get("action", "")
            # Skip actions explicitly disabled in the binding
            if enabled_actions is not None and action not in enabled_actions:
                continue

            operations.append(
                {
                    "action": action,
                    "connector": spec.connector_name,
                    "required_fields": op.get("required_fields", []),
                    "field_mapping": op.get("field_mapping", {}),
                    "method": op.get("method", "POST"),
                    "description": op.get("description", ""),
                }
            )

    if not operations:
        return None

    # Build operations block
    ops_lines: list[str] = []
    for op in operations:
        mapping_hint = ""
        if op["field_mapping"]:
            mapping_hint = f"  Field mapping hints: {json.dumps(op['field_mapping'])}"
        ops_lines.append(
            f"- action: {op['action']}\n"
            f"  connector: {op['connector']}\n"
            f"  method: {op['method']}\n"
            f"  required_fields: {json.dumps(op['required_fields'])}\n"
            f"  description: {op.get('description') or op['action'].replace('_', ' ')}"
            + (f"\n{mapping_hint}" if mapping_hint else "")
        )

    operations_block = "\n".join(ops_lines)

    prompt = f"""\
You are the {domain} interpretation layer for Norm, a hospitality operations platform.
Your ONLY job is to understand user messages and return structured JSON.
You do NOT execute actions, write to databases, or call external systems.

## Available operations

{operations_block}

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

## Rules

1. Select the best matching **action** based on the user's intent. Set the "action" and "connector" fields accordingly.
2. Match venue names fuzzily. "Murdochs" = "Mr Murdoch's", "Freeman and Grey" = "Freeman & Grey", "zeppa" = "La Zeppa".
3. Match other entity names fuzzily where applicable (products, employees, etc.).
4. The "required_fields" listed for each action are the fields you should try to extract.
5. If there is an open task and the message looks like a reply, set is_followup=true.
6. If is_followup=true, only include fields that the NEW message provides or changes.
7. Write natural, concise clarification questions when fields are missing.
8. Set confidence based on how certain you are about the interpretation.
9. Set "intent" to "{domain}.<action>" using the chosen action name.
"""

    logger.info(
        "Built dynamic prompt for domain=%s with %d operations from %d bindings",
        domain,
        len(operations),
        len(bindings),
    )
    return prompt
