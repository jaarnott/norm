"""AI-assisted connector spec generation from API documentation."""

import json

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
  "operations": [
    {
      "action": "action_name",
      "method": "POST",
      "path_template": "/resource/",
      "headers": {"Accept": "application/json", "Content-Type": "application/json"},
      "required_fields": ["field1", "field2"],
      "field_mapping": {"source_field": "apiFieldName"},
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
- Be conservative with required_fields — only mark fields truly required by the API.
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
    parsed.setdefault("operations", [])
    parsed.setdefault("credential_fields", [])
    parsed.setdefault("example_requests", [])

    return {
        "spec": parsed,
        "llm_call_id": llm_call_id,
    }
