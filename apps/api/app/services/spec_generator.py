"""AI-assisted connector spec generation from API documentation."""

import json
import re

from sqlalchemy.orm import Session

from app.interpreter.llm_interpreter import call_llm


_SPEC_GENERATION_SYSTEM_PROMPT = """\
You are a connector specification generator. Given API documentation, produce a \
ConnectorSpec JSON that can be used to configure a config-driven connector.

You MUST respond with ONLY a valid JSON object matching this schema:

{
  "connector_name": "lowercase_snake_case",
  "display_name": "Human Readable Name",
  "category": "hr" | "procurement" | "finance" | "communication" | "other",
  "execution_mode": "template" | "agent",
  "auth_type": "bearer" | "api_key_header" | "basic" | "oauth2",
  "auth_config": {
    // For bearer: {"token_field": "api_key"}
    // For api_key_header: {"header_name": "X-API-Key", "key_field": "api_key"}
    // For basic: {"username_field": "api_key", "password": "x"} or {"username_field": "username", "password_field": "password"}
    // For oauth2: {"token_field": "access_token"}
  },
  "base_url_template": "https://{{ creds.subdomain }}.example.com/api/v1",
  "credential_fields": [
    {"key": "api_key", "label": "API Key", "secret": true},
    {"key": "subdomain", "label": "Subdomain", "secret": false}
  ],
  "tools": [
    {
      "action": "action_name",
      "description": "A clear, concise description of what this tool does — shown to the LLM to help it decide when to use it",
      "method": "POST",
      "path_template": "/resource/",
      "headers": {"Accept": "application/json", "Content-Type": "application/json"},
      "required_fields": ["field1", "field2"],
      "field_mapping": {"source_field": "apiFieldName"},
      "field_descriptions": {"field1": "Date in YYYY-MM-DD format (e.g., 2026-03-16)", "field2": "Numeric employee ID (e.g., 12345)"},
      "request_body_template": "{{ ... Jinja2 template producing JSON ... }}",
      "success_status_codes": [200, 201],
      "response_ref_path": "id",
      "timeout_seconds": 30
    }
  ]
}

Available Jinja2 filters for request_body_template:
- split_name(name, "first"|"last") — splits "Sarah Johnson" into parts
- format_date(date_str, format) — reformats dates
- flatten_venue(venue_dict) — extracts venue name from dict
- default_if_none(value, default) — safe null handling

Guidelines:
- Use "template" execution_mode when the API is well-documented with clear endpoints.
- Use "agent" execution_mode when the API is complex or poorly documented.
- For template mode, write precise Jinja2 request_body_template strings.
- Identify all CRUD operations visible in the docs.
- ALWAYS include a "description" for each tool. Write a concise sentence describing \
what the tool does (e.g., "Get all rostered shifts for a given date range", \
"Create a new purchase order"). This is shown to the LLM to help it choose the right tool.
- IMPORTANT: required_fields MUST include ALL fields referenced as {{ field_name }} in \
path_template AND request_body_template. Any Jinja2 variable used in the templates \
(except those under `creds.`) must appear in required_fields AND field_mapping. For \
example, if path_template is "/sales?start={{ start }}&end={{ end }}", then \
required_fields must include "start" and "end", and field_mapping must include \
{"start": "start", "end": "end"}.
- Be conservative with required_fields — only mark fields truly required by the API, \
but always include fields used in templates.
- ALWAYS provide field_descriptions for every field in required_fields. Describe the \
expected format, type, and any constraints AND include a concrete example value in \
parentheses at the end using "e.g." — for example: "Date in YYYY-MM-DD format \
(e.g., 2026-03-16)", "Start time in ISO 8601 format with timezone (e.g., \
2026-03-16T06:00:00+13:00)", "Numeric employee ID (e.g., 12345)", "Timespan in \
d.HH:mm:ss format (e.g., 0.08:00:00)". The example in parentheses is used to \
pre-populate test fields in the UI so users can try the tool immediately. These \
descriptions help the LLM extract values in the correct format from user messages.
- Respond with valid JSON only, no markdown fences or explanations.
"""


def generate_connector_spec(api_docs: str, db: Session) -> dict:
    """Generate a connector spec from API documentation using an LLM.

    Returns the parsed spec dict for admin review before saving.
    """
    parsed, llm_call_id = call_llm(
        system_prompt=_SPEC_GENERATION_SYSTEM_PROMPT,
        user_prompt=f"## API Documentation\n\n{api_docs}",
        db=db,
        call_type="spec_generation",
    )

    # Ensure required fields have defaults
    parsed.setdefault("execution_mode", "template")
    parsed.setdefault("auth_config", {})
    parsed.setdefault("tools", [])
    parsed.setdefault("credential_fields", [])
    parsed.setdefault("example_requests", [])

    # Post-process: ensure fields referenced in templates are in required_fields
    _backfill_required_fields(parsed)

    return {
        "spec": parsed,
        "llm_call_id": llm_call_id,
    }


# Matches {{ field_name }} or {{ field_name | filter(...) }}, ignoring creds.*
_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\|[^}]*)?\}\}")


def _backfill_required_fields(spec: dict) -> None:
    """Ensure any Jinja2 variable in path_template or request_body_template
    is present in the tool's required_fields and field_mapping."""
    for tool in spec.get("tools", []):
        required = list(tool.get("required_fields", []))
        required_set = set(required)
        field_mapping = dict(tool.get("field_mapping", {}) or {})

        for tpl_key in ("path_template", "request_body_template"):
            template = tool.get(tpl_key) or ""
            for match in _TEMPLATE_VAR_RE.finditer(template):
                field = match.group(1)
                # Skip credential references (handled via creds.*)
                if field == "creds":
                    continue
                if field not in required_set:
                    required.append(field)
                    required_set.add(field)
                # Ensure field appears in field_mapping so the UI shows it
                if field not in field_mapping:
                    field_mapping[field] = field

        tool["required_fields"] = required
        tool["field_mapping"] = field_mapping
        tool.setdefault("field_descriptions", {})
