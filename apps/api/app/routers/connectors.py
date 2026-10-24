from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db, get_config_db
from app.db.models import Connection, ConnectionSpec, User
from app.auth.dependencies import get_current_user, require_permission
from app.connectors import spec_rows
from app.services.oauth_service import needs_reconnect

router = APIRouter()

# Platform-level connectors that are not spec-driven (e.g. the LLM API key).
# Domain connectors (BambooHR, Deputy, etc.) are managed as ConnectorSpecs in the DB.
AVAILABLE_MODELS = [
    {"id": "claude-opus-5-5", "label": "Claude Opus 5.5 (Recommended)"},
    {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5"},
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
                    "label": "Norm's model",
                    "description": "Answers every conversation and runs scheduled "
                    "tasks; also writes playbooks, specs and tests.",
                    "secret": False,
                    "type": "select",
                    "options": AVAILABLE_MODELS,
                    "default": settings.LLM_INTERPRETER_MODEL,
                },
                {
                    # Stored as router_model: it was the router's until the
                    # router was removed (28 Sep 2026).
                    "key": "router_model",
                    "label": "Fast model",
                    "description": "Small background jobs: summarising long "
                    "conversations and matching invoice lines to stock items.",
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
    from app.db.encrypted import is_secret_key

    meta = next((c for c in PLATFORM_CONNECTORS if c["name"] == connector_name), None)
    fields = meta["fields"] if meta else (credential_fields or [])
    secret_keys = {f["key"] for f in fields if f.get("secret")}
    # Anything named like a secret is masked too: until 19 Oct 2026 a spec
    # connector's PUT answered with its typed API key / password in clear.
    return {
        k: ("••••••••" if (k in secret_keys or is_secret_key(k)) and v else v)
        for k, v in (config or {}).items()
    }


@router.get("/connectors")
async def list_connectors(
    venue_id: str | None = None,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    # Filter configs by venue_id. No venue: the platform rows (the Anthropic
    # key) and the caller's organisation-wide rows — never another
    # organisation's (services/connection_scope.py, 23 Oct 2026).
    from app.services import connection_scope
    from app.services.entitlements import org_id_for_user

    if venue_id:
        # Only a venue the caller can open, or one of their organisation's
        # when they look after its connections or venues (security ledger
        # S25: any signed-in user could read any venue's connection
        # settings, 24 Oct 2026).
        if not connection_scope.can_see_venue_connections(db, user, venue_id):
            raise HTTPException(403, "You can't see that venue's connections.")
        saved = {
            r.connector_name: r
            for r in db.query(Connection).filter(Connection.venue_id == venue_id)
        }
    else:
        saved = {
            r.connector_name: r
            for r in connection_scope.platform_only(db.query(Connection)).all()
        }
        saved.update(
            {
                r.connector_name: r
                for r in connection_scope.org_wide(
                    db.query(Connection), org_id_for_user(user.id, db)
                ).all()
            }
        )
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
                entry["needs_reconnect"] = needs_reconnect(config_row)
                entry["last_auth_error"] = config_row.last_auth_error
            if spec.auth_type == "password_login":
                # One login, several accounts (Loaded's companies): which one
                # this connection works on, and what to call one.
                from app.services.password_login import account_choice, account_name

                entry["account_choice"] = account_choice(spec)
                entry["account"] = account_name(config_row)
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
    # exactly this shape: one basic-auth row, no venue, no OAuth token. Only
    # the caller's own organisation's (services/connection_scope.py).
    from app.services.entitlements import org_id_for_user

    _org = org_id_for_user(user.id, db)
    global_cfg = next(
        (
            c
            for c in rows
            if c.venue_id is None
            and c.enabled == "true"
            and _org
            and c.organization_id == _org
        ),
        None,
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
            if needs_reconnect(cfg):
                return "needs_reconnect"
            return "connected"
        if spec.auth_type == "password_login" and cfg.needs_reconnect:
            return "needs_reconnect"  # its login is being refused
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
        # LoadedHub stores venue_id/venue_name; Xero's hook stores
        # tenant_id/tenant_name against config.xero_tenant_id.
        meta = cfg.oauth_metadata or {}
        name = meta.get("venue_name") or meta.get("tenant_name") or meta.get("userName")
        token_company = str(meta.get("venue_id") or meta.get("tenant_id") or "").strip()
        config = cfg.config or {}
        stored = str(
            config.get("x_loaded_company_id") or config.get("xero_tenant_id") or ""
        ).strip()
        wrong = bool(token_company and stored and token_company != stored)
        return (str(name) if name else None), wrong

    bindings = {v.id: _token_binding(_cfg_for(v.id)) for v in venues}
    login = spec.auth_type == "password_login"
    if login:
        from app.services.password_login import account_choice, account_name
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
            # a password login's account (Loaded Legacy: the Loaded company)
            "account": account_name(_cfg_for(v.id)) if login else None,
        }
        for v in venues
    ]

    return {
        "connector_name": spec.connector_name,
        "display_name": spec.display_name,
        "auth_type": spec.auth_type,
        "credential_fields": spec.credential_fields or [],
        "account_choice": account_choice(spec) if login else None,
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
    spec = None
    if not meta:
        # Check if it's a spec-driven connector
        spec = (
            config_db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == name)
            .first()
        )
        if not spec:
            raise HTTPException(404, f"Unknown connector: {name}")

    # A venue's connection is set by someone who works at that venue.
    if body.venue_id and user.role != "admin":
        from app.services.venue_service import get_user_venues

        if body.venue_id not in {v.id for v in get_user_venues(db, user.id)}:
            raise HTTPException(403, "Not one of your venues")

    # Venue-aware lookup. With no venue: a platform connector's row (the
    # Anthropic key — platform admins only) or the caller's own
    # organisation-wide row, never another organisation's (23 Oct 2026).
    from app.services import connection_scope
    from app.services.entitlements import org_id_for_user

    query = db.query(Connection).filter(Connection.connector_name == name)
    org_id = None
    if body.venue_id:
        query = query.filter(Connection.venue_id == body.venue_id)
    elif meta:
        if user.role != "admin":
            raise HTTPException(403, "Only a platform admin sets the platform key")
        query = connection_scope.platform_only(query)
    else:
        org_id = org_id_for_user(user.id, db)
        if not org_id:
            raise HTTPException(400, "Pick a venue, or act in an organisation")
        query = connection_scope.org_wide(query, org_id)
    row = query.first()

    # Merge: keep existing values for redacted fields
    config = dict(row.config) if row else {}
    for k, v in body.config.items():
        if v != "••••••••":
            config[k] = v

    # A password login is checked before it is kept: a login the service
    # refuses is never saved (services/password_login).
    token = account = None
    if spec is not None and spec.auth_type == "password_login" and body.enabled:
        from app.services.password_login import (
            ChooseAccount,
            PasswordLoginError,
            check_and_prepare,
        )

        try:
            config, token, account = check_and_prepare(spec, db, body.venue_id, config)
        except ChooseAccount as exc:
            # The login works; the form lists the accounts to pick from.
            raise HTTPException(
                409, {"message": str(exc), "choose": exc.choice}
            ) from None
        except PasswordLoginError as exc:
            raise HTTPException(400, str(exc)) from None

    if row:
        row.config = config
        row.enabled = "true" if body.enabled else "false"
    else:
        row = Connection(
            connector_name=name,
            venue_id=body.venue_id,
            organization_id=org_id,
            config=config,
            enabled="true" if body.enabled else "false",
        )
        db.add(row)
    if token is not None:
        from app.services.password_login import store_checked_token

        store_checked_token(row, token, account)
    db.commit()
    db.refresh(row)
    return {
        "name": row.connector_name,
        "enabled": row.enabled == "true",
        "config": _redact_config(
            row.config, name, spec.credential_fields if spec is not None else None
        ),
    }


def _platform_row(db: Session, name: str, user: User):
    """The platform row for a platform connector (the Anthropic key), for a
    platform admin. These routes name only the connector, so they used to act
    on whichever row came first — another organisation's included
    (23 Oct 2026)."""
    from app.services import connection_scope

    if not any(c["name"] == name for c in PLATFORM_CONNECTORS):
        raise HTTPException(404, f"{name} is set per venue or per organisation")
    if user.role != "admin":
        raise HTTPException(403, "Only a platform admin changes the platform key")
    return connection_scope.platform_only(
        db.query(Connection).filter(Connection.connector_name == name)
    ).first()


@router.patch("/connectors/{name}/toggle")
async def toggle_connector(
    name: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("settings:connectors")),
):
    row = _platform_row(db, name, user)
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
    row = _platform_row(db, name, user)
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
        config_row = _platform_row(db, name, user)
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

    # Merge saved credentials with any values from the form (non-redacted
    # only) — the caller's organisation-wide row, never another
    # organisation's (23 Oct 2026).
    from app.services import connection_scope
    from app.services.entitlements import org_id_for_user

    config_row = connection_scope.org_wide(
        db.query(Connection).filter(Connection.connector_name == name),
        org_id_for_user(user.id, db),
    ).first()
    credentials = dict(config_row.config) if config_row else {}
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


def _writes(tool_def: dict, connector: str, config_db: Session) -> bool:
    """A tool that changes something, whatever its declared method: labelled
    as writing or drafting, or a consolidator that may call a write.

    A consolidator's ``allowed_write_actions`` counts only for the actions in
    it that really write. Xero's bills and profit-and-loss reads list
    ``resolve_dates`` there — a read the list doesn't need — and counting the
    list as a whole refused both pages from 23 to 24 Oct 2026. An action that
    can't be found counts as a write, so an unknown name never passes."""
    if spec_rows.effect(tool_def) in ("write", "draft"):
        return True
    declared = (tool_def.get("consolidator_config") or {}).get(
        "allowed_write_actions"
    ) or []
    return any(_action_writes(str(a), connector, config_db) for a in declared)


def _action_writes(name: str, connector: str, config_db: Session) -> bool:
    """Whether ``name`` (``connector.action``, or a bare action on this
    connector or Norm's own) is a write: a non-GET endpoint or a tool
    labelled writing or drafting — the executor's own test."""
    conn, _, action = name.rpartition(".")
    for candidate in [conn] if conn else [connector, "norm"]:
        spec = (
            config_db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == candidate)
            .first()
        )
        row = next(
            (
                t
                for t in (spec_rows.rows(spec) if spec else [])
                if isinstance(t, dict) and t.get("action") == action
            ),
            None,
        )
        if row is not None:
            return str(row.get("method", "GET")).upper() != "GET" or (
                spec_rows.effect(row) in ("write", "draft")
            )
    return True


@router.post("/connectors/{name}/execute/{action}")
def execute_connector_action(
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
    that venue, otherwise their organisation's organisation-wide connection —
    never another venue's or another organisation's.

    And only a tool the caller's role reaches (services/screen_access): the
    venue alone let a Kitchen role read any GET row, sales and pay included.
    """
    from app.agents.internal_tools import get_handler
    from app.services import screen_access

    screen_access.require_tool(db, config_db, user, name, action)
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
    for t in spec_rows.rows(spec):
        if t.get("action") == action:
            tool_def = t
            break
    if not tool_def:
        raise HTTPException(404, f"Tool not found: {action}")

    if tool_def.get("method", "POST").upper() != "GET" or _writes(
        tool_def, name, config_db
    ):
        # GET-only means READ-only: an internal-mode write declares GET too
        # (effect "write", or a consolidator with allowed_write_actions). A
        # page's write goes through the preview/confirm routes below.
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

    if (tool_def.get("consolidator_config") or {}).get("function_code"):
        # A GET consolidator (the Bills page's xero.get_bills): run it the way
        # dashboards and MCP do — the shared executor resolves this venue's
        # credentials strictly and runs the sandbox. execute_spec below is for
        # raw HTTP rows only.
        from app.connectors.tool_executor import execute_connector_tool
        from app.services import caller_scope

        # The signed-in person is the one acting, so a tool row's `requires`
        # is checked against their role (Oct 2026).
        with caller_scope.use(caller_scope.for_user(db, user)):
            tool_result = execute_connector_tool(
                connector_name=name,
                action=action,
                params={**params, **({"venue_id": venue_id} if venue_id else {})},
                db=db,
                config_db=config_db,
                venue_id=venue_id,
                strict_venue=True,
                # a venue-less call acts in the caller's organisation
                user=user,
            )
        return {
            "success": tool_result.success,
            "data": tool_result.payload,
            "error": tool_result.error,
        }

    from app.services.entitlements import org_id_for_user

    config_row = _resolve_credentials(
        name, venue_id, db, strict_venue=True, org_id=org_id_for_user(user.id, db)
    )
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


class PageWriteBody(BaseModel):
    params: dict = {}
    venue_id: str | None = None
    #: confirm only — what the person's card carried.
    fingerprint: str | None = None


@router.post("/connectors/{name}/execute/{action}/preview")
async def preview_connector_write(
    name: str,
    action: str,
    body: PageWriteBody,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """What a page's write button would change — the card and its fingerprint,
    from live data, nothing written (services/page_writes). The write half of
    the component-API door: the GET route above never runs a write."""
    from app.services import page_writes, screen_access

    screen_access.require_tool(db, config_db, user, name, action)
    return page_writes.preview(
        db,
        tool=f"{name}.{action}",
        params=body.params,
        user=user,
        venue_id=body.venue_id,
        config_db=config_db,
    )


@router.post("/connectors/{name}/execute/{action}/confirm")
async def confirm_connector_write(
    name: str,
    action: str,
    body: PageWriteBody,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Run the write the person previewed: refused (409) if Loaded or Xero
    moved since they looked; recorded against the person who pressed."""
    from app.services import page_writes, screen_access

    screen_access.require_tool(db, config_db, user, name, action)
    result = page_writes.confirm(
        db,
        tool=f"{name}.{action}",
        params=body.params,
        user=user,
        venue_id=body.venue_id,
        fingerprint=body.fingerprint,
        config_db=config_db,
    )
    db.commit()
    return result


@router.get("/connectors/bamboohr/files/{file_id}")
async def download_bamboohr_file(
    file_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Proxy a file download from BambooHR (keeps API key server-side).

    The Hiring board's (an applicant's CV): only for a role that reaches it.
    """
    import httpx
    from fastapi.responses import Response

    from app.services import screen_access

    screen_access.require_any_component(
        db, config_db, user, ("hiring_board", "employee_records")
    )

    # The caller's organisation's BambooHR — it took whichever came first,
    # so anyone signed in could fetch another organisation's files
    # (23 Oct 2026).
    from app.services import connection_scope
    from app.services.entitlements import org_id_for_user

    org_id = org_id_for_user(user.id, db)
    config_row = connection_scope.in_org(
        db.query(Connection).filter(
            Connection.connector_name == "bamboohr",
            Connection.enabled == "true",
        ),
        org_id,
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
