"""Component API config management and execution.

Provides CRUD endpoints for managing per-component API configurations
and an execution endpoint that components call directly (bypassing the
LLM tool system entirely).
"""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_permission
from app.db.config_models import ComponentApiConfig, ConnectorSpec
from app.db.engine import get_config_db, get_config_db_rw, get_db
from app.db.models import ConnectorConfig, User

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ComponentApiConfigCreate(BaseModel):
    component_key: str
    connector_name: str
    action_name: str
    display_label: str | None = None
    method: str = "GET"
    path_template: str
    request_body_template: str | None = None
    headers: dict = {}
    required_fields: list[str] = []
    field_descriptions: dict = {}
    field_mapping: dict | None = None
    ref_fields: dict | None = None
    id_field: str | None = None
    response_field_mapping: dict | None = None
    enabled: bool = True


class ComponentApiConfigUpdate(BaseModel):
    display_label: str | None = None
    method: str | None = None
    path_template: str | None = None
    request_body_template: str | None = None
    headers: dict | None = None
    required_fields: list[str] | None = None
    field_descriptions: dict | None = None
    field_mapping: dict | None = None
    ref_fields: dict | None = None
    id_field: str | None = None
    response_field_mapping: dict | None = None
    enabled: bool | None = None


class ExecuteRequest(BaseModel):
    venue_id: str | None = None
    params: dict | list = {}


class PreviewRequest(BaseModel):
    config_id: str
    venue_id: str | None = None
    params: dict = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_dict(cfg: ComponentApiConfig) -> dict:
    return {
        "id": cfg.id,
        "component_key": cfg.component_key,
        "connector_name": cfg.connector_name,
        "action_name": cfg.action_name,
        "display_label": cfg.display_label,
        "method": cfg.method,
        "path_template": cfg.path_template,
        "request_body_template": cfg.request_body_template,
        "headers": cfg.headers,
        "required_fields": cfg.required_fields,
        "field_descriptions": cfg.field_descriptions,
        "field_mapping": cfg.field_mapping,
        "ref_fields": cfg.ref_fields,
        "id_field": cfg.id_field,
        "response_field_mapping": cfg.response_field_mapping,
        "enabled": cfg.enabled,
        "created_at": cfg.created_at.isoformat() if cfg.created_at else None,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------


@router.get("/component-api-configs")
async def list_configs(
    component_key: str | None = None,
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    query = config_db.query(ComponentApiConfig)
    if component_key:
        query = query.filter(ComponentApiConfig.component_key == component_key)
    configs = query.order_by(
        ComponentApiConfig.component_key, ComponentApiConfig.action_name
    ).all()
    return {"configs": [_to_dict(c) for c in configs]}


@router.post("/component-api-configs", status_code=201)
async def create_config(
    body: ComponentApiConfigCreate,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    cfg = ComponentApiConfig(
        component_key=body.component_key,
        connector_name=body.connector_name,
        action_name=body.action_name,
        display_label=body.display_label,
        method=body.method,
        path_template=body.path_template,
        request_body_template=body.request_body_template,
        headers=body.headers,
        required_fields=body.required_fields,
        field_descriptions=body.field_descriptions,
        field_mapping=body.field_mapping,
        ref_fields=body.ref_fields,
        id_field=body.id_field,
        response_field_mapping=body.response_field_mapping,
        enabled=body.enabled,
    )
    config_db.add(cfg)
    config_db.commit()
    config_db.refresh(cfg)
    return _to_dict(cfg)


@router.put("/component-api-configs/{config_id}")
async def update_config(
    config_id: str,
    body: ComponentApiConfigUpdate,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    cfg = (
        config_db.query(ComponentApiConfig)
        .filter(ComponentApiConfig.id == config_id)
        .first()
    )
    if not cfg:
        raise HTTPException(404, "Config not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(cfg, field, value)
    config_db.commit()
    config_db.refresh(cfg)
    return _to_dict(cfg)


@router.delete("/component-api-configs/{config_id}")
async def delete_config(
    config_id: str,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    cfg = (
        config_db.query(ComponentApiConfig)
        .filter(ComponentApiConfig.id == config_id)
        .first()
    )
    if not cfg:
        raise HTTPException(404, "Config not found")
    config_db.delete(cfg)
    config_db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Shared rendering helper
# ---------------------------------------------------------------------------


def _render_request(
    cfg: ComponentApiConfig,
    spec: ConnectorSpec,
    credentials: dict,
    params: dict,
    db: Session,
    venue_id: str | None = None,
) -> dict:
    """Render a full HTTP request from config + params without executing it.

    Returns {"method", "url", "headers", "body"}.
    """
    import json
    from urllib.parse import urlsplit, urlunsplit

    from app.connectors.spec_executor import _apply_auth, _jinja_env

    template_ctx = {"creds": credentials, **params}

    # Render URL
    url = _jinja_env.from_string(cfg.path_template).render(**template_ctx).strip()
    if url.startswith("//"):
        url = "https:" + url
    parts = urlsplit(url)
    if parts.query:
        url = urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                re.sub(
                    r"(\d{2}:\d{2}:\d{2})\+(\d{1,2}:\d{2})", r"\1%2B\2", parts.query
                ),
                parts.fragment,
            )
        )

    # Render body
    req_body = None
    if cfg.request_body_template and cfg.method in ("POST", "PUT", "PATCH"):
        rendered = _jinja_env.from_string(cfg.request_body_template).render(
            **template_ctx
        )
        try:
            req_body = json.loads(rendered)
        except json.JSONDecodeError:
            req_body = rendered
    elif req_body is None and cfg.method in ("POST", "PUT", "PATCH"):
        if params:
            req_body = params

    # Build headers
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

    # Apply auth
    headers, _ = _apply_auth(
        headers,
        spec.auth_type,
        spec.auth_config or {},
        credentials,
        spec=spec,
        db=db,
        venue_id=venue_id,
    )

    return {
        "method": cfg.method,
        "url": url,
        "headers": headers,
        "body": req_body,
    }


# ---------------------------------------------------------------------------
# Preview endpoint — renders request without executing
# ---------------------------------------------------------------------------


@router.post("/component-api-configs/preview-request")
async def preview_request(
    body: PreviewRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("admin:system")),
):
    """Render a full HTTP request from a component API config without executing it."""
    cfg = (
        config_db.query(ComponentApiConfig)
        .filter(ComponentApiConfig.id == body.config_id)
        .first()
    )
    if not cfg:
        raise HTTPException(404, "Config not found")

    spec = (
        config_db.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == cfg.connector_name)
        .first()
    )
    if not spec:
        raise HTTPException(404, f"Connector spec not found: {cfg.connector_name}")

    cred_query = db.query(ConnectorConfig).filter(
        ConnectorConfig.connector_name == cfg.connector_name,
        ConnectorConfig.enabled == "true",
    )
    if body.venue_id:
        cred_query = cred_query.filter(ConnectorConfig.venue_id == body.venue_id)
    cred_row = cred_query.first()
    if not cred_row:
        raise HTTPException(400, f"No credentials for {cfg.connector_name}")

    try:
        rendered = _render_request(
            cfg, spec, cred_row.config or {}, body.params, db, body.venue_id
        )
    except Exception as e:
        raise HTTPException(400, f"Render error: {e}") from e

    # Mask auth tokens for display
    display_headers = dict(rendered["headers"])
    if "Authorization" in display_headers:
        val = display_headers["Authorization"]
        if len(val) > 20:
            display_headers["Authorization"] = val[:20] + "..."

    return {
        "method": rendered["method"],
        "url": rendered["url"],
        "headers": display_headers,
        "body": rendered["body"],
    }


# ---------------------------------------------------------------------------
# Execution endpoint — components call this directly
# ---------------------------------------------------------------------------


@router.post("/component-api/{component_key}/{action_name}")
async def execute_component_api(
    component_key: str,
    action_name: str,
    body: ExecuteRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Execute a component API call against an external connector.

    Looks up the config, resolves credentials, renders the URL/body,
    makes the HTTP call, and returns the raw response. No working
    documents, no response transforms, no LLM involvement.

    The execution itself lives in ``services.component_api`` so the MCP
    app-support tools run the identical path.
    """
    from app.services.component_api import ComponentApiError, execute_component_action

    try:
        return execute_component_action(
            component_key, action_name, body.params, body.venue_id, db, config_db
        )
    except ComponentApiError as e:
        raise HTTPException(e.status_code, str(e)) from e
