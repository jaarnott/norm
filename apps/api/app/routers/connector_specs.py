"""Admin CRUD endpoints for connector specs."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import ConnectorSpec, ConnectorConfig, User
from app.auth.dependencies import get_current_user, require_role

router = APIRouter(prefix="/connector-specs", tags=["connector-specs"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class OperationSchema(BaseModel):
    action: str
    method: str = "POST"
    path_template: str = ""
    headers: dict = {}
    required_fields: list[str] = []
    field_mapping: dict = {}
    request_body_template: str | None = None
    success_status_codes: list[int] = [200, 201]
    response_ref_path: str | None = None
    timeout_seconds: int = 30


class ConnectorSpecCreate(BaseModel):
    connector_name: str
    display_name: str
    category: str | None = None
    execution_mode: str = "template"
    auth_type: str
    auth_config: dict = {}
    base_url_template: str | None = None
    operations: list[dict] = []
    api_documentation: str | None = None
    example_requests: list[dict] = []
    credential_fields: list[dict] = []
    oauth_config: dict | None = None
    enabled: bool = True


class ConnectorSpecUpdate(BaseModel):
    display_name: str | None = None
    category: str | None = None
    execution_mode: str | None = None
    auth_type: str | None = None
    auth_config: dict | None = None
    base_url_template: str | None = None
    operations: list[dict] | None = None
    api_documentation: str | None = None
    example_requests: list[dict] | None = None
    credential_fields: list[dict] | None = None
    oauth_config: dict | None = None
    enabled: bool | None = None


class DryRunBody(BaseModel):
    extracted_fields: dict
    operation_action: str | None = None


class GenerateBody(BaseModel):
    api_docs: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _spec_to_dict(spec: ConnectorSpec) -> dict:
    return {
        "id": spec.id,
        "connector_name": spec.connector_name,
        "display_name": spec.display_name,
        "category": spec.category,
        "execution_mode": spec.execution_mode,
        "auth_type": spec.auth_type,
        "auth_config": spec.auth_config,
        "base_url_template": spec.base_url_template,
        "operations": spec.operations,
        "api_documentation": spec.api_documentation,
        "example_requests": spec.example_requests,
        "credential_fields": spec.credential_fields,
        "oauth_config": spec.oauth_config,
        "version": spec.version,
        "enabled": spec.enabled,
        "created_at": spec.created_at.isoformat() if spec.created_at else None,
        "updated_at": spec.updated_at.isoformat() if spec.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
async def list_specs(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    specs = db.query(ConnectorSpec).order_by(ConnectorSpec.connector_name).all()
    return {"specs": [_spec_to_dict(s) for s in specs]}


@router.post("", status_code=201)
async def create_spec(
    body: ConnectorSpecCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    existing = db.query(ConnectorSpec).filter(
        ConnectorSpec.connector_name == body.connector_name
    ).first()
    if existing:
        raise HTTPException(409, f"Spec already exists: {body.connector_name}")

    spec = ConnectorSpec(
        connector_name=body.connector_name,
        display_name=body.display_name,
        category=body.category,
        execution_mode=body.execution_mode,
        auth_type=body.auth_type,
        auth_config=body.auth_config,
        base_url_template=body.base_url_template,
        operations=body.operations,
        api_documentation=body.api_documentation,
        example_requests=body.example_requests,
        credential_fields=body.credential_fields,
        oauth_config=body.oauth_config,
        enabled=body.enabled,
    )
    db.add(spec)
    db.commit()
    db.refresh(spec)
    return _spec_to_dict(spec)


@router.get("/{name}")
async def get_spec(
    name: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    spec = db.query(ConnectorSpec).filter(ConnectorSpec.connector_name == name).first()
    if not spec:
        raise HTTPException(404, f"Spec not found: {name}")
    return _spec_to_dict(spec)


@router.put("/{name}")
async def update_spec(
    name: str,
    body: ConnectorSpecUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    spec = db.query(ConnectorSpec).filter(ConnectorSpec.connector_name == name).first()
    if not spec:
        raise HTTPException(404, f"Spec not found: {name}")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(spec, key, value)

    spec.version = spec.version + 1
    db.commit()
    db.refresh(spec)
    return _spec_to_dict(spec)


@router.delete("/{name}")
async def delete_spec(
    name: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    spec = db.query(ConnectorSpec).filter(ConnectorSpec.connector_name == name).first()
    if not spec:
        raise HTTPException(404, f"Spec not found: {name}")
    db.delete(spec)
    db.commit()
    return {"deleted": True}


@router.post("/{name}/dry-run")
async def dry_run(
    name: str,
    body: DryRunBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    """Render a template with sample fields — returns the HTTP request without executing it."""
    from app.connectors.spec_executor import render_request

    spec = db.query(ConnectorSpec).filter(ConnectorSpec.connector_name == name).first()
    if not spec:
        raise HTTPException(404, f"Spec not found: {name}")

    if spec.execution_mode != "template":
        raise HTTPException(400, "Dry-run is only available for template-mode specs")

    # Find operation
    operation = None
    for op in (spec.operations or []):
        if body.operation_action and op.get("action") == body.operation_action:
            operation = op
            break
    if operation is None and spec.operations:
        operation = spec.operations[0]
    if operation is None:
        raise HTTPException(400, "No operations defined on this spec")

    # Get credentials (use empty dict for dry-run if none configured)
    config_row = db.query(ConnectorConfig).filter(
        ConnectorConfig.connector_name == name,
    ).first()
    credentials = config_row.config if config_row else {}

    try:
        rendered = render_request(spec, operation, body.extracted_fields, credentials)
        return {"rendered_request": rendered.to_audit_dict()}
    except Exception as exc:
        raise HTTPException(400, f"Template rendering failed: {exc}")


@router.post("/{name}/test")
async def test_spec(
    name: str,
    body: DryRunBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    """Execute a test request against the real API."""
    from app.connectors.spec_executor import execute_spec

    spec = db.query(ConnectorSpec).filter(ConnectorSpec.connector_name == name).first()
    if not spec:
        raise HTTPException(404, f"Spec not found: {name}")

    # Find operation
    operation = None
    for op in (spec.operations or []):
        if body.operation_action and op.get("action") == body.operation_action:
            operation = op
            break
    if operation is None and spec.operations:
        operation = spec.operations[0]
    if operation is None:
        raise HTTPException(400, "No operations defined on this spec")

    config_row = db.query(ConnectorConfig).filter(
        ConnectorConfig.connector_name == name,
        ConnectorConfig.enabled == "true",
    ).first()
    if not config_row:
        raise HTTPException(400, f"No credentials configured for {name}")

    result, rendered = execute_spec(
        spec, operation, body.extracted_fields, config_row.config, db,
    )
    return {
        "success": result.success,
        "reference": result.reference,
        "response_payload": result.response_payload,
        "error": result.error_message,
        "rendered_request": rendered.to_audit_dict(),
    }


@router.post("/generate")
async def generate_spec(
    body: GenerateBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    """AI-assisted: send API docs, get back a draft connector spec."""
    from app.services.spec_generator import generate_connector_spec

    try:
        result = generate_connector_spec(body.api_docs, db)
        return result
    except Exception as exc:
        raise HTTPException(500, f"Spec generation failed: {exc}")
