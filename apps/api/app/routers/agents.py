"""Admin API for agent configuration."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import AgentConfig, AgentConnectorBinding, ConnectorSpec, User
from app.auth.dependencies import get_current_user, require_role
from app.services.agent_config_service import (
    get_system_prompt,
    get_default_prompt,
    update_agent_config,
    reset_prompt,
    get_connector_bindings,
    upsert_connector_binding,
    delete_connector_binding,
    get_all_capabilities_summary,
)

router = APIRouter()

KNOWN_SLUGS = ["procurement", "hr", "reports", "router"]


def _merge_capabilities(binding_caps: list[dict], spec_ops: list[dict]) -> list[dict]:
    """Merge spec operations into binding capabilities.

    - Existing binding caps keep their enabled state
    - New spec ops get added with enabled=False
    - Stale caps (in binding but not in spec) are kept but not lost
    """
    existing = {c["action"]: c for c in binding_caps}
    merged = []
    for op in spec_ops:
        action = op.get("action", "")
        if action in existing:
            merged.append(existing[action])
        else:
            merged.append({
                "action": action,
                "label": op.get("description", action.replace("_", " ").title()),
                "enabled": False,
            })
    # Keep any binding caps whose action isn't in the spec (stale but don't lose them)
    spec_actions = {op.get("action", "") for op in spec_ops}
    for cap in binding_caps:
        if cap["action"] not in spec_actions:
            merged.append(cap)
    return merged


def _agent_to_dict(
    slug: str,
    config: AgentConfig | None,
    bindings: list[dict],
    specs_by_name: dict[str, ConnectorSpec] | None = None,
    include_prompt: bool = True,
) -> dict:
    specs_by_name = specs_by_name or {}
    default_prompt = get_default_prompt(slug)
    prompt = (config.system_prompt if config and config.system_prompt is not None else default_prompt)
    is_custom = config is not None and config.system_prompt is not None

    enriched_bindings = []
    bound_connector_names = set()
    for b in bindings:
        connector_name = b["connector_name"]
        bound_connector_names.add(connector_name)
        spec = specs_by_name.get(connector_name)
        caps = b["capabilities"]
        if spec and spec.operations:
            caps = _merge_capabilities(caps, spec.operations)
        label = spec.display_name if spec else connector_name
        enriched_bindings.append({
            "connector_name": connector_name,
            "connector_label": label,
            "capabilities": caps,
            "enabled": b["enabled"],
        })

    # Build available_connectors: specs matching this agent's category that aren't bound
    agent_category = slug  # agent slug maps to spec category
    available_connectors = []
    for spec in specs_by_name.values():
        if spec.connector_name not in bound_connector_names and spec.category == agent_category:
            available_connectors.append({
                "connector_name": spec.connector_name,
                "display_name": spec.display_name,
            })

    result = {
        "slug": slug,
        "display_name": config.display_name if config else slug.replace("_", " ").title(),
        "description": config.description if config else None,
        "is_custom_prompt": is_custom,
        "enabled": config.enabled if config else True,
        "bindings": enriched_bindings,
        "available_connectors": available_connectors,
    }
    if include_prompt:
        result["system_prompt"] = prompt
    return result


@router.get("/agents")
async def list_agents(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    configs = {r.agent_slug: r for r in db.query(AgentConfig).all()}
    all_bindings = db.query(AgentConnectorBinding).all()
    specs_by_name = {s.connector_name: s for s in db.query(ConnectorSpec).all()}

    bindings_by_slug: dict[str, list[dict]] = {}
    for b in all_bindings:
        bindings_by_slug.setdefault(b.agent_slug, []).append({
            "connector_name": b.connector_name,
            "capabilities": b.capabilities or [],
            "enabled": b.enabled,
        })

    agents = []
    for slug in KNOWN_SLUGS:
        agents.append(_agent_to_dict(slug, configs.get(slug), bindings_by_slug.get(slug, []), specs_by_name))
    return {"agents": agents}


@router.get("/agents/capabilities")
async def capabilities_summary(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return get_all_capabilities_summary(db)


@router.get("/agents/{slug}")
async def get_agent(
    slug: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    if slug not in KNOWN_SLUGS:
        raise HTTPException(404, f"Unknown agent: {slug}")
    config = db.query(AgentConfig).filter(AgentConfig.agent_slug == slug).first()
    bindings = get_connector_bindings(slug, db)
    specs_by_name = {s.connector_name: s for s in db.query(ConnectorSpec).all()}
    return _agent_to_dict(slug, config, bindings, specs_by_name)


class AgentUpdateBody(BaseModel):
    system_prompt: str | None = None
    description: str | None = None
    display_name: str | None = None


@router.put("/agents/{slug}")
async def update_agent(
    slug: str,
    body: AgentUpdateBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    if slug not in KNOWN_SLUGS:
        raise HTTPException(404, f"Unknown agent: {slug}")
    row = update_agent_config(
        slug, db,
        system_prompt=body.system_prompt,
        description=body.description,
        display_name=body.display_name,
    )
    db.commit()
    bindings = get_connector_bindings(slug, db)
    specs_by_name = {s.connector_name: s for s in db.query(ConnectorSpec).all()}
    return _agent_to_dict(slug, row, bindings, specs_by_name)


@router.post("/agents/{slug}/reset-prompt")
async def reset_agent_prompt(
    slug: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    if slug not in KNOWN_SLUGS:
        raise HTTPException(404, f"Unknown agent: {slug}")
    row = reset_prompt(slug, db)
    db.commit()
    bindings = get_connector_bindings(slug, db)
    specs_by_name = {s.connector_name: s for s in db.query(ConnectorSpec).all()}
    return _agent_to_dict(slug, row, bindings, specs_by_name)


@router.get("/agents/{slug}/bindings")
async def list_bindings(
    slug: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    if slug not in KNOWN_SLUGS:
        raise HTTPException(404, f"Unknown agent: {slug}")
    return {"bindings": get_connector_bindings(slug, db)}


class BindingBody(BaseModel):
    capabilities: list[dict] = []
    enabled: bool = True


@router.put("/agents/{slug}/bindings/{connector}")
async def upsert_binding(
    slug: str,
    connector: str,
    body: BindingBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    if slug not in KNOWN_SLUGS:
        raise HTTPException(404, f"Unknown agent: {slug}")
    row = upsert_connector_binding(slug, connector, body.capabilities, body.enabled, db)
    db.commit()
    spec = db.query(ConnectorSpec).filter(ConnectorSpec.connector_name == connector).first()
    caps = row.capabilities or []
    if spec and spec.operations:
        caps = _merge_capabilities(caps, spec.operations)
    return {
        "connector_name": row.connector_name,
        "connector_label": spec.display_name if spec else row.connector_name,
        "capabilities": caps,
        "enabled": row.enabled,
    }


@router.delete("/agents/{slug}/bindings/{connector}")
async def remove_binding(
    slug: str,
    connector: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    if slug not in KNOWN_SLUGS:
        raise HTTPException(404, f"Unknown agent: {slug}")
    deleted = delete_connector_binding(slug, connector, db)
    if not deleted:
        raise HTTPException(404, f"No binding for {slug}/{connector}")
    db.commit()
    return {"deleted": True}
