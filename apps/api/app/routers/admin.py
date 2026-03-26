"""Admin API — deployment management, config sync, and (Phase 4) test management."""

import copy
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import require_permission
from app.config import settings
from app.db.engine import get_db
from app.db.models import (
    AgentConfig,
    AgentConnectorBinding,
    ConnectorSpec,
    Deployment,
    E2ETest,
    E2ETestRun,
    User,
)

router = APIRouter(tags=["admin"])
log = logging.getLogger(__name__)

REDACTED = "***REDACTED***"

SENSITIVE_KEY_FRAGMENTS = ("secret", "key", "token", "password")

ENV_URLS: dict[str, str] = {
    "local": "http://localhost:8000",
    "testing": "https://testing.bettercallnorm.com",
    "staging": "https://staging.bettercallnorm.com",
    "production": "https://bettercallnorm.com",
}


# ── Helpers ─────────────────────────────────────────────────────────


def _require_admin(user: User) -> None:
    if user.role != "admin":
        raise HTTPException(403, "Admin access required")


# ── Config Sync Helpers ─────────────────────────────────────────────


def _redact_dict(d: dict | None, parent_key: str = "") -> dict | None:
    """Recursively redact sensitive values in a dict."""
    if d is None:
        return None
    out: dict[str, Any] = {}
    for k, v in d.items():
        if any(frag in k.lower() for frag in SENSITIVE_KEY_FRAGMENTS):
            out[k] = REDACTED
        elif isinstance(v, dict):
            out[k] = _redact_dict(v, k)
        else:
            out[k] = v
    return out


def _redact_connector_spec(spec_dict: dict) -> dict:
    """Redact auth_config and oauth_config sensitive fields."""
    result = copy.deepcopy(spec_dict)
    if "auth_config" in result and isinstance(result["auth_config"], dict):
        result["auth_config"] = _redact_dict(result["auth_config"])
    if "oauth_config" in result and isinstance(result["oauth_config"], dict):
        result["oauth_config"] = _redact_dict(result["oauth_config"])
    return result


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


def _agent_to_dict(agent: AgentConfig) -> dict:
    return {
        "id": agent.id,
        "agent_slug": agent.agent_slug,
        "display_name": agent.display_name,
        "system_prompt": agent.system_prompt,
        "description": agent.description,
        "enabled": agent.enabled,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
    }


def _binding_to_dict(binding: AgentConnectorBinding) -> dict:
    return {
        "id": binding.id,
        "agent_slug": binding.agent_slug,
        "connector_name": binding.connector_name,
        "capabilities": binding.capabilities,
        "enabled": binding.enabled,
        "created_at": binding.created_at.isoformat() if binding.created_at else None,
        "updated_at": binding.updated_at.isoformat() if binding.updated_at else None,
    }


def _dict_structure(d: dict | None) -> dict | None:
    """Return structure of a dict (keys only, values replaced with types) for diffing."""
    if d is None:
        return None
    return {k: type(v).__name__ for k, v in d.items()}


def _verify_sync_token(token: str | None) -> bool:
    """Check if the provided token matches the CONFIG_SYNC_SECRET."""
    return bool(
        token and settings.CONFIG_SYNC_SECRET and token == settings.CONFIG_SYNC_SECRET
    )


# ── Config Sync Schemas ────────────────────────────────────────────


class ConfigImportSelections(BaseModel):
    connector_specs: list[str] = []
    agent_configs: list[str] = []
    agent_bindings: list[list[str]] = []  # [[agent_slug, connector_name], ...]


class ConfigImportRequest(BaseModel):
    config: dict
    selections: ConfigImportSelections


class ConfigFetchRemoteRequest(BaseModel):
    environment: str  # "testing" | "staging" | "production" | "local"


# ── Schemas ─────────────────────────────────────────────────────────


class DeployWebhookPayload(BaseModel):
    environment: str
    image_tag: str
    git_sha: str
    status: str  # pending | running | success | failed
    commit_message: str | None = None
    logs_url: str | None = None
    triggered_by: str | None = None


class PromoteRequest(BaseModel):
    image_tag: str
    target_environment: str = "production"


# ── Endpoints ───────────────────────────────────────────────────────


@router.get("/admin/deployments")
def list_deployments(
    environment: str | None = None,
    limit: int = 20,
    user: User = Depends(require_permission("admin:deployments")),
    db: Session = Depends(get_db),
):
    """List recent deployments, optionally filtered by environment."""
    q = db.query(Deployment).order_by(Deployment.started_at.desc())
    if environment:
        q = q.filter(Deployment.environment == environment)
    rows = q.limit(limit).all()
    return {
        "deployments": [
            {
                "id": d.id,
                "environment": d.environment,
                "image_tag": d.image_tag,
                "git_sha": d.git_sha,
                "commit_message": d.commit_message,
                "status": d.status,
                "started_at": d.started_at.isoformat() if d.started_at else None,
                "completed_at": d.completed_at.isoformat() if d.completed_at else None,
                "logs_url": d.logs_url,
                "triggered_by": d.triggered_by,
            }
            for d in rows
        ]
    }


@router.get("/admin/environments")
def list_environments(
    user: User = Depends(require_permission("admin:deployments")),
    db: Session = Depends(get_db),
):
    """List all environments with their latest deployment status."""
    envs = []
    for env_name in ("testing", "staging", "production"):
        latest = (
            db.query(Deployment)
            .filter(Deployment.environment == env_name)
            .order_by(Deployment.started_at.desc())
            .first()
        )
        envs.append(
            {
                "name": env_name,
                "latest_deploy": {
                    "image_tag": latest.image_tag,
                    "git_sha": latest.git_sha,
                    "status": latest.status,
                    "started_at": latest.started_at.isoformat()
                    if latest.started_at
                    else None,
                    "commit_message": latest.commit_message,
                }
                if latest
                else None,
            }
        )
    return {"environments": envs}


@router.post("/admin/deploy-webhook")
def deploy_webhook(
    payload: DeployWebhookPayload,
    db: Session = Depends(get_db),
):
    """Receive deploy status from GitHub Actions.

    This endpoint is called by the CD pipeline after each deployment.
    No auth required — will be secured via webhook secret in Phase 3.
    """
    # Check if we already have a deployment record for this sha+env
    existing = (
        db.query(Deployment)
        .filter(
            Deployment.git_sha == payload.git_sha,
            Deployment.environment == payload.environment,
        )
        .first()
    )

    if existing:
        existing.status = payload.status
        existing.logs_url = payload.logs_url
        if payload.status in ("success", "failed"):
            existing.completed_at = datetime.now(timezone.utc)
    else:
        dep = Deployment(
            environment=payload.environment,
            image_tag=payload.image_tag,
            git_sha=payload.git_sha,
            commit_message=payload.commit_message,
            status=payload.status,
            logs_url=payload.logs_url,
            triggered_by=payload.triggered_by or "ci",
        )
        db.add(dep)

    db.commit()
    return {"ok": True}


@router.post("/admin/promote")
def promote(
    body: PromoteRequest,
    user: User = Depends(require_permission("admin:deployments")),
    db: Session = Depends(get_db),
):
    """Trigger a production deployment via GitHub Actions workflow_dispatch.

    TODO (Phase 3): Integrate with GitHub API to trigger the deploy workflow.
    """
    # For now, create a pending deployment record
    dep = Deployment(
        environment=body.target_environment,
        image_tag=body.image_tag,
        git_sha=body.image_tag,  # SHA is the image tag in our convention
        status="pending",
        triggered_by=user.email,
    )
    db.add(dep)
    db.commit()

    # TODO (Phase 3): Call GitHub API
    # import httpx
    # httpx.post(
    #     f"https://api.github.com/repos/{OWNER}/{REPO}/actions/workflows/deploy.yml/dispatches",
    #     headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
    #     json={"ref": "main", "inputs": {"environment": body.target_environment, "image_tag": body.image_tag}},
    # )

    return {"ok": True, "deployment_id": dep.id}


@router.post("/admin/rollback")
def rollback(
    body: PromoteRequest,
    user: User = Depends(require_permission("admin:deployments")),
    db: Session = Depends(get_db),
):
    """Roll back an environment to a previous known-good deployment.

    Finds the last successful deployment for the target environment
    and re-deploys that image tag.
    """

    # Find the last successful deploy for this environment (excluding the current one)
    last_good = (
        db.query(Deployment)
        .filter(
            Deployment.environment == body.target_environment,
            Deployment.status == "success",
            Deployment.image_tag != body.image_tag,
        )
        .order_by(Deployment.started_at.desc())
        .first()
    )

    if not last_good:
        raise HTTPException(
            404, "No previous successful deployment found to roll back to"
        )

    # Create a pending rollback deployment
    dep = Deployment(
        environment=body.target_environment,
        image_tag=last_good.image_tag,
        git_sha=last_good.git_sha,
        commit_message=f"Rollback to {last_good.git_sha[:7]} (triggered by {user.email})",
        status="pending",
        triggered_by=user.email,
    )
    db.add(dep)
    db.commit()

    return {
        "ok": True,
        "deployment_id": dep.id,
        "rolling_back_to": {
            "image_tag": last_good.image_tag,
            "git_sha": last_good.git_sha,
            "deployed_at": last_good.started_at.isoformat()
            if last_good.started_at
            else None,
        },
    }


# ── Config Sync Endpoints ─────────────────────────────────────────


def _build_config_export(db: Session) -> dict:
    """Shared logic for config-export: query DB, redact, and return."""
    specs = db.query(ConnectorSpec).all()
    agents = db.query(AgentConfig).all()
    bindings = db.query(AgentConnectorBinding).all()

    return {
        "connector_specs": [_redact_connector_spec(_spec_to_dict(s)) for s in specs],
        "agent_configs": [_agent_to_dict(a) for a in agents],
        "agent_bindings": [_binding_to_dict(b) for b in bindings],
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "environment": settings.ENVIRONMENT,
    }


_optional_bearer = HTTPBearer(auto_error=False)


def _require_admin_or_sync_token(
    request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
) -> None:
    """Allow access if the request carries a valid sync token OR admin user auth."""
    # 1. Check X-Config-Sync-Token header (service-to-service)
    token = request.headers.get("x-config-sync-token")
    if _verify_sync_token(token):
        return

    # 2. Fall back to Bearer token auth (admin user)
    if credentials is None:
        raise HTTPException(
            401, "Valid X-Config-Sync-Token or admin:system permission required"
        )
    from app.auth.security import decode_access_token

    try:
        payload = decode_access_token(credentials.credentials)
        user_id: str | None = payload.get("sub")
        if not user_id:
            raise HTTPException(401, "Invalid token")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Invalid token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or inactive")
    if user.role != "admin":
        raise HTTPException(403, "admin:system permission required")


@router.get("/admin/config-export")
def config_export_endpoint(
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_admin_or_sync_token),
):
    """Export all system config (connector specs, agent configs, bindings).

    Auth: admin:system permission OR valid X-Config-Sync-Token header.
    Sensitive fields in auth_config and oauth_config are redacted.
    ConnectorConfig (per-venue credentials) is NOT included.
    """
    return _build_config_export(db)


@router.post("/admin/config-diff")
def config_diff(
    body: dict,
    user: User = Depends(require_permission("admin:system")),
    db: Session = Depends(get_db),
):
    """Compare import JSON against current DB state.

    Returns added/modified/removed/unchanged for each config type.
    """
    # --- Connector Specs ---
    db_specs = {s.connector_name: s for s in db.query(ConnectorSpec).all()}
    import_specs = {s["connector_name"]: s for s in body.get("connector_specs", [])}

    spec_result = _diff_connector_specs(db_specs, import_specs)

    # --- Agent Configs ---
    db_agents = {a.agent_slug: a for a in db.query(AgentConfig).all()}
    import_agents = {a["agent_slug"]: a for a in body.get("agent_configs", [])}

    agent_result = _diff_agent_configs(db_agents, import_agents)

    # --- Agent Bindings ---
    db_bindings = {
        (b.agent_slug, b.connector_name): b
        for b in db.query(AgentConnectorBinding).all()
    }
    import_bindings = {
        (b["agent_slug"], b["connector_name"]): b
        for b in body.get("agent_bindings", [])
    }

    binding_result = _diff_agent_bindings(db_bindings, import_bindings)

    return {
        "connector_specs": spec_result,
        "agent_configs": agent_result,
        "agent_bindings": binding_result,
    }


def _diff_connector_specs(
    db_specs: dict[str, ConnectorSpec], import_specs: dict[str, dict]
) -> dict:
    added = []
    modified = []
    removed = []
    unchanged = []

    all_names = set(db_specs.keys()) | set(import_specs.keys())
    for name in sorted(all_names):
        if name not in db_specs:
            added.append({"connector_name": name})
        elif name not in import_specs:
            removed.append({"connector_name": name})
        else:
            changes = _compare_spec_fields(db_specs[name], import_specs[name])
            if changes:
                modified.append({"connector_name": name, "changes": changes})
            else:
                unchanged.append({"connector_name": name})

    return {
        "added": added,
        "modified": modified,
        "removed": removed,
        "unchanged": unchanged,
    }


def _compare_spec_fields(db_spec: ConnectorSpec, imp: dict) -> list[dict]:
    changes = []
    # Simple scalar fields
    for field in (
        "display_name",
        "category",
        "execution_mode",
        "auth_type",
        "base_url_template",
    ):
        db_val = getattr(db_spec, field, None)
        imp_val = imp.get(field)
        if db_val != imp_val:
            changes.append({"field": field, "old_value": db_val, "new_value": imp_val})

    # Tools: compare count + action names
    db_tools = db_spec.tools or []
    imp_tools = imp.get("tools", [])
    db_tool_actions = sorted([t.get("action", "") for t in db_tools])
    imp_tool_actions = sorted([t.get("action", "") for t in imp_tools])
    if len(db_tools) != len(imp_tools) or db_tool_actions != imp_tool_actions:
        changes.append(
            {
                "field": "tools",
                "old_value": f"{len(db_tools)} tools: {db_tool_actions}",
                "new_value": f"{len(imp_tools)} tools: {imp_tool_actions}",
            }
        )

    # Auth config: structure only
    db_auth_struct = _dict_structure(db_spec.auth_config)
    imp_auth_struct = _dict_structure(imp.get("auth_config"))
    if db_auth_struct != imp_auth_struct:
        changes.append(
            {
                "field": "auth_config",
                "old_value": db_auth_struct,
                "new_value": imp_auth_struct,
            }
        )

    # Credential fields
    db_creds = db_spec.credential_fields or []
    imp_creds = imp.get("credential_fields", [])
    if db_creds != imp_creds:
        changes.append(
            {
                "field": "credential_fields",
                "old_value": db_creds,
                "new_value": imp_creds,
            }
        )

    # OAuth config: structure only
    db_oauth_struct = _dict_structure(db_spec.oauth_config)
    imp_oauth_struct = _dict_structure(imp.get("oauth_config"))
    if db_oauth_struct != imp_oauth_struct:
        changes.append(
            {
                "field": "oauth_config",
                "old_value": db_oauth_struct,
                "new_value": imp_oauth_struct,
            }
        )

    return changes


def _diff_agent_configs(
    db_agents: dict[str, AgentConfig], import_agents: dict[str, dict]
) -> dict:
    added = []
    modified = []
    removed = []
    unchanged = []

    all_slugs = set(db_agents.keys()) | set(import_agents.keys())
    for slug in sorted(all_slugs):
        if slug not in db_agents:
            added.append({"agent_slug": slug})
        elif slug not in import_agents:
            removed.append({"agent_slug": slug})
        else:
            changes = _compare_agent_fields(db_agents[slug], import_agents[slug])
            if changes:
                modified.append({"agent_slug": slug, "changes": changes})
            else:
                unchanged.append({"agent_slug": slug})

    return {
        "added": added,
        "modified": modified,
        "removed": removed,
        "unchanged": unchanged,
    }


def _compare_agent_fields(db_agent: AgentConfig, imp: dict) -> list[dict]:
    changes = []
    for field in ("display_name", "description"):
        db_val = getattr(db_agent, field, None)
        imp_val = imp.get(field)
        if db_val != imp_val:
            changes.append({"field": field, "old_value": db_val, "new_value": imp_val})

    # System prompt: show first 100 chars + length
    db_prompt = db_agent.system_prompt or ""
    imp_prompt = imp.get("system_prompt") or ""
    if db_prompt != imp_prompt:
        changes.append(
            {
                "field": "system_prompt",
                "old_value": f"{db_prompt[:100]}... ({len(db_prompt)} chars)"
                if len(db_prompt) > 100
                else db_prompt,
                "new_value": f"{imp_prompt[:100]}... ({len(imp_prompt)} chars)"
                if len(imp_prompt) > 100
                else imp_prompt,
            }
        )

    return changes


def _diff_agent_bindings(
    db_bindings: dict[tuple[str, str], AgentConnectorBinding],
    import_bindings: dict[tuple[str, str], dict],
) -> dict:
    added = []
    modified = []
    removed = []
    unchanged = []

    all_keys = set(db_bindings.keys()) | set(import_bindings.keys())
    for key in sorted(all_keys):
        slug, connector = key
        label = {"agent_slug": slug, "connector_name": connector}
        if key not in db_bindings:
            added.append(label)
        elif key not in import_bindings:
            removed.append(label)
        else:
            changes = _compare_binding_fields(db_bindings[key], import_bindings[key])
            if changes:
                modified.append({**label, "changes": changes})
            else:
                unchanged.append(label)

    return {
        "added": added,
        "modified": modified,
        "removed": removed,
        "unchanged": unchanged,
    }


def _compare_binding_fields(db_binding: AgentConnectorBinding, imp: dict) -> list[dict]:
    changes = []
    if db_binding.enabled != imp.get("enabled"):
        changes.append(
            {
                "field": "enabled",
                "old_value": db_binding.enabled,
                "new_value": imp.get("enabled"),
            }
        )

    db_actions = sorted([c.get("action", "") for c in (db_binding.capabilities or [])])
    imp_actions = sorted([c.get("action", "") for c in (imp.get("capabilities") or [])])
    if db_actions != imp_actions:
        changes.append(
            {
                "field": "capabilities",
                "old_value": db_actions,
                "new_value": imp_actions,
            }
        )

    return changes


@router.post("/admin/config-import")
def config_import(
    body: ConfigImportRequest,
    user: User = Depends(require_permission("admin:system")),
    db: Session = Depends(get_db),
):
    """Import selected config items. Skips redacted values."""
    config = body.config
    selections = body.selections
    applied = {"connector_specs": 0, "agent_configs": 0, "agent_bindings": 0}

    # --- Connector Specs ---
    import_specs_by_name = {
        s["connector_name"]: s for s in config.get("connector_specs", [])
    }
    for name in selections.connector_specs:
        if name not in import_specs_by_name:
            continue
        imp = import_specs_by_name[name]
        spec = (
            db.query(ConnectorSpec).filter(ConnectorSpec.connector_name == name).first()
        )
        if spec is None:
            spec = ConnectorSpec(connector_name=name)
            db.add(spec)

        _apply_spec_fields(spec, imp)
        applied["connector_specs"] += 1

    # --- Agent Configs ---
    import_agents_by_slug = {
        a["agent_slug"]: a for a in config.get("agent_configs", [])
    }
    for slug in selections.agent_configs:
        if slug not in import_agents_by_slug:
            continue
        imp = import_agents_by_slug[slug]
        agent = db.query(AgentConfig).filter(AgentConfig.agent_slug == slug).first()
        if agent is None:
            agent = AgentConfig(agent_slug=slug)
            db.add(agent)

        _apply_agent_fields(agent, imp)
        applied["agent_configs"] += 1

    # --- Agent Bindings ---
    import_bindings_by_key = {
        (b["agent_slug"], b["connector_name"]): b
        for b in config.get("agent_bindings", [])
    }
    for pair in selections.agent_bindings:
        if len(pair) != 2:
            continue
        key = (pair[0], pair[1])
        if key not in import_bindings_by_key:
            continue
        imp = import_bindings_by_key[key]
        binding = (
            db.query(AgentConnectorBinding)
            .filter(
                AgentConnectorBinding.agent_slug == key[0],
                AgentConnectorBinding.connector_name == key[1],
            )
            .first()
        )
        if binding is None:
            binding = AgentConnectorBinding(agent_slug=key[0], connector_name=key[1])
            db.add(binding)

        _apply_binding_fields(binding, imp)
        applied["agent_bindings"] += 1

    db.commit()
    return {"applied": applied}


def _is_redacted(value: Any) -> bool:
    """Check whether a value is the redaction sentinel."""
    return value == REDACTED


def _strip_redacted(d: dict | None) -> dict | None:
    """Return a copy of dict with redacted values removed (keys kept only if not redacted)."""
    if d is None:
        return None
    out: dict[str, Any] = {}
    for k, v in d.items():
        if _is_redacted(v):
            continue  # skip — don't overwrite DB value
        if isinstance(v, dict):
            out[k] = _strip_redacted(v)
        else:
            out[k] = v
    return out


def _apply_spec_fields(spec: ConnectorSpec, imp: dict) -> None:
    """Apply import fields to a ConnectorSpec, skipping redacted values."""
    for field in (
        "display_name",
        "category",
        "execution_mode",
        "auth_type",
        "base_url_template",
        "api_documentation",
        "version",
        "enabled",
    ):
        val = imp.get(field)
        if val is not None and not _is_redacted(val):
            setattr(spec, field, val)

    # JSON fields — skip redacted values within dicts
    if "tools" in imp and not _is_redacted(imp["tools"]):
        spec.tools = imp["tools"]
    if "auth_config" in imp:
        cleaned = _strip_redacted(imp["auth_config"])
        if cleaned is not None:
            # Merge with existing to preserve redacted keys
            existing = spec.auth_config or {}
            existing.update(cleaned)
            spec.auth_config = existing
    if "credential_fields" in imp and not _is_redacted(imp["credential_fields"]):
        spec.credential_fields = imp["credential_fields"]
    if "oauth_config" in imp:
        cleaned = _strip_redacted(imp["oauth_config"])
        if cleaned is not None:
            existing = spec.oauth_config or {}
            existing.update(cleaned)
            spec.oauth_config = existing
    if "example_requests" in imp and not _is_redacted(imp["example_requests"]):
        spec.example_requests = imp["example_requests"]
    if "test_request" in imp and not _is_redacted(imp["test_request"]):
        spec.test_request = imp["test_request"]


def _apply_agent_fields(agent: AgentConfig, imp: dict) -> None:
    """Apply import fields to an AgentConfig, skipping redacted values."""
    for field in ("display_name", "description", "system_prompt", "enabled"):
        val = imp.get(field)
        if val is not None and not _is_redacted(val):
            setattr(agent, field, val)


def _apply_binding_fields(binding: AgentConnectorBinding, imp: dict) -> None:
    """Apply import fields to an AgentConnectorBinding."""
    if "enabled" in imp:
        binding.enabled = imp["enabled"]
    if "capabilities" in imp:
        binding.capabilities = imp["capabilities"]


@router.post("/admin/config-reseed")
def config_reseed(
    user: User = Depends(require_permission("admin:system")),
    db: Session = Depends(get_db),
):
    """Emergency recovery: reseed all system config from code definitions."""
    from app.services.system_config_sync import sync_system_config

    sync_system_config(db)
    return {"ok": True, "message": "System configuration reseeded from code"}


@router.post("/admin/config-fetch-remote")
async def config_fetch_remote(
    body: ConfigFetchRemoteRequest,
    user: User = Depends(require_permission("admin:system")),
):
    """Fetch config export from a remote Norm environment."""
    if body.environment not in ENV_URLS:
        raise HTTPException(
            400,
            f"Unknown environment: {body.environment}. "
            f"Valid: {', '.join(ENV_URLS.keys())}",
        )

    base_url = ENV_URLS[body.environment]
    url = f"{base_url}/api/admin/config-export"

    if not settings.CONFIG_SYNC_SECRET:
        raise HTTPException(
            500,
            "CONFIG_SYNC_SECRET is not configured. "
            "Set it in both environments to enable cross-env config sync.",
        )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                url,
                headers={"X-Config-Sync-Token": settings.CONFIG_SYNC_SECRET},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            502,
            f"Remote environment returned {exc.response.status_code}: "
            f"{exc.response.text[:500]}",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            502,
            f"Failed to reach {body.environment} ({base_url}): {exc}",
        ) from exc


# ── E2E Test Schemas ──────────────────────────────────────────────


class GenerateTestRequest(BaseModel):
    description: str


class SaveTestRequest(BaseModel):
    name: str
    description: str
    playwright_script: str
    steps: list = []


class UpdateTestRequest(BaseModel):
    name: str | None = None
    playwright_script: str | None = None
    steps: list | None = None


class RunTestsRequest(BaseModel):
    environment: str = "testing"
    test_ids: list[str] | None = None  # None means all


class TestRunWebhookPayload(BaseModel):
    test_id: str | None = None
    environment: str
    status: str
    duration_ms: int | None = None
    error_message: str | None = None
    screenshots: list = []
    video_url: str | None = None
    git_sha: str | None = None


# ── E2E Test Endpoints ────────────────────────────────────────────


@router.post("/admin/tests/generate")
async def generate_test(
    body: GenerateTestRequest,
    user: User = Depends(require_permission("admin:tests")),
):
    """Generate a Playwright test from a natural language description."""
    from app.services.test_generator import generate_test as _generate

    result = await _generate(body.description)
    return result


@router.post("/admin/tests")
def save_test(
    body: SaveTestRequest,
    user: User = Depends(require_permission("admin:tests")),
    db: Session = Depends(get_db),
):
    """Save a generated test to the suite."""
    test = E2ETest(
        name=body.name,
        description=body.description,
        playwright_script=body.playwright_script,
        steps_json=body.steps,
        created_by=user.id,
    )
    db.add(test)
    db.commit()
    db.refresh(test)
    return {
        "id": test.id,
        "name": test.name,
        "description": test.description,
        "playwright_script": test.playwright_script,
        "steps": test.steps_json,
        "created_at": test.created_at.isoformat() if test.created_at else None,
    }


@router.get("/admin/tests")
def list_tests(
    user: User = Depends(require_permission("admin:tests")),
    db: Session = Depends(get_db),
):
    """List all E2E tests with last run status."""
    rows = db.query(E2ETest).order_by(E2ETest.created_at.desc()).all()
    return {
        "tests": [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "playwright_script": t.playwright_script,
                "steps": t.steps_json,
                "last_run_status": t.last_run_status,
                "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in rows
        ]
    }


@router.get("/admin/tests/{test_id}")
def get_test(
    test_id: str,
    user: User = Depends(require_permission("admin:tests")),
    db: Session = Depends(get_db),
):
    """Get a single E2E test detail."""
    test = db.query(E2ETest).filter(E2ETest.id == test_id).first()
    if not test:
        raise HTTPException(404, "Test not found")
    return {
        "id": test.id,
        "name": test.name,
        "description": test.description,
        "playwright_script": test.playwright_script,
        "steps": test.steps_json,
        "last_run_status": test.last_run_status,
        "last_run_at": test.last_run_at.isoformat() if test.last_run_at else None,
        "created_at": test.created_at.isoformat() if test.created_at else None,
        "updated_at": test.updated_at.isoformat() if test.updated_at else None,
    }


@router.put("/admin/tests/{test_id}")
def update_test(
    test_id: str,
    body: UpdateTestRequest,
    user: User = Depends(require_permission("admin:tests")),
    db: Session = Depends(get_db),
):
    """Update an existing E2E test."""
    test = db.query(E2ETest).filter(E2ETest.id == test_id).first()
    if not test:
        raise HTTPException(404, "Test not found")
    if body.name is not None:
        test.name = body.name
    if body.playwright_script is not None:
        test.playwright_script = body.playwright_script
    if body.steps is not None:
        test.steps_json = body.steps
    db.commit()
    db.refresh(test)
    return {
        "id": test.id,
        "name": test.name,
        "description": test.description,
        "playwright_script": test.playwright_script,
        "steps": test.steps_json,
        "updated_at": test.updated_at.isoformat() if test.updated_at else None,
    }


@router.delete("/admin/tests/{test_id}")
def delete_test(
    test_id: str,
    user: User = Depends(require_permission("admin:tests")),
    db: Session = Depends(get_db),
):
    """Delete an E2E test."""
    test = db.query(E2ETest).filter(E2ETest.id == test_id).first()
    if not test:
        raise HTTPException(404, "Test not found")
    db.delete(test)
    db.commit()
    return {"ok": True}


@router.post("/admin/tests/run")
def run_tests(
    body: RunTestsRequest,
    user: User = Depends(require_permission("admin:tests")),
    db: Session = Depends(get_db),
):
    """Create pending test run records. Actual execution happens externally (CI/CD)."""
    if body.test_ids:
        tests = db.query(E2ETest).filter(E2ETest.id.in_(body.test_ids)).all()
    else:
        tests = db.query(E2ETest).all()

    if not tests:
        raise HTTPException(404, "No tests found")

    runs = []
    for t in tests:
        run = E2ETestRun(
            test_id=t.id,
            environment=body.environment,
            status="pending",
            triggered_by="manual",
        )
        db.add(run)
        runs.append(run)

    db.commit()
    return {
        "ok": True,
        "runs": [{"id": r.id, "test_id": r.test_id, "status": r.status} for r in runs],
    }


@router.get("/admin/test-runs")
def list_test_runs(
    environment: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(require_permission("admin:tests")),
    db: Session = Depends(get_db),
):
    """List test runs, optionally filtered by environment."""
    q = db.query(E2ETestRun).order_by(E2ETestRun.started_at.desc())
    if environment:
        q = q.filter(E2ETestRun.environment == environment)
    total = q.count()
    rows = q.offset(offset).limit(limit).all()
    return {
        "total": total,
        "runs": [
            {
                "id": r.id,
                "test_id": r.test_id,
                "environment": r.environment,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "duration_ms": r.duration_ms,
                "error_message": r.error_message,
                "screenshots": r.screenshots_json,
                "video_url": r.video_url,
                "triggered_by": r.triggered_by,
                "git_sha": r.git_sha,
            }
            for r in rows
        ],
    }


@router.get("/admin/test-runs/{run_id}")
def get_test_run(
    run_id: str,
    user: User = Depends(require_permission("admin:tests")),
    db: Session = Depends(get_db),
):
    """Get a single test run detail."""
    run = db.query(E2ETestRun).filter(E2ETestRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Test run not found")
    return {
        "id": run.id,
        "test_id": run.test_id,
        "environment": run.environment,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "duration_ms": run.duration_ms,
        "error_message": run.error_message,
        "screenshots": run.screenshots_json,
        "video_url": run.video_url,
        "triggered_by": run.triggered_by,
        "git_sha": run.git_sha,
    }


@router.post("/admin/test-runs/webhook")
def test_run_webhook(
    payload: TestRunWebhookPayload,
    db: Session = Depends(get_db),
):
    """Receive test run results from CI/CD.

    No auth required — will be secured via webhook secret.
    """
    # Find pending run for this test+environment, or create one
    q = db.query(E2ETestRun).filter(
        E2ETestRun.environment == payload.environment,
        E2ETestRun.status.in_(["pending", "running"]),
    )
    if payload.test_id:
        q = q.filter(E2ETestRun.test_id == payload.test_id)
    run = q.order_by(E2ETestRun.started_at.desc()).first()

    if run:
        run.status = payload.status
        run.duration_ms = payload.duration_ms
        run.error_message = payload.error_message
        run.screenshots_json = payload.screenshots
        run.video_url = payload.video_url
        run.git_sha = payload.git_sha
        if payload.status in ("passed", "failed", "error"):
            run.completed_at = datetime.now(timezone.utc)
    else:
        run = E2ETestRun(
            test_id=payload.test_id,
            environment=payload.environment,
            status=payload.status,
            duration_ms=payload.duration_ms,
            error_message=payload.error_message,
            screenshots_json=payload.screenshots,
            video_url=payload.video_url,
            git_sha=payload.git_sha,
            triggered_by="ci",
        )
        if payload.status in ("passed", "failed", "error"):
            run.completed_at = datetime.now(timezone.utc)
        db.add(run)

    # Update the test's last_run fields
    if payload.test_id:
        test = db.query(E2ETest).filter(E2ETest.id == payload.test_id).first()
        if test:
            test.last_run_status = payload.status
            test.last_run_at = datetime.now(timezone.utc)

    db.commit()
    return {"ok": True}
