"""Service layer for agent configuration (prompts + connector bindings)."""

from sqlalchemy.orm import Session

from app.db.models import AgentConfig, AgentConnectionBinding
from app.connectors import spec_rows


def get_system_prompt(agent_slug: str, db: Session) -> str:
    """Return the system prompt stored in the DB for this agent."""
    row = db.query(AgentConfig).filter(AgentConfig.agent_slug == agent_slug).first()
    if row and row.system_prompt is not None:
        return row.system_prompt
    return ""


# App notes are standing rules for one App, added to every conversation while
# that App is on — so they must stay short (validator enforces both caps).
APP_NOTE_MAX_CHARS = 300
APP_NOTES_TOTAL_MAX_CHARS = 1500


def unified_prompt_active(db: Session) -> bool:
    """One Norm prompt instead of per-agent prompts — on once the Norm (base)
    row carries a system prompt. That emptiness is the switch, so the change
    flips in the shared config DB without a deploy, and back by clearing it."""
    row = db.query(AgentConfig).filter(AgentConfig.agent_slug == "base").first()
    return bool(row and (row.system_prompt or "").strip())


def build_unified_prompt(
    domain: str,
    db: Session,
    apps_on: set[str] | None = None,
) -> str:
    """The unified system prompt (Apps v3 prompt consolidation, 28 Sep 2026):

        Norm prompt (the base row: identity, shared rules, calendar, formatting)
      + the member's light personality (tone only)
      + a short note for each App that's ON (standing rules for that App)

    Tool know-how rides on tool descriptions and workflows on skills, so
    neither is repeated here. ``apps_on`` None = every App counts as on
    (gating inactive / no org)."""
    from app.services.entitlements import _catalog

    rows = {r.agent_slug: r for r in db.query(AgentConfig).all()}
    base = rows.get("base")
    parts = [(base.system_prompt or "").strip() if base else ""]

    member = rows.get(domain)
    persona = (getattr(member, "persona", None) or "").strip() if member else ""
    if persona:
        parts.append(f"## This conversation\n{persona}")

    notes = []
    for a in _catalog(db):
        if a.tier not in ("app", "user"):
            continue
        note = ((a.composition or {}).get("note") or "").strip()
        if not note or (apps_on is not None and a.slug not in apps_on):
            continue
        notes.append(f"- **{a.name}**: {note[:APP_NOTE_MAX_CHARS]}")
    if notes:
        block, used = [], 0
        for n in notes:
            if used + len(n) > APP_NOTES_TOTAL_MAX_CHARS:
                break
            block.append(n)
            used += len(n)
        parts.append("## Your Apps\n" + "\n".join(block))
    return "\n\n".join(p for p in parts if p)


def update_agent_config(
    agent_slug: str,
    db: Session,
    system_prompt: str | None = None,
    description: str | None = None,
    display_name: str | None = None,
    persona: str | None = None,
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
        if persona is not None:
            row.persona = persona
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
        db.query(AgentConnectionBinding)
        .filter(AgentConnectionBinding.agent_slug == agent_slug)
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
    needed; this is the one answer (unattended task scope, the admin pages).

    Enabled capabilities on enabled bindings only — a disabled binding is a
    tool the agent cannot reach, and counting it would answer the question
    wrongly in the direction that hurts (claiming reach it hasn't got).
    """
    # Apps v3: a member's reach is the tools of the Apps bound to it (the
    # App Map), not its legacy binding rows.
    from app.services.entitlements import _catalog, app_member

    apps = _catalog(db)
    if any((a.composition or {}).get("tools") for a in apps):
        agent_rows = [a for a in apps if a.tier == "agent"]
        return {
            key.split(".", 1)[-1]
            for a in apps
            if a.tier in ("app", "user") and app_member(a, agent_rows) == agent_slug
            for key in (a.composition or {}).get("tools") or []
        }
    return _binding_actions(agent_slug, db)


def _binding_actions(agent_slug: str, db: Session) -> set[str]:
    """Enabled capabilities on the agent's enabled legacy binding rows."""
    actions: set[str] = set()
    rows = (
        db.query(AgentConnectionBinding)
        .filter(
            AgentConnectionBinding.agent_slug == agent_slug,
            AgentConnectionBinding.enabled == True,  # noqa: E712
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


def default_tool_filter(agent_slug: str, config_db: Session) -> list[str] | None:
    """The toolset an UNATTENDED run falls back to when its task carries no
    explicit tool_filter: the agent's own bound actions (the pre-one-agent
    domain scope).

    Interactive chat gets the full entitled union (a human is present to approve
    any write). An unattended run (a scheduled task, a "Run Now") must NOT —
    several state-changing tools are registered as GET and execute without an
    approval card, so handing an unfiltered scheduled run the whole union would
    let a report task auto-fire a purchase order. Materialising the agent's own
    scope here keeps an unfiltered run exactly as capable as it was before the
    one-agent change. Returns None when the agent has no curated bindings, which
    leaves the union in place — matching the old no-narrowing behaviour.

    A task filed under no team member — created in a Norm thread, once the
    router went (Sep 2026) — gets ``_read_only_scope`` instead.
    """
    # Apps v3: once Apps own tools, an unattended run's scope is the tools of
    # its member's own Apps plus Norm Core's — the same ownership the tool
    # filter uses, so a report task still can't reach create_purchase_order —
    # UNIONED with the member's legacy bindings. Shared reads moved to other
    # members' Apps (get_labour to Reports' Loaded Reports, get_stock to
    # Procurement's Loaded Stock), and a time_attendance or chef task that
    # could read them before must still be able to. Retire the union with the
    # bindings, not before.
    from app.agents.registry import MEMBERS
    from app.services.entitlements import ALL_MEMBERS, _catalog, app_member

    apps = _catalog(config_db)
    if any((a.composition or {}).get("tools") for a in apps):
        if agent_slug not in MEMBERS:
            return _read_only_scope(apps, config_db)
        agent_rows = [a for a in apps if a.tier == "agent"]
        scoped: set[str] = set()
        for a in apps:
            if a.tier not in ("app", "user"):
                continue
            if app_member(a, agent_rows) in (agent_slug, ALL_MEMBERS):
                scoped.update(
                    key.split(".", 1)[-1]
                    for key in (a.composition or {}).get("tools") or []
                )
        scoped |= _binding_actions(agent_slug, config_db)
        return sorted(scoped) if scoped else None
    actions = get_agent_actions(agent_slug, config_db)
    return sorted(actions) if actions else None


def _read_only_scope(apps, config_db: Session) -> list[str]:
    """An unattended run with no member and no tool_filter may READ anything
    an App owns, and use Norm Core (email, charts, memory) — nothing that
    writes. "Reads" means rows marked ``read_only`` explicitly: method alone
    won't do, since create_purchase_order and the invoice-receiving tools are
    registered as GET. A task that needs a write carries a tool_filter, which
    it gets from the conversation that created it. Still intersected with the
    org's entitled union downstream, so it never exceeds what the org holds."""
    from app.db.config_models import ConnectionSpec
    from app.services.entitlements import ALL_MEMBERS, app_member

    agent_rows = [a for a in apps if a.tier == "agent"]
    owned: dict[str, bool] = {}  # "connector.action" -> is Norm Core's
    for a in apps:
        if a.tier not in ("app", "user"):
            continue
        core = app_member(a, agent_rows) == ALL_MEMBERS
        for key in (a.composition or {}).get("tools") or []:
            owned[key] = owned.get(key, False) or core
    read_only: set[str] = set()
    connectors = {k.split(".", 1)[0] for k in owned}
    for spec in config_db.query(ConnectionSpec).filter(
        ConnectionSpec.connector_name.in_(connectors)
    ):
        for t in spec_rows.rows(spec):
            if t.get("read_only") is True and t.get("action"):
                read_only.add(f"{spec.connector_name}.{t['action']}")
    return sorted(
        {k.split(".", 1)[-1] for k, core in owned.items() if core or k in read_only}
    )


def upsert_connector_binding(
    agent_slug: str,
    connector_name: str,
    capabilities: list[dict],
    enabled: bool,
    db: Session,
) -> AgentConnectionBinding:
    """Upsert a connector binding."""
    row = (
        db.query(AgentConnectionBinding)
        .filter(
            AgentConnectionBinding.agent_slug == agent_slug,
            AgentConnectionBinding.connector_name == connector_name,
        )
        .first()
    )
    if row:
        row.capabilities = capabilities
        row.enabled = enabled
    else:
        row = AgentConnectionBinding(
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
        db.query(AgentConnectionBinding)
        .filter(
            AgentConnectionBinding.agent_slug == agent_slug,
            AgentConnectionBinding.connector_name == connector_name,
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
        db.query(AgentConnectionBinding)
        .filter(
            AgentConnectionBinding.enabled == True  # noqa: E712
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
