"""Admin CRUD endpoints for connector specs."""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db, get_config_db, get_config_db_rw, SessionLocal
from app.db.models import ConnectorSpec, ConnectorConfig, User
from app.auth.dependencies import get_current_user, require_permission

router = APIRouter(prefix="/connector-specs", tags=["connector-specs"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ToolSchema(BaseModel):
    action: str
    method: str = "POST"
    path_template: str = ""
    headers: dict = {}
    required_fields: list[str] = []
    field_mapping: dict = {}
    field_descriptions: dict = {}
    field_schema: dict | None = None
    request_body_template: str | None = None
    success_status_codes: list[int] = [200, 201]
    response_ref_path: str | None = None
    timeout_seconds: int = 30
    display_component: str | None = None
    display_props: dict | None = None
    summary_fields: list[str] | None = None
    response_transform: dict | None = None
    consolidator_config: dict | None = None


class TransformPreviewBody(BaseModel):
    payload: dict | list
    response_transform: dict


class ConnectorSpecCreate(BaseModel):
    connector_name: str
    display_name: str
    category: str | None = None
    execution_mode: str = "template"
    auth_type: str
    auth_config: dict = {}
    base_url_template: str | None = None
    tools: list[dict] = []
    api_documentation: str | None = None
    example_requests: list[dict] = []
    credential_fields: list[dict] = []
    oauth_config: dict | None = None
    test_request: dict | None = None
    enabled: bool = True


class ConnectorSpecUpdate(BaseModel):
    display_name: str | None = None
    category: str | None = None
    execution_mode: str | None = None
    auth_type: str | None = None
    auth_config: dict | None = None
    base_url_template: str | None = None
    tools: list[dict] | None = None
    api_documentation: str | None = None
    example_requests: list[dict] | None = None
    credential_fields: list[dict] | None = None
    oauth_config: dict | None = None
    test_request: dict | None = None
    enabled: bool | None = None


class DryRunBody(BaseModel):
    extracted_fields: dict
    tool_action: str | None = None
    venue_id: str | None = None


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
        "tools": spec.tools,
        "api_documentation": spec.api_documentation,
        "example_requests": spec.example_requests,
        "credential_fields": spec.credential_fields,
        "oauth_config": spec.oauth_config,
        "test_request": spec.test_request,
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
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    specs = config_db.query(ConnectorSpec).order_by(ConnectorSpec.connector_name).all()
    return {"specs": [_spec_to_dict(s) for s in specs]}


@router.post("", status_code=201)
async def create_spec(
    body: ConnectorSpecCreate,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    existing = (
        config_db.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == body.connector_name)
        .first()
    )
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
        tools=body.tools,
        api_documentation=body.api_documentation,
        example_requests=body.example_requests,
        credential_fields=body.credential_fields,
        oauth_config=body.oauth_config,
        test_request=body.test_request,
        enabled=body.enabled,
    )
    config_db.add(spec)
    config_db.commit()
    config_db.refresh(spec)
    return _spec_to_dict(spec)


@router.get("/{name}")
async def get_spec(
    name: str,
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    spec = (
        config_db.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == name)
        .first()
    )
    if not spec:
        raise HTTPException(404, f"Spec not found: {name}")
    return _spec_to_dict(spec)


@router.put("/{name}")
async def update_spec(
    name: str,
    body: ConnectorSpecUpdate,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    spec = (
        config_db.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == name)
        .first()
    )
    if not spec:
        raise HTTPException(404, f"Spec not found: {name}")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(spec, key, value)

    spec.version = spec.version + 1
    config_db.commit()
    config_db.refresh(spec)
    return _spec_to_dict(spec)


@router.delete("/{name}")
async def delete_spec(
    name: str,
    config_db: Session = Depends(get_config_db_rw),
    user: User = Depends(require_permission("admin:system")),
):
    spec = (
        config_db.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == name)
        .first()
    )
    if not spec:
        raise HTTPException(404, f"Spec not found: {name}")
    config_db.delete(spec)
    config_db.commit()
    return {"deleted": True}


@router.post("/{name}/dry-run")
async def dry_run(
    name: str,
    body: DryRunBody,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("admin:system")),
):
    """Render a template with sample fields — returns the HTTP request without executing it."""
    from app.connectors.spec_executor import render_request

    spec = (
        config_db.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == name)
        .first()
    )
    if not spec:
        raise HTTPException(404, f"Spec not found: {name}")

    if spec.execution_mode != "template":
        raise HTTPException(400, "Dry-run is only available for template-mode specs")

    # Find tool
    operation = None
    for op in spec.tools or []:
        if body.tool_action and op.get("action") == body.tool_action:
            operation = op
            break
    if operation is None and body.tool_action:
        available = [op.get("action") for op in (spec.tools or [])]
        raise HTTPException(
            400,
            f"Tool '{body.tool_action}' not found. Save the spec first. Available: {available}",
        )
    if operation is None and spec.tools:
        operation = spec.tools[0]
    if operation is None:
        raise HTTPException(400, "No tools defined on this spec")

    # Get credentials (use empty dict for dry-run if none configured)
    config_row = (
        db.query(ConnectorConfig)
        .filter(
            ConnectorConfig.connector_name == name,
        )
        .first()
    )
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
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("admin:system")),
):
    """Execute a test request against the real API."""
    from app.connectors.spec_executor import execute_spec

    spec = (
        config_db.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == name)
        .first()
    )
    if not spec:
        raise HTTPException(404, f"Spec not found: {name}")

    # Find tool
    operation = None
    for op in spec.tools or []:
        if body.tool_action and op.get("action") == body.tool_action:
            operation = op
            break
    if operation is None and body.tool_action:
        available = [op.get("action") for op in (spec.tools or [])]
        raise HTTPException(
            400,
            f"Tool '{body.tool_action}' not found. Save the spec first. Available: {available}",
        )
    if operation is None and spec.tools:
        operation = spec.tools[0]
    if operation is None:
        raise HTTPException(400, "No tools defined on this spec")

    config_query = db.query(ConnectorConfig).filter(
        ConnectorConfig.connector_name == name,
        ConnectorConfig.enabled == "true",
    )
    if body.venue_id:
        config_query = config_query.filter(ConnectorConfig.venue_id == body.venue_id)
    config_row = config_query.first()
    if not config_row:
        raise HTTPException(400, f"No credentials configured for {name}")

    try:
        result, rendered = execute_spec(
            spec,
            operation,
            body.extracted_fields,
            config_row.config,
            db,
        )
        return {
            "success": result.success,
            "reference": result.reference,
            "response_payload": result.response_payload,
            "error": result.error_message,
            "rendered_request": rendered.to_audit_dict(),
        }
    except Exception as exc:
        return {
            "success": False,
            "reference": None,
            "response_payload": {},
            "error": str(exc),
            "rendered_request": None,
        }


@router.post("/{name}/preview-transform")
async def preview_transform(
    name: str,
    body: TransformPreviewBody,
    user: User = Depends(require_permission("admin:system")),
):
    """Preview a response transform on a sample payload."""
    from app.connectors.response_transform import apply_response_transform

    try:
        transformed = apply_response_transform(body.payload, body.response_transform)
        return {"transformed": transformed}
    except Exception as exc:
        raise HTTPException(400, f"Transform failed: {exc}")


@router.post("/generate")
async def generate_spec(
    body: GenerateBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("admin:system")),
):
    """AI-assisted: send API docs, get back a draft connector spec."""
    from app.services.spec_generator import generate_connector_spec

    try:
        # db is used here for secrets lookup, not config queries
        result = generate_connector_spec(body.api_docs, db)
        return result
    except Exception as exc:
        raise HTTPException(500, f"Spec generation failed: {exc}")


# ---------------------------------------------------------------------------
# Consolidator endpoints
# ---------------------------------------------------------------------------


class GenerateConsolidatorBody(BaseModel):
    description: str


class EditConsolidatorBody(BaseModel):
    instruction: str
    current_tool: dict


class TestConsolidatorBody(BaseModel):
    consolidator_config: dict
    params: dict = {}


class AutoBuildConsolidatorBody(BaseModel):
    description: str | None = None
    current_tool: dict | None = None
    test_params: dict = {}
    max_iterations: int = 3


def _build_tools_context(config_db: Session) -> str:
    """Build a text listing of all available connector tools for AI prompts."""
    specs = config_db.query(ConnectorSpec).filter(ConnectorSpec.enabled == True).all()  # noqa: E712
    tools_context = []
    for spec in specs:
        if spec.execution_mode == "internal":
            continue
        for t in spec.tools or []:
            tools_context.append(
                f"- {spec.connector_name}.{t.get('action')} [{t.get('method', 'GET')}]: {t.get('description', '')}"
            )
            fields = t.get("required_fields", [])
            descs = t.get("field_descriptions", {})
            if fields:
                for f in fields:
                    tools_context.append(f"    field: {f} — {descs.get(f, '')}")
    return "\n".join(tools_context)


_TEMPLATE_VARS_TEXT = """- {{today_iso}} — today's date in ISO format with timezone (e.g., 2026-03-21T00:00:00%2B13:00)
- {{four_weeks_ago_iso}} — 4 weeks ago in same format
- {{one_week_ago_iso}} — 1 week ago in same format
- {{today}} — today's date as YYYY-MM-DD
- {{<any_input_param>}} — any field from required_fields
- {{step_id.field}} — reference a field from a completed step's result (auto-skips into .data wrapper). Parallel steps cannot reference each other — only steps that ran before the parallel group.
- {{step_id.nested.field}} — dotted path navigation; use [N] for array indexing e.g. {{step.items[0].id}}"""

_CONFIG_SCHEMA_TEXT = """- action: a snake_case name for this consolidator tool
- description: what this tool does (shown to the LLM)
- required_fields: array of field names the LLM must provide
- field_descriptions: object mapping field names to descriptions
- consolidator_config: object with:
  - function_code: a Python function string that defines run(params, call_api, log)

The function receives:
- params: dict with all required_fields values + auto-injected template vars:
  - today, today_iso, one_week_ago, one_week_ago_iso, four_weeks_ago, four_weeks_ago_iso
  - All values from required_fields (passed by the LLM)
- call_api(connector, action, params): calls any connector tool, returns the result data
  - Response transforms (field mapping, filters, timezone normalization) are applied automatically
  - The venue param is resolved automatically from params
  - Max 20 API calls per execution
- log(message): debug output captured and shown in the test UI

Available in function scope: math, json, datetime modules. Standard Python (loops, list comprehensions, try/except, dict/list operations).

Example consolidator_config:
{
  "function_code": "def run(params, call_api, log):\\n    venue = params['venue']\\n    date = params.get('date', params['today'])\\n    \\n    log(f'Fetching roster for {venue} on {date}')\\n    roster = call_api('loadedhub', 'get_roster', {\\n        'venue': venue,\\n        'start_datetime': f'{date}T00:00:00%2B13:00',\\n        'end_datetime': f'{date}T23:59:59%2B13:00',\\n    })\\n    \\n    shifts = []\\n    for r in (roster if isinstance(roster, list) else [roster]):\\n        for s in r.get('rosteredShifts', []):\\n            if date in str(s.get('clockinTime', '')):\\n                shifts.append(s)\\n    \\n    log(f'Found {len(shifts)} shifts')\\n    return shifts"
}

IMPORTANT:
- Call connector tools FIRST to explore the APIs and see actual field names and data structures
- Then write the function using those exact field names
- Use log() liberally for debugging
- The function must define run(params, call_api, log) and return the result
- Use test_consolidator to verify the function works before saving"""


def _parse_ai_json(raw: str) -> dict:
    """Strip markdown fences and parse JSON from AI response."""
    import json as json_mod

    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        raw = "\n".join(lines).strip()
    try:
        return json_mod.loads(raw)
    except json_mod.JSONDecodeError:
        raise HTTPException(500, f"AI returned invalid JSON: {raw[:500]}")


@router.post("/norm/generate-consolidator")
async def generate_consolidator(
    body: GenerateConsolidatorBody,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("admin:system")),
):
    """Use AI to generate a consolidator config from a natural language description."""
    from app.services.secrets import get_api_key
    import anthropic

    api_key = get_api_key("anthropic", "api_key", db)
    if not api_key:
        raise HTTPException(400, "Anthropic API key required")

    tools_text = _build_tools_context(config_db)

    prompt = f"""You are a tool configuration assistant. Given a user's description of what they want, generate a consolidator config JSON.

Available connector tools:
{tools_text}

Template variables available:
{_TEMPLATE_VARS_TEXT}

Generate a JSON object with these fields:
{_CONFIG_SCHEMA_TEXT}

User description: {body.description}

Return ONLY valid JSON, no markdown fences."""

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    return _parse_ai_json(response.content[0].text)


@router.post("/norm/edit-consolidator")
async def edit_consolidator(
    body: EditConsolidatorBody,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("admin:system")),
):
    """Use AI to edit an existing consolidator config based on a natural language instruction."""
    from app.services.secrets import get_api_key
    import anthropic
    import json as json_mod

    api_key = get_api_key("anthropic", "api_key", db)
    if not api_key:
        raise HTTPException(400, "Anthropic API key required")

    tools_text = _build_tools_context(config_db)
    current_json = json_mod.dumps(body.current_tool, indent=2)

    prompt = f"""You are a tool configuration assistant. You are editing an existing consolidator tool config.

Available connector tools:
{tools_text}

Template variables available:
{_TEMPLATE_VARS_TEXT}

Current tool definition:
{current_json}

User's edit instruction: {body.instruction}

Return the COMPLETE updated tool definition as a JSON object with these fields:
{_CONFIG_SCHEMA_TEXT}

Important: Return the full updated config, not just the changed parts.
Return ONLY valid JSON, no markdown fences."""

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    return _parse_ai_json(response.content[0].text)


@router.post("/norm/test-consolidator")
async def test_consolidator(
    body: TestConsolidatorBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("admin:system")),
):
    """Test a consolidator config with real data without saving it."""
    from app.agents.internal_tools import execute_consolidator

    try:
        result = execute_consolidator(body.consolidator_config, body.params, db, None)
        # Cap large step results to prevent browser parse failures
        if isinstance(result.get("data"), dict):
            for step_id, step_data in result["data"].items():
                step_json = json.dumps(step_data, default=str)
                if len(step_json) > 50_000:
                    if isinstance(step_data, list):
                        result["data"][step_id] = {
                            "_truncated": True,
                            "_total_items": len(step_data),
                            "_preview": step_data[:3],
                        }
                    elif isinstance(step_data, dict) and isinstance(
                        step_data.get("data"), list
                    ):
                        arr = step_data["data"]
                        result["data"][step_id] = {
                            **step_data,
                            "data": {
                                "_truncated": True,
                                "_total_items": len(arr),
                                "_preview": arr[:3],
                            },
                        }
        return result
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.post("/norm/auto-build-consolidator")
async def auto_build_consolidator(
    body: AutoBuildConsolidatorBody,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("admin:system")),
):
    """Autonomous loop: generate/fix a consolidator config, test it, iterate until it works."""
    from app.services.secrets import get_api_key
    from app.agents.internal_tools import execute_consolidator
    import anthropic
    import json as json_mod
    import logging

    log = logging.getLogger("consolidator.auto_build")

    if not body.description and not body.current_tool:
        raise HTTPException(
            400,
            "Provide either 'description' (new build) or 'current_tool' (fix existing)",
        )

    api_key = get_api_key("anthropic", "api_key", db)
    if not api_key:
        raise HTTPException(400, "Anthropic API key required")

    client = anthropic.Anthropic(api_key=api_key)
    tools_text = _build_tools_context(config_db)
    iteration_log: list[dict] = []
    max_iter = min(body.max_iterations, 5)

    # Step 1: Generate initial config (or use current_tool)
    if body.current_tool:
        tool_config = body.current_tool
        log.info("Auto-build: starting from existing tool config")
    else:
        log.info("Auto-build: generating from description: %s", body.description)
        gen_prompt = f"""You are a tool configuration assistant. Given a user's description of what they want, generate a consolidator config JSON.

Available connector tools:
{tools_text}

Template variables available:
{_TEMPLATE_VARS_TEXT}

Generate a JSON object with these fields:
{_CONFIG_SCHEMA_TEXT}

User description: {body.description}

Return ONLY valid JSON, no markdown fences."""

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            messages=[{"role": "user", "content": gen_prompt}],
        )
        tool_config = _parse_ai_json(response.content[0].text)
        log.info(
            "Auto-build: initial config generated — action=%s, steps=%d",
            tool_config.get("action"),
            len((tool_config.get("consolidator_config") or {}).get("steps", [])),
        )

    # Step 2: Test → Fix loop
    test_result = None
    for attempt in range(1, max_iter + 1):
        consolidator_config = tool_config.get("consolidator_config", tool_config)
        log.info(
            "Auto-build attempt %d/%d — testing config with %d steps",
            attempt,
            max_iter,
            len(consolidator_config.get("steps", [])),
        )

        try:
            test_result = execute_consolidator(
                consolidator_config, body.test_params, db, None
            )
        except Exception as exc:
            log.error(
                "Auto-build attempt %d — execute_consolidator raised: %s", attempt, exc
            )
            test_result = {"success": False, "error": str(exc), "_steps": []}

        # Check success
        all_steps_ok = test_result.get("success", False)
        step_errors = [
            s for s in test_result.get("_steps", []) if s.get("status") == "error"
        ]
        is_success = all_steps_ok and not step_errors

        error_summary = None
        if not is_success:
            error_summary = (
                "; ".join(
                    f"Step '{s.get('id', '?')}': {s.get('error', 'unknown')}"
                    for s in step_errors
                )
                if step_errors
                else test_result.get("error") or "Unknown error"
            )

        log.info(
            "Auto-build attempt %d — %s%s",
            attempt,
            "SUCCESS" if is_success else "FAILED",
            f": {error_summary}" if error_summary else "",
        )

        iteration_log.append(
            {
                "attempt": attempt,
                "success": is_success,
                "error_summary": error_summary,
                "config": tool_config,
                "test_result": test_result,
            }
        )

        if is_success:
            break

        if attempt >= max_iter:
            log.warning(
                "Auto-build: max iterations reached (%d), returning last config",
                max_iter,
            )
            break

        # Fix: send the config + test result to AI for repair
        fix_prompt = f"""You are a tool configuration assistant. You generated a consolidator config that was tested and FAILED. Fix it.

Available connector tools:
{tools_text}

Template variables available:
{_TEMPLATE_VARS_TEXT}

Current tool definition:
{json_mod.dumps(tool_config, indent=2)}

Test result (FAILED):
{json_mod.dumps(test_result, indent=2, default=str)[:3000]}

Step-by-step execution log:
{json_mod.dumps(test_result.get("_steps", []), indent=2)}

Analyze why the test failed. Common issues:
- Template variables like {{{{step_id.field}}}} not resolving correctly
- Missing filter steps to extract specific items from API results
- Wrong connector or action names
- Missing required params for API calls

Return the COMPLETE fixed tool definition as a JSON object with these fields:
{_CONFIG_SCHEMA_TEXT}

Return ONLY valid JSON, no markdown fences."""

        try:
            fix_response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2048,
                messages=[{"role": "user", "content": fix_prompt}],
            )
            tool_config = _parse_ai_json(fix_response.content[0].text)
            log.info(
                "Auto-build attempt %d — AI returned fix with %d steps",
                attempt,
                len((tool_config.get("consolidator_config") or {}).get("steps", [])),
            )
        except Exception as exc:
            log.error("Auto-build attempt %d — AI fix call failed: %s", attempt, exc)
            iteration_log.append(
                {
                    "attempt": attempt,
                    "success": False,
                    "error_summary": f"AI fix failed: {exc}",
                    "config": tool_config,
                    "test_result": test_result,
                }
            )
            break

    return {
        "config": tool_config,
        "test_result": test_result,
        "iterations": len(iteration_log),
        "iteration_log": iteration_log,
    }


# ---------------------------------------------------------------------------
# Consolidator Chat — interactive AI tool builder with real connector access
# ---------------------------------------------------------------------------


class ConsolidatorChatBody(BaseModel):
    messages: list[dict]
    current_tool: dict | None = None


_SAVE_TOOL = {
    "name": "save_consolidator",
    "description": (
        "Save the final consolidator tool definition. Call this when you have built and tested a working config. "
        "This sends the config to the frontend for the user to review."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "Snake-case action name"},
            "description": {"type": "string", "description": "What this tool does"},
            "required_fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Field names the LLM must provide when calling this tool",
            },
            "field_descriptions": {
                "type": "object",
                "description": "Map of field name to description",
            },
            "consolidator_config": {
                "type": "object",
                "description": 'The consolidator config with function_code: {"function_code": "def run(params, call_api, log):\\n    ..."}',
            },
        },
        "required": [
            "action",
            "description",
            "required_fields",
            "field_descriptions",
            "consolidator_config",
        ],
    },
}

_TEST_CONSOLIDATOR_TOOL = {
    "name": "test_consolidator",
    "description": (
        "Test a consolidator function end-to-end with real data. "
        "Executes the function_code with the given params and returns the result + debug logs. "
        "Use this to verify the function works before saving."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "consolidator_config": {
                "type": "object",
                "description": 'The consolidator config to test: {"function_code": "def run(params, call_api, log):\\n    ..."}',
            },
            "params": {
                "type": "object",
                "description": "Input parameters to pass (the values the LLM would provide for required_fields)",
            },
        },
        "required": ["consolidator_config", "params"],
    },
}


def _build_connector_tools(db, config_db=None) -> list[dict]:
    """Build Anthropic tool schemas for all enabled connector specs, with venue injection."""
    from app.db.models import Venue

    _cdb = config_db
    if _cdb is None:
        raise RuntimeError(
            "config_db is required — check that config_db is passed through the call chain"
        )
    specs = _cdb.query(ConnectorSpec).filter(ConnectorSpec.enabled == True).all()  # noqa: E712

    # Build venue lookup: connector_name -> list of venue names with configs
    venue_map: dict[str, list[str]] = {}
    configs = db.query(ConnectorConfig).filter(ConnectorConfig.enabled == "true").all()
    venue_ids = {c.venue_id for c in configs if c.venue_id}
    venues_by_id: dict[str, str] = {}
    if venue_ids:
        for v in db.query(Venue).filter(Venue.id.in_(venue_ids)).all():
            venues_by_id[v.id] = v.name
    for c in configs:
        if c.venue_id and c.venue_id in venues_by_id:
            venue_map.setdefault(c.connector_name, []).append(venues_by_id[c.venue_id])

    anthropic_tools = []
    for spec in specs:
        if spec.execution_mode == "internal":
            continue
        configured_venues = venue_map.get(spec.connector_name, [])
        for t in spec.tools or []:
            action = t.get("action", "")
            if not action:
                continue
            tool_name = f"{spec.connector_name}__{action}"
            properties = {}
            required = list(t.get("required_fields") or [])
            all_fields = required + list(t.get("optional_fields") or [])
            field_descs = t.get("field_descriptions") or {}
            field_mapping = t.get("field_mapping") or {}
            field_schema_map = t.get("field_schema") or {}
            for field in all_fields:
                if field in field_schema_map:
                    prop = {**field_schema_map[field]}
                    if "description" not in prop:
                        prop["description"] = field_descs.get(field, field)
                    properties[field] = prop
                else:
                    api_name = field_mapping.get(field, field)
                    desc_parts = []
                    hint = field_descs.get(field, "")
                    if hint:
                        desc_parts.append(hint)
                    if api_name != field:
                        desc_parts.append(f"Maps to API field: {api_name}")
                    properties[field] = {
                        "type": "string",
                        "description": ". ".join(desc_parts) if desc_parts else field,
                    }
            # Inject venue param if this connector has venue-specific configs
            if configured_venues:
                properties["venue"] = {
                    "type": "string",
                    "description": f"Venue name. Available: {', '.join(configured_venues)}.",
                    "enum": configured_venues,
                }
            method = t.get("method", "GET")
            desc = t.get("description", action)
            anthropic_tools.append(
                {
                    "name": tool_name,
                    "description": f"[{method}] {desc}",
                    "input_schema": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                }
            )
    return anthropic_tools


@router.post("/norm/consolidator-chat")
async def consolidator_chat(
    body: ConsolidatorChatBody,
    user: User = Depends(require_permission("admin:system")),
):
    """SSE chat endpoint for building/editing consolidator tools interactively."""
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def on_event(event: dict):
        loop.call_soon_threadsafe(queue.put_nowait, event)

    async def generate():
        yield ": " + " " * 2048 + "\n\n"

        def run():
            import logging
            from app.services.secrets import get_api_key
            from app.connectors.spec_executor import execute_spec
            from app.agents.tool_loop import _resolve_venue_config
            import anthropic

            log = logging.getLogger("consolidator.chat")
            from app.db.engine import _ConfigSessionLocal

            _config_factory = _ConfigSessionLocal or SessionLocal
            db = SessionLocal()
            config_db = _config_factory()
            try:
                api_key = get_api_key("anthropic", "api_key", db)
                if not api_key:
                    on_event({"type": "error", "message": "Anthropic API key required"})
                    return

                # Build real connector tools for the LLM
                connector_tools = _build_connector_tools(db, config_db)
                tools = [_SAVE_TOOL, _TEST_CONSOLIDATOR_TOOL] + connector_tools
                log.info(
                    "Consolidator chat: %d connector tools available",
                    len(connector_tools),
                )

                current_tool_text = ""
                if body.current_tool:
                    current_tool_text = f"\n\nCurrent tool definition being edited:\n```json\n{json.dumps(body.current_tool, indent=2)}\n```"

                system_prompt = f"""You are a consolidator tool builder. You help users create Python functions that chain multiple connector API calls together.

You have DIRECT ACCESS to all the connector tools listed below. You can call them to explore the APIs, see response shapes, and understand what data is available.

Template variables available in params:
{_TEMPLATE_VARS_TEXT}

Consolidator config schema:
{_CONFIG_SCHEMA_TEXT}
{current_tool_text}

Workflow:
1. When asked to build a consolidator, FIRST call the relevant connector tools to understand the data structures and field names.
2. Write a Python function `run(params, call_api, log)` that:
   - Uses `call_api(connector, action, params)` to fetch data from connector tools
   - Transforms, filters, and calculates using standard Python
   - Uses `log()` for debug output
   - Returns the final result (list, dict, or scalar)
3. Before saving, use `test_consolidator` to run the function end-to-end with real params. Check the _logs for debug output and verify the data is correct.
4. Only call save_consolidator AFTER test_consolidator confirms it works.
5. The consolidator_config must be: {{"function_code": "def run(params, call_api, log):\\n    ..."}}

Keep responses concise. Show the key data from API responses (field names, IDs, structure) so the user can verify."""

                client = anthropic.Anthropic(api_key=api_key)
                messages = list(body.messages)

                for iteration in range(25):  # max tool-use iterations
                    log.info(
                        "Consolidator chat: LLM call %d with %d messages",
                        iteration + 1,
                        len(messages),
                    )

                    response = client.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=4096,
                        system=system_prompt,
                        messages=messages,
                        tools=tools,
                    )

                    # Collect all blocks
                    assistant_content = []
                    tool_calls = []

                    for block in response.content:
                        if block.type == "text":
                            on_event({"type": "text", "text": block.text})
                            assistant_content.append(
                                {"type": "text", "text": block.text}
                            )
                        elif block.type == "tool_use":
                            assistant_content.append(
                                {
                                    "type": "tool_use",
                                    "id": block.id,
                                    "name": block.name,
                                    "input": block.input,
                                }
                            )
                            tool_calls.append(block)

                    if not tool_calls:
                        on_event({"type": "complete"})
                        break

                    # Execute all tool calls
                    tool_results = []
                    for block in tool_calls:
                        if block.name == "save_consolidator":
                            on_event({"type": "save", "config": block.input})
                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": "Config saved successfully.",
                                }
                            )
                            continue

                        if block.name == "test_consolidator":
                            on_event(
                                {
                                    "type": "tool_use",
                                    "name": "test_consolidator",
                                    "input": {
                                        "params": block.input.get("params", {}),
                                        "steps": len(
                                            (
                                                block.input.get(
                                                    "consolidator_config", {}
                                                )
                                            ).get("steps", [])
                                        ),
                                    },
                                }
                            )
                            import time as _time

                            t0 = _time.time()
                            try:
                                from app.agents.internal_tools import (
                                    execute_consolidator,
                                )

                                test_result = execute_consolidator(
                                    block.input.get("consolidator_config", {}),
                                    block.input.get("params", {}),
                                    db,
                                    None,
                                )
                                duration_ms = int((_time.time() - t0) * 1000)
                                log.info(
                                    "Consolidator chat: test_consolidator completed in %dms",
                                    duration_ms,
                                )
                            except Exception as exc:
                                duration_ms = int((_time.time() - t0) * 1000)
                                log.error(
                                    "Consolidator chat: test_consolidator failed after %dms: %s",
                                    duration_ms,
                                    exc,
                                )
                                test_result = {
                                    "success": False,
                                    "error": str(exc),
                                }

                            # Send summary to frontend
                            on_event(
                                {
                                    "type": "tool_result",
                                    "name": "test_consolidator",
                                    "result": {
                                        "success": test_result.get("success"),
                                        "_steps": test_result.get("_steps", []),
                                        "duration_ms": duration_ms,
                                    },
                                }
                            )

                            # Send full result to LLM (truncated if large)
                            result_text = json.dumps(test_result, default=str)
                            if len(result_text) > 12000:
                                result_text = result_text[:12000] + '..."}'
                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result_text,
                                }
                            )
                            continue

                        # Real connector tool — parse name and execute
                        on_event(
                            {
                                "type": "tool_use",
                                "name": block.name,
                                "input": block.input,
                            }
                        )
                        parts = block.name.split("__", 1)
                        if len(parts) != 2:
                            result_text = json.dumps(
                                {"error": f"Unknown tool: {block.name}"}
                            )
                            on_event(
                                {
                                    "type": "tool_result",
                                    "name": block.name,
                                    "result": {"error": f"Unknown tool: {block.name}"},
                                }
                            )
                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result_text,
                                }
                            )
                            continue

                        connector_name, action = parts
                        spec = (
                            config_db.query(ConnectorSpec)
                            .filter(ConnectorSpec.connector_name == connector_name)
                            .first()
                        )
                        if not spec:
                            result_text = json.dumps(
                                {"error": f"Connector not found: {connector_name}"}
                            )
                            on_event(
                                {
                                    "type": "tool_result",
                                    "name": block.name,
                                    "result": {
                                        "error": f"Connector not found: {connector_name}"
                                    },
                                }
                            )
                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result_text,
                                }
                            )
                            continue

                        tool_def = None
                        for t in spec.tools or []:
                            if t.get("action") == action:
                                tool_def = t
                                break
                        if not tool_def:
                            result_text = json.dumps(
                                {"error": f"Tool not found: {action}"}
                            )
                            on_event(
                                {
                                    "type": "tool_result",
                                    "name": block.name,
                                    "result": {"error": f"Tool not found: {action}"},
                                }
                            )
                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result_text,
                                }
                            )
                            continue

                        # Resolve credentials
                        config_row = _resolve_venue_config(
                            connector_name, block.input or {}, db
                        )
                        # Fallback: if no venue specified, find any enabled config
                        if not config_row:
                            config_row = (
                                db.query(ConnectorConfig)
                                .filter(
                                    ConnectorConfig.connector_name == connector_name,
                                    ConnectorConfig.enabled == "true",
                                )
                                .first()
                            )
                        credentials = config_row.config if config_row else {}
                        venue_id = config_row.venue_id if config_row else None

                        # Strip venue params
                        params = dict(block.input or {})
                        params.pop("venue", None)
                        params.pop("venue_name", None)
                        params.pop("venue_id", None)

                        import time as _time

                        t0 = _time.time()
                        log.info(
                            "Consolidator chat: calling %s.%s with %s",
                            connector_name,
                            action,
                            params,
                        )
                        try:
                            result, rendered = execute_spec(
                                spec,
                                tool_def,
                                params,
                                credentials,
                                db,
                                None,
                                venue_id=venue_id,
                            )
                            duration_ms = int((_time.time() - t0) * 1000)
                            result_data = {
                                "success": result.success,
                                "data": result.response_payload,
                                "error": result.error_message,
                            }
                            log.info(
                                "Consolidator chat: %s.%s returned in %dms (success=%s)",
                                connector_name,
                                action,
                                duration_ms,
                                result.success,
                            )
                        except Exception as exc:
                            duration_ms = int((_time.time() - t0) * 1000)
                            log.error(
                                "Consolidator chat tool %s error after %dms: %s",
                                block.name,
                                duration_ms,
                                exc,
                            )
                            result_data = {"success": False, "error": str(exc)}

                        # Send a slim version to the frontend (don't send 1MB+ payloads over SSE)
                        raw_json = json.dumps(result_data.get("data"), default=str)
                        raw_size = len(raw_json)
                        if raw_size > 5000:
                            # Show summary to frontend: item count + first few items
                            from app.agents.tool_loop import _unwrap_array

                            arr = (
                                _unwrap_array(result_data.get("data"))
                                if isinstance(result_data.get("data"), (dict, list))
                                else None
                            )
                            if arr:
                                preview_items = arr[:3]
                                frontend_data = {
                                    "success": result_data.get("success"),
                                    "_summary": f"{len(arr)} items, {raw_size:,} bytes",
                                    "_preview": preview_items,
                                    "error": result_data.get("error"),
                                }
                            else:
                                frontend_data = {
                                    "success": result_data.get("success"),
                                    "_summary": f"{raw_size:,} bytes",
                                    "error": result_data.get("error"),
                                }
                        else:
                            frontend_data = result_data
                        on_event(
                            {
                                "type": "tool_result",
                                "name": block.name,
                                "result": frontend_data,
                            }
                        )

                        # Send smart summary to LLM for large responses
                        result_text = json.dumps(result_data, default=str)
                        if len(result_text) > 12000:
                            from app.agents.tool_loop import _unwrap_array

                            arr = (
                                _unwrap_array(result_data.get("data"))
                                if isinstance(result_data.get("data"), (dict, list))
                                else None
                            )
                            if arr:
                                # Show field names from first item + first 3 items fully + count
                                first_keys = (
                                    list(arr[0].keys())
                                    if arr and isinstance(arr[0], dict)
                                    else []
                                )
                                summary = {
                                    "success": result_data.get("success"),
                                    "total_items": len(arr),
                                    "fields_available": first_keys,
                                    "sample_items": arr[:3],
                                    "note": f"Response contained {len(arr)} items. Showing first 3 as samples.",
                                }
                                result_text = json.dumps(summary, default=str)
                            else:
                                result_text = result_text[:12000] + '..."}'

                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result_text,
                            }
                        )

                    messages.append({"role": "assistant", "content": assistant_content})
                    messages.append({"role": "user", "content": tool_results})

                    if response.stop_reason == "end_turn":
                        on_event({"type": "complete"})
                        break

            except Exception as exc:
                log.error("Consolidator chat error: %s", exc, exc_info=True)
                on_event({"type": "error", "message": str(exc)})
            finally:
                config_db.close()
                db.close()

        bg = asyncio.ensure_future(asyncio.to_thread(run))
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if event["type"] in ("complete", "error"):
                    break
                await asyncio.sleep(0)
        finally:
            await bg

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
