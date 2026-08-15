"""Service layer for agent configuration (prompts + connector bindings)."""

from sqlalchemy.orm import Session

from app.db.models import AgentConfig, AgentConnectorBinding


def get_system_prompt(agent_slug: str, db: Session) -> str:
    """Return the system prompt stored in the DB for this agent."""
    row = db.query(AgentConfig).filter(AgentConfig.agent_slug == agent_slug).first()
    if row and row.system_prompt is not None:
        return row.system_prompt
    return ""


def update_agent_config(
    agent_slug: str,
    db: Session,
    system_prompt: str | None = None,
    description: str | None = None,
    display_name: str | None = None,
) -> AgentConfig:
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
    """Clear the custom system prompt for this agent."""
    row = db.query(AgentConfig).filter(AgentConfig.agent_slug == agent_slug).first()
    if row:
        row.system_prompt = None
        db.flush()
    return row


def get_connector_bindings(agent_slug: str, db: Session) -> list[dict]:
    """Return connector bindings for an agent."""
    rows = (
        db.query(AgentConnectorBinding)
        .filter(AgentConnectorBinding.agent_slug == agent_slug)
        .all()
    )
    return [
        {
            "connector_name": r.connector_name,
            "capabilities": r.capabilities or [],
            "enabled": r.enabled,
        }
        for r in rows
    ]


def get_agent_actions(agent_slug: str, db: Session) -> set[str]:
    """The connector actions THIS agent can actually call.

    "What can this agent do" was being re-derived inline wherever it was
    needed, and the router — the one place that decides who handles a
    message — had no way to ask at all. It was given bare domain slugs, so on
    15 Aug 2026 it sent a wage-cost question to `hr` (recruitment) while
    `time_attendance` held `get_timeclock_entries_for_period`. See
    `describe_domains`.

    Enabled capabilities on enabled bindings only — a disabled binding is a
    tool the agent cannot reach, and counting it would answer the question
    wrongly in the direction that hurts (claiming reach it hasn't got).
    """
    actions: set[str] = set()
    rows = (
        db.query(AgentConnectorBinding)
        .filter(
            AgentConnectorBinding.agent_slug == agent_slug,
            AgentConnectorBinding.enabled == True,  # noqa: E712
        )
        .all()
    )
    for row in rows:
        for cap in row.capabilities or []:
            if isinstance(cap, str):
                actions.add(cap)
            elif cap.get("enabled", True) and cap.get("action"):
                actions.add(cap["action"])
    return actions


def describe_domains(slugs: list[str], db: Session | None) -> str:
    """The routing menu: one line per agent, saying what it actually does.

    The router used to be handed `- procurement\\n- hr\\n- reports…` and asked
    to pick. From a bare list "wage costs" reads as `hr`, which in Norm is
    BambooHR recruitment — no hours, no pay, and (then) no way to hand off. The
    descriptions that make the choice obvious already existed in
    `agent_configs.description`; nobody was showing them to the router.

    Falls back to the bare slug per-agent, so an agent registered in code with
    no config row is still routable — and the whole list degrades to the old
    behaviour when there is no config DB (the prompt-rendering test helper).
    """
    if db is None:
        return "\n".join(f"- {s}" for s in slugs)
    rows = {r.agent_slug: r for r in db.query(AgentConfig).all()}
    lines = []
    for slug in slugs:
        row = rows.get(slug)
        desc = (row.description or "").strip() if row else ""
        lines.append(f"- {slug}: {desc}" if desc else f"- {slug}")
    return "\n".join(lines)


def upsert_connector_binding(
    agent_slug: str,
    connector_name: str,
    capabilities: list[dict],
    enabled: bool,
    db: Session,
) -> AgentConnectorBinding:
    """Upsert a connector binding."""
    row = (
        db.query(AgentConnectorBinding)
        .filter(
            AgentConnectorBinding.agent_slug == agent_slug,
            AgentConnectorBinding.connector_name == connector_name,
        )
        .first()
    )
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
    row = (
        db.query(AgentConnectorBinding)
        .filter(
            AgentConnectorBinding.agent_slug == agent_slug,
            AgentConnectorBinding.connector_name == connector_name,
        )
        .first()
    )
    if not row:
        return False
    db.delete(row)
    db.flush()
    return True


def get_all_capabilities_summary(db: Session) -> dict:
    """Returns {slug: {description, capabilities: [...]}} for all agents."""
    configs = {r.agent_slug: r for r in db.query(AgentConfig).all()}
    bindings = (
        db.query(AgentConnectorBinding)
        .filter(
            AgentConnectorBinding.enabled == True  # noqa: E712
        )
        .all()
    )

    # Group bindings by agent_slug
    bindings_by_slug: dict[str, list] = {}
    for b in bindings:
        bindings_by_slug.setdefault(b.agent_slug, []).append(b)

    result = {}
    for slug in set(list(configs.keys()) + list(bindings_by_slug.keys())):
        cfg = configs.get(slug)
        caps = []
        for b in bindings_by_slug.get(slug, []):
            for cap in b.capabilities or []:
                if cap.get("enabled", True):
                    caps.append(
                        {
                            "action": cap.get("action", ""),
                            "label": cap.get("label", cap.get("action", "")),
                            "connector": b.connector_name,
                        }
                    )
        result[slug] = {
            "description": cfg.description if cfg else slug,
            "display_name": cfg.display_name if cfg else slug.title(),
            "enabled": cfg.enabled if cfg else True,
            "capabilities": caps,
        }
    return result
