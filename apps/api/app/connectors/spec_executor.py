"""Config-driven connector execution engine.

Supports two execution modes:
- **template**: Jinja2 renders the HTTP request deterministically
- **agent**: LLM generates the HTTP request from API docs + extracted fields
"""

import json
import logging
import time
from dataclasses import dataclass, asdict

import httpx
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy.orm import Session

from app.connectors.base import ConnectorResult
from app.connectors.template_filters import TEMPLATE_FILTERS

logger = logging.getLogger(__name__)


@dataclass
class RenderedRequest:
    method: str
    url: str
    headers: dict
    body: dict | str | None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_audit_dict(self) -> dict:
        """Version safe for DB storage — redacts auth headers."""
        d = self.to_dict()
        if d.get("headers"):
            redacted = {}
            for k, v in d["headers"].items():
                if k.lower() in ("authorization", "x-api-key"):
                    redacted[k] = "••••••••"
                else:
                    redacted[k] = v
            d["headers"] = redacted
        return d


# ---------------------------------------------------------------------------
# Jinja2 environment (shared, sandboxed)
# ---------------------------------------------------------------------------

def _build_jinja_env() -> SandboxedEnvironment:
    env = SandboxedEnvironment(
        autoescape=True,
    )
    env.filters.update(TEMPLATE_FILTERS)
    return env


_jinja_env = _build_jinja_env()


# ---------------------------------------------------------------------------
# Auth application
# ---------------------------------------------------------------------------

def _apply_auth(headers: dict, auth_type: str, auth_config: dict, credentials: dict, *, spec=None, db: Session | None = None, venue_id: str | None = None) -> tuple[dict, tuple | None]:
    """Apply authentication to request headers. Returns (headers, httpx_auth_tuple_or_None)."""
    httpx_auth = None

    if auth_type == "bearer":
        token_field = auth_config.get("token_field", "api_key")
        token = credentials.get(token_field, "")
        headers["Authorization"] = f"Bearer {token}"

    elif auth_type == "api_key_header":
        header_name = auth_config.get("header_name", "X-API-Key")
        key_field = auth_config.get("key_field", "api_key")
        headers[header_name] = credentials.get(key_field, "")

    elif auth_type == "basic":
        username_field = auth_config.get("username_field", "username")
        password_field = auth_config.get("password_field", "password")
        password = auth_config.get("password") or credentials.get(password_field, "")
        httpx_auth = (credentials.get(username_field, ""), password)

    elif auth_type == "oauth2":
        token = ""
        if spec and db and spec.oauth_config:
            try:
                from app.services.oauth_service import get_valid_access_token
                token = get_valid_access_token(spec, db, venue_id=venue_id)
            except Exception as exc:
                logger.warning("OAuth token retrieval failed, falling back to credentials: %s", exc)
                token = credentials.get(auth_config.get("token_field", "access_token"), "")
        else:
            token = credentials.get(auth_config.get("token_field", "access_token"), "")
        headers["Authorization"] = f"Bearer {token}"

    return headers, httpx_auth


# ---------------------------------------------------------------------------
# Template mode
# ---------------------------------------------------------------------------

def render_request(
    spec,
    operation: dict,
    extracted_fields: dict,
    credentials: dict,
    db: Session | None = None,
    venue_id: str | None = None,
) -> RenderedRequest:
    """Render a deterministic HTTP request from a connector spec + operation template."""
    template_ctx = {
        "creds": credentials,
        **extracted_fields,
    }

    # Render base URL + path
    base_url = _jinja_env.from_string(spec.base_url_template or "").render(**template_ctx)
    path = _jinja_env.from_string(operation.get("path_template", "")).render(**template_ctx)
    url = base_url.rstrip("/") + path

    # URL-encode '+' in query parameter values (e.g., +13:00 → %2B13:00)
    # The '+' sign means space in URL query strings, so it must be percent-encoded.
    if '?' in url:
        base_part, qs = url.split('?', 1)
        url = base_part + '?' + qs.replace('+', '%2B')

    if ":///" in url:
        raise ValueError(
            f"Rendered URL has empty hostname: {url}. "
            "Check base_url_template and credentials."
        )

    # Headers from operation definition (render values through Jinja2)
    raw_headers = operation.get("headers", {})
    headers = {}
    for k, v in raw_headers.items():
        headers[k] = _jinja_env.from_string(str(v)).render(**template_ctx).strip()

    # Apply auth (venue_id for per-venue OAuth tokens)
    headers, _ = _apply_auth(headers, spec.auth_type, spec.auth_config, credentials, spec=spec, db=db, venue_id=venue_id)

    # Render request body
    body = None
    body_template = operation.get("request_body_template")
    if body_template:
        rendered_body = _jinja_env.from_string(body_template).render(**template_ctx)
        try:
            body = json.loads(rendered_body)
        except json.JSONDecodeError:
            body = rendered_body

    return RenderedRequest(
        method=operation.get("method", "POST"),
        url=url,
        headers=headers,
        body=body,
    )


# ---------------------------------------------------------------------------
# HTTP execution (shared by both modes)
# ---------------------------------------------------------------------------

def execute_http(rendered: RenderedRequest, operation: dict, credentials: dict | None = None, auth_type: str | None = None, auth_config: dict | None = None) -> ConnectorResult:
    """Execute an HTTP request and return a ConnectorResult."""
    timeout = operation.get("timeout_seconds", 30)
    success_codes = operation.get("success_status_codes", [200, 201])
    ref_path = operation.get("response_ref_path")

    # Build httpx auth tuple for basic auth
    httpx_auth = None
    if auth_type == "basic" and credentials and auth_config:
        username_field = auth_config.get("username_field", "username")
        password = auth_config.get("password") or credentials.get(auth_config.get("password_field", "password"), "")
        httpx_auth = (credentials.get(username_field, ""), password)

    try:
        resp = httpx.request(
            method=rendered.method,
            url=rendered.url,
            headers=rendered.headers,
            json=rendered.body if isinstance(rendered.body, dict) else None,
            content=rendered.body if isinstance(rendered.body, str) else None,
            auth=httpx_auth,
            timeout=float(timeout),
        )
    except httpx.TimeoutException:
        return ConnectorResult(
            success=False,
            reference=None,
            response_payload={},
            error_message="Request timed out",
        )
    except httpx.HTTPError as exc:
        return ConnectorResult(
            success=False,
            reference=None,
            response_payload={},
            error_message=f"Network error: {exc}",
        )

    # Determine success
    if resp.status_code in success_codes:
        reference = _extract_reference(resp, ref_path)
        try:
            payload = resp.json()
        except Exception:
            payload = {"body": resp.text[:500]}
        return ConnectorResult(
            success=True,
            reference=reference,
            response_payload=payload,
        )

    return ConnectorResult(
        success=False,
        reference=None,
        response_payload={"status_code": resp.status_code, "body": resp.text[:200]},
        error_message=f"API error {resp.status_code}: {resp.text[:200]}",
    )


def _extract_reference(resp: httpx.Response, ref_path: str | None) -> str | None:
    """Extract a reference value from the response using a dotted path."""
    if not ref_path:
        return None

    parts = ref_path.split(".")
    if parts[0] == "headers":
        val = resp.headers.get(parts[1], "") if len(parts) > 1 else ""
        # For Location headers, extract the last path segment as the ID
        if parts[-1].lower() == "location" and val:
            return val.rstrip("/").rsplit("/", 1)[-1]
        return val

    # Navigate JSON body
    try:
        data = resp.json()
    except Exception:
        return None
    for part in parts:
        if isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return str(data) if data is not None else None


# ---------------------------------------------------------------------------
# Agent mode
# ---------------------------------------------------------------------------

_AGENT_EXECUTION_SYSTEM_PROMPT = """\
You are an API request builder. Given extracted fields from an approved task and \
API documentation, produce the exact HTTP request to execute.

You MUST respond with ONLY a JSON object in this format:
{
  "method": "POST",
  "path": "/employees/",
  "headers": {"Accept": "application/json"},
  "body": { ... }
}

Rules:
- Use ONLY the provided API documentation to determine the correct endpoint, method, and body structure.
- Map the extracted fields to the API's expected field names.
- Do NOT include authentication headers — they are injected server-side.
- Do NOT include the base URL — only the path.
- Respond with valid JSON only, no markdown fences or explanations.
"""


def execute_via_agent(
    spec,
    operation: dict,
    extracted_fields: dict,
    credentials: dict,
    db: Session,
    task_id: str | None = None,
) -> RenderedRequest:
    """Use an LLM to generate the HTTP request, then inject credentials."""
    from app.interpreter.llm_interpreter import call_llm

    user_prompt_parts = [
        "## Operation",
        f"Action: {operation.get('action', 'unknown')}",
        f"Method hint: {operation.get('method', 'POST')}",
        f"Path hint: {operation.get('path_template', '')}",
        f"Required fields: {json.dumps(operation.get('required_fields', []))}",
        "",
        "## Extracted Fields (approved by user)",
        json.dumps(extracted_fields, indent=2),
        "",
    ]

    if spec.api_documentation:
        user_prompt_parts.extend([
            "## API Documentation",
            spec.api_documentation,
            "",
        ])

    if spec.example_requests:
        user_prompt_parts.extend([
            "## Example Requests",
            json.dumps(spec.example_requests, indent=2),
            "",
        ])

    if operation.get("field_mapping"):
        user_prompt_parts.extend([
            "## Field Mapping Hints",
            json.dumps(operation["field_mapping"], indent=2),
            "",
        ])

    user_prompt = "\n".join(user_prompt_parts)

    parsed, _ = call_llm(
        system_prompt=_AGENT_EXECUTION_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        db=db,
        task_id=task_id,
        call_type="execution",
    )

    # Build the full URL
    template_ctx = {"creds": credentials, **extracted_fields}
    base_url = _jinja_env.from_string(spec.base_url_template or "").render(**template_ctx)
    path = parsed.get("path", "")
    url = base_url.rstrip("/") + path

    if ":///" in url:
        raise ValueError(
            f"Rendered URL has empty hostname: {url}. "
            "Check base_url_template and credentials."
        )

    # Start with headers from LLM, then inject auth
    headers = parsed.get("headers", {})
    headers, _ = _apply_auth(headers, spec.auth_type, spec.auth_config, credentials, spec=spec, db=db)

    return RenderedRequest(
        method=parsed.get("method", "POST"),
        url=url,
        headers=headers,
        body=parsed.get("body"),
    )


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------

def _normalize_fields(extracted_fields: dict, operation: dict) -> dict:
    """Normalize field values to fix common LLM formatting mistakes."""
    import re
    field_descs = operation.get("field_descriptions", {})
    normalized = dict(extracted_fields)
    for key, value in normalized.items():
        if not isinstance(value, str):
            continue
        desc = field_descs.get(key, "")
        # Fix datetime fields that need URL-encoded timezone offset (%2B)
        if "%2B" in desc:
            # "2026-03-16T07:00:00 13:00" → space before offset → %2B
            value = re.sub(r'(\d{2}:\d{2}:\d{2})\s+(\d{1,2}:\d{2})$', r'\1%2B\2', value)
            # "2026-03-16T07:00:00+13:00" → bare + → %2B
            value = re.sub(r'(\d{2}:\d{2}:\d{2})\+(\d{1,2}:\d{2})$', r'\1%2B\2', value)
            normalized[key] = value
        elif "8601" in desc or "timezone" in desc.lower():
            # Standard ISO format: space before offset → +
            value = re.sub(r'(\d{2}:\d{2}:\d{2})\s+(\d{1,2}:\d{2})$', r'\1+\2', value)
            normalized[key] = value
    return normalized


def execute_spec(
    spec,
    operation: dict,
    extracted_fields: dict,
    credentials: dict,
    db: Session,
    task_id: str | None = None,
    venue_id: str | None = None,
) -> tuple[ConnectorResult, RenderedRequest]:
    """Execute a connector spec operation. Returns (result, rendered_request)."""
    # Normalize field values to fix common LLM formatting mistakes
    extracted_fields = _normalize_fields(extracted_fields, operation)

    # Validate required fields
    required = operation.get("required_fields", [])
    missing = [f for f in required if f not in extracted_fields or not extracted_fields[f]]
    if missing:
        return ConnectorResult(
            success=False,
            reference=None,
            response_payload={},
            error_message=f"Missing required fields: {', '.join(missing)}",
        ), RenderedRequest(method="", url="", headers={}, body=None)

    if spec.execution_mode == "agent":
        rendered = execute_via_agent(spec, operation, extracted_fields, credentials, db, task_id)
    else:
        rendered = render_request(spec, operation, extracted_fields, credentials, db=db, venue_id=venue_id)

    result = execute_http(
        rendered,
        operation,
        credentials=credentials,
        auth_type=spec.auth_type,
        auth_config=spec.auth_config,
    )
    return result, rendered
