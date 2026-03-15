"""Service layer for agent configuration (prompts + connector bindings)."""

from sqlalchemy.orm import Session

from app.db.models import AgentConfig, AgentConnectorBinding

# Lazy-loaded defaults to avoid circular imports at module level
_DEFAULTS: dict[str, str] | None = None


def _load_defaults() -> dict[str, str]:
    global _DEFAULTS
    if _DEFAULTS is None:
        from app.agents.procurement.prompt import PROCUREMENT_SYSTEM_PROMPT
        from app.agents.hr.prompt import HR_SYSTEM_PROMPT
        from app.agents.reports.prompt import REPORTS_SYSTEM_PROMPT
        from app.agents.router import ROUTER_PROMPT

        _DEFAULTS = {
            "procurement": PROCUREMENT_SYSTEM_PROMPT,
            "hr": HR_SYSTEM_PROMPT,
            "reports": REPORTS_SYSTEM_PROMPT,
            "router": ROUTER_PROMPT,
        }
    return _DEFAULTS


def get_default_prompt(agent_slug: str) -> str:
    """Return the hardcoded default prompt for an agent slug."""
    defaults = _load_defaults()
    return defaults.get(agent_slug, "")


def get_system_prompt(agent_slug: str, db: Session) -> str:
    """DB lookup with fallback to hardcoded default."""
    row = db.query(AgentConfig).filter(AgentConfig.agent_slug == agent_slug).first()
    if row and row.system_prompt is not None:
        return row.system_prompt
    return get_default_prompt(agent_slug)


def update_agent_config(agent_slug: str, db: Session, system_prompt: str | None = None, description: str | None = None, display_name: str | None = None) -> AgentConfig:
    """Upsert an AgentConfig row."""
    row = db.query(AgentConfig).filter(AgentConfig.agent_slug == agent_slug).first()
    if row:
        if system_prompt is not None:
            row.system_prompt = system_prompt
        if description is not None:
            row.description = description
        if display_name is not None:
            row.display_name = display_name
    else:
        row = AgentConfig(
            agent_slug=agent_slug,
            display_name=display_name or agent_slug.title() + " Agent",
            system_prompt=system_prompt,
            description=description,
        )
        db.add(row)
    db.flush()
    return row


def reset_prompt(agent_slug: str, db: Session) -> AgentConfig | None:
    """Set system_prompt = None (revert to hardcoded default)."""
    row = db.query(AgentConfig).filter(AgentConfig.agent_slug == agent_slug).first()
    if row:
        row.system_prompt = None
        db.flush()
    return row


def get_connector_bindings(agent_slug: str, db: Session) -> list[dict]:
    """Return connector bindings for an agent."""
    rows = db.query(AgentConnectorBinding).filter(
        AgentConnectorBinding.agent_slug == agent_slug
    ).all()
    return [
        {
            "connector_name": r.connector_name,
            "capabilities": r.capabilities or [],
            "enabled": r.enabled,
        }
        for r in rows
    ]


def upsert_connector_binding(agent_slug: str, connector_name: str, capabilities: list[dict], enabled: bool, db: Session) -> AgentConnectorBinding:
    """Upsert a connector binding."""
    row = db.query(AgentConnectorBinding).filter(
        AgentConnectorBinding.agent_slug == agent_slug,
        AgentConnectorBinding.connector_name == connector_name,
    ).first()
    if row:
        row.capabilities = capabilities
        row.enabled = enabled
    else:
        row = AgentConnectorBinding(
            agent_slug=agent_slug,
            connector_name=connector_name,
            capabilities=capabilities,
            enabled=enabled,
        )
        db.add(row)
    db.flush()
    return row


def delete_connector_binding(agent_slug: str, connector_name: str, db: Session) -> bool:
    """Remove a connector binding. Returns True if deleted."""
    row = db.query(AgentConnectorBinding).filter(
        AgentConnectorBinding.agent_slug == agent_slug,
        AgentConnectorBinding.connector_name == connector_name,
    ).first()
    if not row:
        return False
    db.delete(row)
    db.flush()
    return True


def get_all_capabilities_summary(db: Session) -> dict:
    """Returns {slug: {description, capabilities: [...]}} for all agents."""
    configs = {r.agent_slug: r for r in db.query(AgentConfig).all()}
    bindings = db.query(AgentConnectorBinding).filter(
        AgentConnectorBinding.enabled == True  # noqa: E712
    ).all()

    # Group bindings by agent_slug
    bindings_by_slug: dict[str, list] = {}
    for b in bindings:
        bindings_by_slug.setdefault(b.agent_slug, []).append(b)

    result = {}
    for slug in set(list(configs.keys()) + list(bindings_by_slug.keys())):
        cfg = configs.get(slug)
        caps = []
        for b in bindings_by_slug.get(slug, []):
            for cap in (b.capabilities or []):
                if cap.get("enabled", True):
                    caps.append({
                        "action": cap.get("action", ""),
                        "label": cap.get("label", cap.get("action", "")),
                        "connector": b.connector_name,
                    })
        result[slug] = {
            "description": cfg.description if cfg else slug,
            "display_name": cfg.display_name if cfg else slug.title(),
            "enabled": cfg.enabled if cfg else True,
            "capabilities": caps,
        }
    return result
