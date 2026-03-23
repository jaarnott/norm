from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import ConnectorConfig, ConnectorSpec, User
from app.auth.dependencies import get_current_user, require_role

router = APIRouter()

# Platform-level connectors that are not spec-driven (e.g. the LLM API key).
# Domain connectors (BambooHR, Deputy, etc.) are managed as ConnectorSpecs in the DB.
AVAILABLE_MODELS = [
    {"id": "claude-sonnet-4-20250514", "label": "Claude Sonnet 4"},
    {"id": "claude-opus-4-20250514", "label": "Claude Opus 4 (Recommended)"},
    {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5 (Fast)"},
]


def _get_platform_connectors():
    """Build platform connector definitions with runtime defaults."""
    from app.config import settings
    return [
        {
            "name": "anthropic",
            "label": "Anthropic (Claude)",
            "domain": "_platform",
            "fields": [
                {"key": "api_key", "label": "API Key", "secret": True},
                {"key": "interpreter_model", "label": "Agent Model", "secret": False, "type": "select", "options": AVAILABLE_MODELS, "default": settings.LLM_INTERPRETER_MODEL},
                {"key": "router_model", "label": "Router Model", "secret": False, "type": "select", "options": AVAILABLE_MODELS, "default": settings.ROUTER_MODEL},
            ],
        },
    ]


# Kept for backwards compat in helper functions that only need names/field keys
PLATFORM_CONNECTORS = _get_platform_connectors()


def _redact_config(config: dict, connector_name: str, credential_fields: list | None = None) -> dict:
    # Check platform connectors first
    meta = next((c for c in PLATFORM_CONNECTORS if c["name"] == connector_name), None)
    fields = meta["fields"] if meta else (credential_fields or [])
    if not fields:
        return config
    secret_keys = {f["key"] for f in fields if f.get("secret")}
    return {k: ("••••••••" if k in secret_keys and v else v) for k, v in config.items()}


@router.get("/connectors")
async def list_connectors(venue_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Filter configs by venue_id (None = platform/global configs)
    config_query = db.query(ConnectorConfig)
    if venue_id:
        config_query = config_query.filter(ConnectorConfig.venue_id == venue_id)
    else:
        config_query = config_query.filter(ConnectorConfig.venue_id.is_(None))
    saved = {r.connector_name: r for r in config_query.all()}
    result = []

    # Platform connectors (Anthropic)
    for meta in PLATFORM_CONNECTORS:
        row = saved.get(meta["name"])
        result.append({
            **meta,
            "configured": row is not None,
            "enabled": row.enabled == "true" if row else False,
            "config": _redact_config(row.config, meta["name"]) if row else {},
        })

    # Spec-driven connectors from the DB
    specs = db.query(ConnectorSpec).all()
    seen = {c["name"] for c in result}
    for spec in specs:
        if spec.connector_name not in seen:
            config_row = saved.get(spec.connector_name)
            entry = {
                "name": spec.connector_name,
                "label": spec.display_name,
                "domain": spec.category,
                "fields": spec.credential_fields or [],
                "execution_mode": spec.execution_mode,
                "auth_type": spec.auth_type,
                "spec_driven": True,
                "configured": config_row is not None,
                "enabled": config_row.enabled == "true" if config_row else False,
                "config": _redact_config(config_row.config, spec.connector_name, spec.credential_fields) if config_row else {},
            }
            if spec.auth_type == "oauth2" and config_row:
                entry["oauth_connected"] = bool(config_row.access_token)
            result.append(entry)

    return {"connectors": result}


class ConnectorConfigBody(BaseModel):
    config: dict
    enabled: bool = True
    venue_id: str | None = None


@router.put("/connectors/{name}")
async def upsert_connector(name: str, body: ConnectorConfigBody, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    meta = next((c for c in PLATFORM_CONNECTORS if c["name"] == name), None)
    if not meta:
        # Check if it's a spec-driven connector
        spec = db.query(ConnectorSpec).filter(ConnectorSpec.connector_name == name).first()
        if not spec:
            raise HTTPException(404, f"Unknown connector: {name}")

    # Venue-aware lookup
    query = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name == name)
    if body.venue_id:
        query = query.filter(ConnectorConfig.venue_id == body.venue_id)
    else:
        query = query.filter(ConnectorConfig.venue_id.is_(None))
    row = query.first()

    if row:
        # Merge: keep existing values for redacted fields
        merged = dict(row.config)
        for k, v in body.config.items():
            if v != "••••••••":
                merged[k] = v
        row.config = merged
        row.enabled = "true" if body.enabled else "false"
    else:
        row = ConnectorConfig(
            connector_name=name,
            venue_id=body.venue_id,
            config=body.config,
            enabled="true" if body.enabled else "false",
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "name": row.connector_name,
        "enabled": row.enabled == "true",
        "config": _redact_config(row.config, name),
    }


@router.patch("/connectors/{name}/toggle")
async def toggle_connector(name: str, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    row = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name == name).first()
    if not row:
        raise HTTPException(404, f"No config for connector: {name}")
    row.enabled = "false" if row.enabled == "true" else "true"
    db.commit()
    db.refresh(row)
    return {
        "name": row.connector_name,
        "enabled": row.enabled == "true",
    }


@router.delete("/connectors/{name}")
async def delete_connector(name: str, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    row = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name == name).first()
    if not row:
        raise HTTPException(404, f"No config for connector: {name}")
    db.delete(row)
    db.commit()
    return {"deleted": True}


class TestBody(BaseModel):
    config: dict


@router.post("/connectors/{name}/test")
async def test_connector(name: str, body: TestBody, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    if name == "anthropic":
        import anthropic
        # Merge saved credentials with form values (skip redacted)
        config_row = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name == name).first()
        credentials = dict(config_row.config) if config_row else {}
        for k, v in body.config.items():
            if v and v != "••••••••":
                credentials[k] = v
        api_key = credentials.get("api_key", "")
        if not api_key:
            raise HTTPException(400, "api_key is required")
        try:
            client = anthropic.Anthropic(api_key=api_key)
            client.models.list(limit=1)
            return {"success": True, "message": "Connected successfully"}
        except anthropic.AuthenticationError:
            return {"success": False, "error": "Invalid API key"}
        except Exception as exc:
            return {"success": False, "error": f"Connection error: {exc}"}

    # Spec-driven connectors: use the test_request from the spec
    spec = db.query(ConnectorSpec).filter(ConnectorSpec.connector_name == name).first()
    if not spec:
        raise HTTPException(404, f"Unknown connector: {name}")

    if not spec.test_request:
        return {"success": False, "error": "No test request configured for this connector. Add one in the Connector Spec editor."}

    # Merge saved credentials with any values from the form (non-redacted only)
    config_row = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name == name).first()
    credentials = config_row.config if config_row else {}
    for k, v in body.config.items():
        if v and v != "••••••••":
            credentials[k] = v

    from app.connectors.spec_executor import render_request, execute_http

    test_op = {
        "method": spec.test_request.get("method", "GET"),
        "path_template": spec.test_request.get("path_template", ""),
        "headers": spec.test_request.get("headers", {}),
        "success_status_codes": spec.test_request.get("success_status_codes", [200]),
        "timeout_seconds": spec.test_request.get("timeout_seconds", 15),
    }

    try:
        rendered = render_request(spec, test_op, {}, credentials, db=db)
        result = execute_http(rendered, test_op, credentials=credentials, auth_type=spec.auth_type, auth_config=spec.auth_config)
        if result.success:
            return {"success": True, "message": "Connected successfully", "rendered_request": rendered.to_audit_dict(), "response": result.response_payload}
        return {"success": False, "error": result.error_message or "API returned an error", "rendered_request": rendered.to_audit_dict(), "response": result.response_payload}
    except Exception as exc:
        return {"success": False, "error": f"Connection test failed: {exc}"}


class ExecuteBody(BaseModel):
    params: dict = {}


@router.post("/connectors/{name}/execute/{action}")
async def execute_connector_action(
    name: str,
    action: str,
    body: ExecuteBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Execute a connector tool directly (no LLM, no task)."""
    # Check internal tool handlers first — these don't need a ConnectorSpec row
    from app.agents.internal_tools import get_handler
    handler = get_handler(name, action)
    if handler:
        result = handler(body.params, db, None)
        db.commit()
        return result

    spec = db.query(ConnectorSpec).filter(ConnectorSpec.connector_name == name).first()
    if not spec:
        raise HTTPException(404, f"Connector not found: {name}")

    tool_def = None
    for t in spec.tools or []:
        if t.get("action") == action:
            tool_def = t
            break
    if not tool_def:
        raise HTTPException(404, f"Tool not found: {action}")

    if tool_def.get("method", "POST").upper() != "GET":
        raise HTTPException(400, "Only read-only (GET) tools can be executed directly")

    # External tools — need credentials
    config_row = db.query(ConnectorConfig).filter(
        ConnectorConfig.connector_name == name,
        ConnectorConfig.enabled == "true",
    ).first()
    if not config_row:
        raise HTTPException(400, f"No credentials configured for {name}")

    from app.connectors.spec_executor import execute_spec
    try:
        result, rendered = execute_spec(spec, tool_def, body.params, config_row.config, db)
        return {
            "success": result.success,
            "data": result.response_payload,
            "error": result.error_message,
        }
    except Exception as exc:
        raise HTTPException(500, f"Execution failed: {exc}")


@router.get("/connectors/bamboohr/files/{file_id}")
async def download_bamboohr_file(
    file_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Proxy a file download from BambooHR (keeps API key server-side)."""
    import httpx
    from fastapi.responses import Response

    config_row = db.query(ConnectorConfig).filter(
        ConnectorConfig.connector_name == "bamboohr",
        ConnectorConfig.enabled == "true",
    ).first()
    if not config_row:
        raise HTTPException(400, "BambooHR connector not configured")

    subdomain = config_row.config.get("subdomain", "")
    api_key = config_row.config.get("api_key", "")
    url = f"https://{subdomain}.bamboohr.com/api/gateway.php/{subdomain}/v1/files/{file_id}"

    try:
        resp = httpx.get(url, auth=(api_key, "x"), timeout=30.0)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Failed to fetch file from BambooHR: {exc}")

    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"BambooHR returned {resp.status_code}")

    content_type = resp.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
    cd = resp.headers.get("content-disposition", "")
    headers = {}
    if cd:
        headers["Content-Disposition"] = cd

    return Response(content=resp.content, media_type=content_type, headers=headers)
