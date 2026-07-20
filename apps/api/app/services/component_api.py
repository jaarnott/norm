"""Execute a component API action — shared by the REST router and the MCP
app-support tools.

A component API config (``ComponentApiConfig`` in the config DB) describes one
HTTP call a display component makes directly — reference data for the purchase
order editor, live prices, the order-batch submit. The web app reaches these
via ``POST /api/component-api/{component}/{action}`` with a Norm session; an
MCP App iframe has no session, so it reaches the same configs through
``norm__component_api`` (see ``app.mcp.app_tools``), which calls this.

One implementation, two front doors. The render/execute logic used to live
inline in ``routers/component_apis.py``; it moved here verbatim so the MCP
path could not drift from the web path.
"""

from __future__ import annotations

import logging
import re

import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class ComponentApiError(Exception):
    """Recoverable failure: bad config, missing credentials, template error."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def execute_component_action(
    component_key: str,
    action_name: str,
    params: dict | list,
    venue_id: str | None,
    db: Session,
    config_db: Session,
) -> dict:
    """Run one component API action and return ``{"data", "status_code"[, "error"]}``.

    Raises ComponentApiError for anything the caller misconfigured, and maps
    transport failures to it too — callers on both surfaces want a message,
    not an httpx traceback.
    """
    from app.connectors.spec_executor import _apply_auth, _jinja_env
    from app.db.config_models import ComponentApiConfig, ConnectorSpec
    from app.db.models import ConnectorConfig

    cfg = (
        config_db.query(ComponentApiConfig)
        .filter(
            ComponentApiConfig.component_key == component_key,
            ComponentApiConfig.action_name == action_name,
            ComponentApiConfig.enabled.is_(True),
        )
        .first()
    )
    if not cfg:
        raise ComponentApiError(
            f"No config for {component_key}/{action_name}", status_code=404
        )

    cred_query = db.query(ConnectorConfig).filter(
        ConnectorConfig.connector_name == cfg.connector_name,
        ConnectorConfig.enabled == "true",
    )
    if venue_id:
        cred_query = cred_query.filter(ConnectorConfig.venue_id == venue_id)
    cred_row = cred_query.first()
    if not cred_row:
        raise ComponentApiError(
            f"No credentials for {cfg.connector_name}"
            + (f" (venue {venue_id})" if venue_id else "")
        )
    credentials = cred_row.config or {}

    spec = (
        config_db.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == cfg.connector_name)
        .first()
    )
    if not spec:
        raise ComponentApiError(
            f"Connector spec not found: {cfg.connector_name}", status_code=404
        )

    template_ctx = {
        "creds": credentials,
        **(params if isinstance(params, dict) else {}),
    }
    try:
        url = _jinja_env.from_string(cfg.path_template).render(**template_ctx).strip()
    except Exception as e:
        raise ComponentApiError(f"URL template error: {e}") from e

    if url.startswith("//"):
        url = "https:" + url

    # Encode '+' in query params as %2B (servers interpret bare + as space)
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    if parts.query:
        encoded_query = re.sub(
            r"(\d{2}:\d{2}:\d{2})\+(\d{1,2}:\d{2})", r"\1%2B\2", parts.query
        )
        url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, encoded_query, parts.fragment)
        )

    req_body = None
    if cfg.request_body_template and cfg.method in ("POST", "PUT", "PATCH"):
        try:
            rendered = _jinja_env.from_string(cfg.request_body_template).render(
                **template_ctx
            )
            import json

            try:
                req_body = json.loads(rendered)
            except json.JSONDecodeError:
                req_body = rendered
        except Exception as e:
            raise ComponentApiError(f"Body template error: {e}") from e

    if req_body is None and cfg.method in ("POST", "PUT", "PATCH"):
        if params:
            req_body = params

    headers = {"Content-Type": "application/json"}
    for k, v in (spec.auth_config or {}).items():
        try:
            headers[k] = _jinja_env.from_string(str(v)).render(**template_ctx).strip()
        except Exception:
            headers[k] = str(v)
    for k, v in (cfg.headers or {}).items():
        try:
            headers[k] = _jinja_env.from_string(str(v)).render(**template_ctx).strip()
        except Exception:
            headers[k] = str(v)

    headers, httpx_auth = _apply_auth(
        headers,
        spec.auth_type,
        spec.auth_config or {},
        credentials,
        spec=spec,
        db=db,
        venue_id=venue_id,
    )

    try:
        resp = httpx.request(
            method=cfg.method,
            url=url,
            headers=headers,
            json=req_body if isinstance(req_body, (dict, list)) else None,
            content=req_body if isinstance(req_body, str) else None,
            auth=httpx_auth,
            timeout=30.0,
        )
    except httpx.TimeoutException as exc:
        raise ComponentApiError("Request timed out", status_code=504) from exc
    except httpx.HTTPError as exc:
        raise ComponentApiError(f"HTTP error: {exc}", status_code=502) from exc

    try:
        data = resp.json()
    except Exception:
        data = resp.text

    if resp.status_code >= 400:
        return {"data": data, "status_code": resp.status_code, "error": True}

    if cfg.response_field_mapping and isinstance(data, (dict, list)):
        data = apply_response_mapping(data, cfg.response_field_mapping)

    return {"data": data, "status_code": resp.status_code}


def apply_response_mapping(data: dict | list, mapping: dict[str, str]) -> dict | list:
    """Remap field names in response data using the configured mapping.

    mapping is {"componentFieldName": "apiFieldName"} — the reverse lookup
    renames API fields to component field names.
    Handles both single objects and arrays of objects.
    Nested arrays (e.g., rosteredShifts) are also processed.
    """
    reverse: dict[str, str] = {}
    for comp_field, api_field in mapping.items():
        if api_field:  # skip unmapped
            reverse[api_field] = comp_field

    def _remap_item(item: dict) -> dict:
        result: dict = {}
        for key, value in item.items():
            new_key = reverse.get(key, key)
            if isinstance(value, list) and value and isinstance(value[0], dict):
                result[new_key] = [_remap_item(v) for v in value]
            else:
                result[new_key] = value
        return result

    if isinstance(data, list):
        return [_remap_item(item) if isinstance(item, dict) else item for item in data]
    if isinstance(data, dict):
        for wrapper_key in ("data", "items", "results"):
            if wrapper_key in data and isinstance(data[wrapper_key], list):
                return {
                    **data,
                    wrapper_key: [
                        _remap_item(item) if isinstance(item, dict) else item
                        for item in data[wrapper_key]
                    ],
                }
        return _remap_item(data)
    return data
