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
            db.query(ConnectorSpec).filter(ConnectorSpec.connector_name == name).first()
        )

        if spec is None:
            spec = ConnectorSpec(
                connector_name=name,
                display_name=defn["display_name"],
                category=defn.get("category"),
                execution_mode=defn.get("execution_mode", "template"),
                auth_type=defn.get("auth_type", "none"),
                base_url_template=defn.get("base_url_template"),
                auth_config=defn.get("auth_config", {}),
                credential_fields=defn.get("credential_fields", []),
                oauth_config=defn.get("oauth_config"),
                test_request=defn.get("test_request"),
                tools=defn.get("tools", []),
            )
            db.add(spec)
            log.info("Created system ConnectorSpec: %s", name)
        else:
            # Always overwrite tools and metadata from code
            changed = False

            # Scalar fields
            for field in (
                "display_name",
                "category",
                "execution_mode",
                "auth_type",
                "base_url_template",
            ):
                code_val = defn.get(field)
                if code_val is not None and getattr(spec, field) != code_val:
                    setattr(spec, field, code_val)
                    changed = True

            # JSON fields (need flag_modified for mutation tracking)
            for json_field in (
                "tools",
                "auth_config",
                "credential_fields",
                "oauth_config",
                "test_request",
            ):
                code_val = defn.get(json_field)
                if code_val is None:
                    # Allow explicitly setting to None for nullable fields
                    if json_field in defn and getattr(spec, json_field) is not None:
                        setattr(spec, json_field, None)
                        flag_modified(spec, json_field)
                        changed = True
                elif getattr(spec, json_field) != code_val:
                    setattr(spec, json_field, code_val)
                    flag_modified(spec, json_field)
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
        row = db.query(AgentConfig).filter(AgentConfig.agent_slug == slug).first()

        if row is None:
            row = AgentConfig(
                agent_slug=slug,
                display_name=defn["display_name"],
                description=defn.get("description"),
                system_prompt=defn.get("system_prompt", ""),
            )
            db.add(row)
            log.info("Created system AgentConfig: %s", slug)
        else:
            # Fill in system_prompt if the admin hasn't set one yet.
            # Once an admin writes a custom prompt, it's never overwritten.
            if not row.system_prompt and defn.get("system_prompt"):
                row.system_prompt = defn["system_prompt"]
                log.info("Set default system_prompt for: %s", slug)
            # Update display_name and description if still at defaults
            if defn.get("display_name") and not row.display_name:
                row.display_name = defn["display_name"]
            if defn.get("description") and not row.description:
                row.description = defn["description"]


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
