from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db, get_config_db
from app.db.models import Connection, ConnectionSpec, User
from app.auth.dependencies import get_current_user, require_permission

router = APIRouter()

# Platform-level connectors that are not spec-driven (e.g. the LLM API key).
# Domain connectors (BambooHR, Deputy, etc.) are managed as ConnectorSpecs in the DB.
AVAILABLE_MODELS = [
    {"id": "claude-sonnet-5", "label": "Claude Sonnet 5"},
    {"id": "claude-opus-4-8", "label": "Claude Opus 4.8 (Recommended)"},
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
                {
                    "key": "interpreter_model",
                    "label": "Agent Model",
                    "secret": False,
                    "type": "select",
                    "options": AVAILABLE_MODELS,
                    "default": settings.LLM_INTERPRETER_MODEL,
                },
                {
                    "key": "router_model",
                    "label": "Router Model",
                    "secret": False,
                    "type": "select",
                    "options": AVAILABLE_MODELS,
                    "default": settings.ROUTER_MODEL,
                },
            ],
        },
    ]


# Kept for backwards compat in helper functions that only need names/field keys
PLATFORM_CONNECTORS = _get_platform_connectors()


def _redact_config(
    config: dict, connector_name: str, credential_fields: list | None = None
) -> dict:
    # Check platform connectors first
    meta = next((c for c in PLATFORM_CONNECTORS if c["name"] == connector_name), None)
    fields = meta["fields"] if meta else (credential_fields or [])
    if not fields:
        return config
    secret_keys = {f["key"] for f in fields if f.get("secret")}
    return {k: ("••••••••" if k in secret_keys and v else v) for k, v in config.items()}


@router.get("/connectors")
async def list_connectors(
    venue_id: str | None = None,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    # Filter configs by venue_id (None = platform/global configs)
    config_query = db.query(Connection)
    if venue_id:
        config_query = config_query.filter(Connection.venue_id == venue_id)
    else:
        config_query = config_query.filter(Connection.venue_id.is_(None))
    saved = {r.connector_name: r for r in config_query.all()}
    result = []

    # Platform connectors (Anthropic)
    for meta in PLATFORM_CONNECTORS:
        row = saved.get(meta["name"])
        result.append(
            {
                **meta,
                "configured": row is not None,
                "enabled": row.enabled == "true" if row else False,
                "config": _redact_config(row.config, meta["name"]) if row else {},
            }
        )

    # Spec-driven connectors from the DB
    specs = config_db.query(ConnectionSpec).all()
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
                "config": _redact_config(
                    config_row.config, spec.connector_name, spec.credential_fields
                )
                if config_row
                else {},
            }
            if spec.auth_type == "oauth2" and config_row:
                entry["oauth_connected"] = bool(config_row.access_token)
            if config_row:
                # Connection health — bool(access_token) can't distinguish a
                # live connection from one whose refresh token has died, so the
                # UI keys "Reconnect needed" off this instead.
                entry["needs_reconnect"] = bool(config_row.needs_reconnect)
                entry["last_auth_error"] = config_row.last_auth_error
            result.append(entry)

    return {"connectors": result}


@router.get("/connectors/{connector}/connect-info")
async def connector_connect_info(
    connector: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("settings:connectors")),
):
    """Everything the connect card needs for one connector.

    Connector meta plus the user's venues, each tagged connected /
    needs_reconnect / not_connected. Scoped to exactly the venues the user works
    with — the same set the venue picker shows (get_user_venues) — for everyone,
    admins included, so the card never lists a venue that isn't on their list.
    """
    from app.services.venue_service import get_user_venues

    spec = (
        config_db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == connector)
        .first()
    )
    if not spec:
        raise HTTPException(404, f"Unknown connector: {connector}")

    venues = get_user_venues(db, user.id)

    rows = (
        db.query(Connection)
        .filter(
            Connection.connector_name == connector,
            Connection.user_id.is_(None),
        )
        .all()
    )
    configs = {c.venue_id: c for c in rows if c.venue_id}
    # An organisation-wide row (venue_id NULL) serves every venue that has no
    # row of its own — the same fallback tool_executor uses at runtime, so the
    # status here says what execution will actually find. Prod's BambooHR is
    # exactly this shape: one basic-auth row, no venue, no OAuth token.
    global_cfg = next(
        (c for c in rows if c.venue_id is None and c.enabled == "true"), None
    )
    platform = spec.auth_type == "none"
    required_keys = [
        f["key"]
        for f in (spec.credential_fields or [])
        if isinstance(f, dict) and f.get("key") and f.get("required", True)
    ]

    def _cfg_for(venue_id):
        return configs.get(venue_id) or global_cfg

    def _status(cfg) -> str:
        # Truthful for every credential shape, not just per-venue OAuth:
        #   auth_type none      -> Norm's own pipe, always connected
        #   oauth2              -> needs a live access token
        #   api key/basic/etc.  -> needs its credential fields filled in
        if platform:
            return "connected"
        if not cfg or cfg.enabled != "true":
            return "not_connected"
        if spec.auth_type == "oauth2":
            if not cfg.access_token:
                return "not_connected"
            if cfg.needs_reconnect:
                return "needs_reconnect"
            return "connected"
        values = cfg.config or {}
        if required_keys:
            ok = all(str(values.get(k) or "").strip() for k in required_keys)
        else:
            ok = any(str(v or "").strip() for v in values.values())
        return "connected" if ok else "not_connected"

    def _scope(venue_id) -> str:
        if platform:
            return "platform"
        return "venue" if venue_id in configs else "organisation"

    def _token_binding(cfg) -> tuple[str | None, bool]:
        # Which provider-side company the stored token actually belongs to
        # (LoadedHub returns venue_id/venue_name in the token response), and
        # whether that differs from the venue's configured company id.
        # Surfacing this is what makes a wrong-company binding visible instead
        # of hiding behind a green "Connected" tick.
        if not cfg or not cfg.access_token:
            return None, False
        meta = cfg.oauth_metadata or {}
        name = meta.get("venue_name") or meta.get("userName")
        token_company = str(meta.get("venue_id") or "").strip()
        stored = str((cfg.config or {}).get("x_loaded_company_id") or "").strip()
        wrong = bool(token_company and stored and token_company != stored)
        return (str(name) if name else None), wrong

    bindings = {v.id: _token_binding(_cfg_for(v.id)) for v in venues}
    venue_rows = [
        {
            "venue_id": v.id,
            "venue_name": v.name,
            "status": _status(_cfg_for(v.id)),
            # where the credential that serves this venue lives: its own row,
            # the organisation-wide row, or Norm itself (no credential)
            "scope": _scope(v.id),
            "connected_as": bindings[v.id][0],
            "wrong_company": bindings[v.id][1],
            "last_auth_error": (
                _cfg_for(v.id).last_auth_error if _cfg_for(v.id) else None
            ),
        }
        for v in venues
    ]

    return {
        "connector_name": spec.connector_name,
        "display_name": spec.display_name,
        "auth_type": spec.auth_type,
        "credential_fields": spec.credential_fields or [],
        # One honest answer for callers that don't care about venues: is this
        # connection usable anywhere? (Platform pipes are always configured.)
        "configured": platform
        or (global_cfg is not None and _status(global_cfg) == "connected")
        or any(r["status"] == "connected" for r in venue_rows),
        "platform": platform,
        "venues": venue_rows,
    }


class ConnectorConfigBody(BaseModel):
    config: dict
    enabled: bool = True
    venue_id: str | None = None


@router.put("/connectors/{name}")
async def upsert_connector(
    name: str,
    body: ConnectorConfigBody,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("settings:connectors")),
):
    meta = next((c for c in PLATFORM_CONNECTORS if c["name"] == name), None)
    if not meta:
        # Check if it's a spec-driven connector
        spec = (
            config_db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == name)
            .first()
        )
        if not spec:
            raise HTTPException(404, f"Unknown connector: {name}")

    # Venue-aware lookup
    query = db.query(Connection).filter(Connection.connector_name == name)
    if body.venue_id:
        query = query.filter(Connection.venue_id == body.venue_id)
    else:
        query = query.filter(Connection.venue_id.is_(None))
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
        row = Connection(
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
async def toggle_connector(
    name: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("settings:connectors")),
):
    row = db.query(Connection).filter(Connection.connector_name == name).first()
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
async def delete_connector(
    name: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("settings:connectors")),
):
    row = db.query(Connection).filter(Connection.connector_name == name).first()
    if not row:
        raise HTTPException(404, f"No config for connector: {name}")
    db.delete(row)
    db.commit()
    return {"deleted": True}


class TestBody(BaseModel):
    config: dict


@router.post("/connectors/{name}/test")
async def test_connector(
    name: str,
    body: TestBody,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("settings:connectors")),
):
    if name == "anthropic":
        import anthropic

        # Merge saved credentials with form values (skip redacted)
        config_row = (
            db.query(Connection).filter(Connection.connector_name == name).first()
        )
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
    spec = (
        config_db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == name)
        .first()
    )
    if not spec:
        raise HTTPException(404, f"Unknown connector: {name}")

    if not spec.test_request:
        return {
            "success": False,
            "error": "No test request configured for this connector. Add one in the Connector Spec editor.",
        }

    # Merge saved credentials with any values from the form (non-redacted only)
    config_row = db.query(Connection).filter(Connection.connector_name == name).first()
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
        result = execute_http(
            rendered,
            test_op,
            credentials=credentials,
            auth_type=spec.auth_type,
            auth_config=spec.auth_config,
        )
        if result.success:
            return {
                "success": True,
                "message": "Connected successfully",
                "rendered_request": rendered.to_audit_dict(),
                "response": result.response_payload,
            }
        return {
            "success": False,
            "error": result.error_message or "API returned an error",
            "rendered_request": rendered.to_audit_dict(),
            "response": result.response_payload,
        }
    except Exception as exc:
        return {"success": False, "error": f"Connection test failed: {exc}"}


class ExecuteBody(BaseModel):
    params: dict = {}


# Built-in handlers the web app calls straight through this route. Every other
# handler belongs to the agent, which runs it inside a thread behind its venue,
# approval and tool-menu gates. This route has none of those, and until Sep 2026
# it ran ANY registered handler for any logged-in user — gmail.send_email,
# norm.save_app, norm.run_automated_task. Add an entry only for a handler a
# page really calls, and only if it is safe with no thread and no venue.
_UI_CALLABLE_HANDLERS = frozenset({("norm_hr", "save_criteria")})


@router.post("/connectors/{name}/execute/{action}")
async def execute_connector_action(
    name: str,
    action: str,
    body: ExecuteBody,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Execute a connector endpoint directly (no LLM, no task).

    Serves page components (the Hiring board, the criteria editor). Endpoints
    must be GET; built-in handlers must be on ``_UI_CALLABLE_HANDLERS``.
    Credentials are resolved strictly: the caller's ``venue_id`` if they hold
    that venue, otherwise a platform-wide connection — never another venue's.
    """
    from app.agents.internal_tools import get_handler

    handler = get_handler(name, action)
    if handler:
        if (name, action) not in _UI_CALLABLE_HANDLERS:
            raise HTTPException(
                403, f"{name}.{action} can't be run directly — ask Norm instead"
            )
        result = handler(body.params, db, None)
        db.commit()
        return result

    spec = (
        config_db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == name)
        .first()
    )
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

    # Credentials: the requested venue if the caller holds it, else a
    # platform-wide connection. The old lookup took the first enabled
    # connection of ANY venue, so one org's page could read another's data.
    from app.connectors.tool_executor import _resolve_credentials
    from app.services.venue_service import user_can_access_venue

    params = dict(body.params)
    venue_id = params.pop("venue_id", None)
    params.pop("venue", None)
    params.pop("venue_name", None)
    if venue_id and not user_can_access_venue(db, user.id, venue_id):
        raise HTTPException(403, "You don't have access to that venue.")

    config_row = _resolve_credentials(name, venue_id, db, strict_venue=True)
    if not config_row:
        raise HTTPException(400, f"No credentials configured for {name}")

    from app.connectors.spec_executor import execute_spec

    try:
        result, rendered = execute_spec(spec, tool_def, params, config_row.config, db)
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

    config_row = (
        db.query(Connection)
        .filter(
            Connection.connector_name == "bamboohr",
            Connection.enabled == "true",
        )
        .first()
    )
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

    content_type = (
        resp.headers.get("content-type", "application/octet-stream")
        .split(";")[0]
        .strip()
    )
    cd = resp.headers.get("content-disposition", "")
    headers = {}
    if cd:
        headers["Content-Disposition"] = cd

    return Response(content=resp.content, media_type=content_type, headers=headers)
