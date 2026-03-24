"""Tests for the system configuration sync mechanism."""

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import AgentConfig, AgentConnectorBinding, ConnectorSpec
from app.services.system_config_sync import sync_system_config
from app.system_config import AGENT_BINDINGS, AGENT_CONFIGS, CONNECTOR_SPECS


def test_sync_creates_connector_specs(db_session: Session):
    """First sync creates all connector specs from system_config."""
    sync_system_config(db_session)

    for defn in CONNECTOR_SPECS:
        spec = (
            db_session.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == defn["connector_name"])
            .first()
        )
        assert spec is not None, f"Missing spec: {defn['connector_name']}"
        assert spec.display_name == defn["display_name"]
        assert spec.execution_mode == defn.get("execution_mode", "template")
        assert len(spec.tools) == len(defn["tools"])


def test_sync_creates_agent_configs(db_session: Session):
    """First sync ensures all agent configs exist."""
    sync_system_config(db_session)

    for defn in AGENT_CONFIGS:
        row = (
            db_session.query(AgentConfig)
            .filter(AgentConfig.agent_slug == defn["agent_slug"])
            .first()
        )
        assert row is not None, f"Missing agent: {defn['agent_slug']}"
        assert row.display_name is not None


def test_sync_creates_bindings(db_session: Session):
    """First sync creates all agent-connector bindings."""
    sync_system_config(db_session)

    for defn in AGENT_BINDINGS:
        row = (
            db_session.query(AgentConnectorBinding)
            .filter(
                AgentConnectorBinding.agent_slug == defn["agent_slug"],
                AgentConnectorBinding.connector_name == defn["connector_name"],
            )
            .first()
        )
        assert row is not None, (
            f"Missing binding: {defn['agent_slug']}↔{defn['connector_name']}"
        )
        assert row.enabled is True


def test_sync_idempotent(db_session: Session):
    """Running sync twice produces the same result — no duplicates."""
    sync_system_config(db_session)

    specs_after_first = db_session.query(ConnectorSpec).count()
    bindings_after_first = db_session.query(AgentConnectorBinding).count()

    sync_system_config(db_session)

    assert db_session.query(ConnectorSpec).count() == specs_after_first
    assert db_session.query(AgentConnectorBinding).count() == bindings_after_first

    # System specs are present
    system_names = {d["connector_name"] for d in CONNECTOR_SPECS}
    found = {s.connector_name for s in db_session.query(ConnectorSpec).all()}
    assert system_names.issubset(found)


def test_sync_preserves_admin_customizations(db_session: Session):
    """Sync does not overwrite admin changes to agent configs."""
    sync_system_config(db_session)

    # Simulate admin customizing a prompt
    agent = (
        db_session.query(AgentConfig).filter(AgentConfig.agent_slug == "router").first()
    )
    agent.system_prompt = "Custom admin prompt"
    agent.display_name = "My Custom Router"
    db_session.flush()

    # Re-sync
    sync_system_config(db_session)

    agent = (
        db_session.query(AgentConfig).filter(AgentConfig.agent_slug == "router").first()
    )
    assert agent.system_prompt == "Custom admin prompt"
    assert agent.display_name == "My Custom Router"


def test_sync_preserves_binding_enabled_toggle(db_session: Session):
    """Sync does not overwrite admin's enabled toggle on capabilities."""
    sync_system_config(db_session)

    # Simulate admin disabling a capability
    binding = (
        db_session.query(AgentConnectorBinding)
        .filter(
            AgentConnectorBinding.agent_slug == "reports",
            AgentConnectorBinding.connector_name == "norm_reports",
        )
        .first()
    )
    caps = [dict(c) for c in binding.capabilities]
    caps[0]["enabled"] = False
    binding.capabilities = caps
    flag_modified(binding, "capabilities")
    db_session.flush()

    # Re-sync
    sync_system_config(db_session)

    binding = (
        db_session.query(AgentConnectorBinding)
        .filter(
            AgentConnectorBinding.agent_slug == "reports",
            AgentConnectorBinding.connector_name == "norm_reports",
        )
        .first()
    )
    render_cap = next(c for c in binding.capabilities if c["action"] == "render_chart")
    assert render_cap["enabled"] is False


def test_sync_updates_spec_tools(db_session: Session):
    """When tools change in code, sync updates the spec and bumps version."""
    sync_system_config(db_session)

    spec = (
        db_session.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == "norm")
        .first()
    )
    v1 = spec.version

    # Manually mutate tools to simulate stale DB state
    spec.tools = []
    db_session.flush()

    # Re-sync should restore tools and bump version
    sync_system_config(db_session)

    db_session.refresh(spec)
    assert len(spec.tools) > 0
    assert spec.version > v1
