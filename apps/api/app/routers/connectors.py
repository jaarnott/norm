from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import ConnectorConfig, ConnectorSpec, User
from app.auth.dependencies import get_current_user, require_role

router = APIRouter()

AVAILABLE_CONNECTORS = [
    {
        "name": "bamboohr",
        "label": "BambooHR",
        "domain": "hr",
        "fields": [
            {"key": "subdomain", "label": "Subdomain", "secret": False},
            {"key": "api_key", "label": "API Key", "secret": True},
        ],
    },
    {
        "name": "anthropic",
        "label": "Anthropic (Claude)",
        "domain": "_platform",
        "fields": [
            {"key": "api_key", "label": "API Key", "secret": True},
        ],
    },
]


def _redact_config(config: dict, connector_name: str, credential_fields: list | None = None) -> dict:
    meta = next((c for c in AVAILABLE_CONNECTORS if c["name"] == connector_name), None)
    fields = meta["fields"] if meta else (credential_fields or [])
    if not fields:
        return config
    secret_keys = {f["key"] for f in fields if f.get("secret")}
    return {k: ("••••••••" if k in secret_keys and v else v) for k, v in config.items()}


@router.get("/connectors")
async def list_connectors(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    saved = {r.connector_name: r for r in db.query(ConnectorConfig).all()}
    result = []
    for meta in AVAILABLE_CONNECTORS:
        row = saved.get(meta["name"])
        result.append({
            **meta,
            "configured": row is not None,
            "enabled": row.enabled == "true" if row else False,
            "config": _redact_config(row.config, meta["name"]) if row else {},
        })

    # Include connector specs from the database (graceful if table not yet migrated)
    try:
        specs = db.query(ConnectorSpec).all()
    except Exception:
        db.rollback()
        specs = []
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


@router.put("/connectors/{name}")
async def upsert_connector(name: str, body: ConnectorConfigBody, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    meta = next((c for c in AVAILABLE_CONNECTORS if c["name"] == name), None)
    if not meta:
        # Check if it's a spec-driven connector
        spec = db.query(ConnectorSpec).filter(ConnectorSpec.connector_name == name).first()
        if not spec:
            raise HTTPException(404, f"Unknown connector: {name}")

    row = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name == name).first()
    if row:
        row.config = body.config
        row.enabled = "true" if body.enabled else "false"
    else:
        row = ConnectorConfig(
            connector_name=name,
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
    if name == "bamboohr":
        import httpx
        subdomain = body.config.get("subdomain", "")
        api_key = body.config.get("api_key", "")
        if not subdomain or not api_key:
            raise HTTPException(400, "subdomain and api_key are required")
        url = f"https://{subdomain}.bamboohr.com/api/gateway.php/{subdomain}/v1/employees/directory"
        try:
            resp = httpx.get(
                url,
                auth=(api_key, "x"),
                headers={"Accept": "application/json"},
                timeout=15.0,
            )
        except httpx.TimeoutException:
            return {"success": False, "error": "Connection timed out"}
        except httpx.HTTPError as exc:
            return {"success": False, "error": f"Network error: {exc}"}

        if resp.status_code == 200:
            return {"success": True, "message": "Connected successfully"}
        return {"success": False, "error": f"API returned status {resp.status_code}"}

    if name == "anthropic":
        import anthropic
        api_key = body.config.get("api_key", "")
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
