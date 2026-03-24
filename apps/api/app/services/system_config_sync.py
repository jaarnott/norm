"""Sync declarative system configuration to the database.

Called on every app startup (i.e. every deploy) to ensure connector specs,
agent configs, and bindings match the definitions in system_config.py.

Sync rules:
- ConnectorSpec:  upsert by connector_name.  Tools and metadata are always
  overwritten from code.  The ``version`` column is bumped on changes.
- AgentConfig:    upsert by agent_slug.  display_name and description are
  set only when the row is first created; admin customizations (including
  system_prompt) are never overwritten.
- AgentConnectorBinding: upsert by (agent_slug, connector_name).
  Capabilities are merged: new actions from code are added, removed actions
  are dropped, but the admin ``enabled`` toggle on each capability is
  preserved.  The binding-level ``enabled`` flag is also preserved.
"""

import logging

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import AgentConfig, AgentConnectorBinding, ConnectorSpec
from app.system_config import AGENT_BINDINGS, AGENT_CONFIGS, CONNECTOR_SPECS

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sync_system_config(db: Session) -> None:
    """Run all system-config sync steps inside the given session."""
    _sync_connector_specs(db)
    _sync_agent_configs(db)
    _sync_agent_bindings(db)
    db.commit()
    log.info("System configuration sync complete")


# ---------------------------------------------------------------------------
# Connector Specs
# ---------------------------------------------------------------------------


def _sync_connector_specs(db: Session) -> None:
    for defn in CONNECTOR_SPECS:
        name = defn["connector_name"]
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == name)
            .first()
        )

        if spec is None:
            spec = ConnectorSpec(
                connector_name=name,
                display_name=defn["display_name"],
                category=defn.get("category"),
                execution_mode=defn.get("execution_mode", "template"),
                auth_type=defn.get("auth_type", "none"),
                tools=defn.get("tools", []),
            )
            db.add(spec)
            log.info("Created system ConnectorSpec: %s", name)
        else:
            # Always overwrite tools and metadata from code
            changed = False
            for field in (
                "display_name",
                "category",
                "execution_mode",
                "auth_type",
            ):
                code_val = defn.get(field)
                if code_val is not None and getattr(spec, field) != code_val:
                    setattr(spec, field, code_val)
                    changed = True

            code_tools = defn.get("tools", [])
            if spec.tools != code_tools:
                spec.tools = code_tools
                flag_modified(spec, "tools")
                changed = True

            if changed:
                spec.version = (spec.version or 1) + 1
                log.info("Updated system ConnectorSpec: %s (v%s)", name, spec.version)


# ---------------------------------------------------------------------------
# Agent Configs
# ---------------------------------------------------------------------------


def _sync_agent_configs(db: Session) -> None:
    for defn in AGENT_CONFIGS:
        slug = defn["agent_slug"]
        row = (
            db.query(AgentConfig)
            .filter(AgentConfig.agent_slug == slug)
            .first()
        )

        if row is None:
            row = AgentConfig(
                agent_slug=slug,
                display_name=defn["display_name"],
                description=defn.get("description"),
            )
            db.add(row)
            log.info("Created system AgentConfig: %s", slug)
        # Existing rows are left untouched — admin may have customized them


# ---------------------------------------------------------------------------
# Agent ↔ Connector Bindings
# ---------------------------------------------------------------------------


def _merge_binding_capabilities(
    code_caps: list[dict],
    existing_caps: list[dict],
) -> list[dict]:
    """Merge code-defined capabilities with existing DB state.

    - Actions in code but not in DB → added with the code-defined enabled flag
    - Actions in both → keep existing enabled flag, update label from code
    - Actions in DB but not in code → dropped (stale)
    """
    existing_by_action = {c["action"]: c for c in existing_caps}
    merged = []
    for cap in code_caps:
        action = cap["action"]
        if action in existing_by_action:
            merged.append(
                {
                    "action": action,
                    "label": cap.get("label", action),
                    "enabled": existing_by_action[action].get("enabled", True),
                }
            )
        else:
            merged.append(
                {
                    "action": action,
                    "label": cap.get("label", action),
                    "enabled": cap.get("enabled", False),
                }
            )
    return merged


def _sync_agent_bindings(db: Session) -> None:
    for defn in AGENT_BINDINGS:
        slug = defn["agent_slug"]
        connector = defn["connector_name"]
        code_caps = defn.get("capabilities", [])

        row = (
            db.query(AgentConnectorBinding)
            .filter(
                AgentConnectorBinding.agent_slug == slug,
                AgentConnectorBinding.connector_name == connector,
            )
            .first()
        )

        if row is None:
            row = AgentConnectorBinding(
                agent_slug=slug,
                connector_name=connector,
                enabled=True,
                capabilities=code_caps,
            )
            db.add(row)
            log.info("Created binding: %s ↔ %s", slug, connector)
        else:
            merged = _merge_binding_capabilities(code_caps, row.capabilities or [])
            if merged != row.capabilities:
                row.capabilities = merged
                flag_modified(row, "capabilities")
                log.info("Updated binding capabilities: %s ↔ %s", slug, connector)
