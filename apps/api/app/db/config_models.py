"""System configuration models — shared across all environments.

These models live in a dedicated config database (CONFIG_DATABASE_URL).
When CONFIG_DATABASE_URL is empty, they fall back to the main database.

Tables:
- connector_specs: tool definitions, auth types, OAuth config
- agent_configs: agent prompts, descriptions
- agent_connector_bindings: which agents use which connectors
- system_secrets: system-level secrets (API keys, OAuth credentials)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    Integer,
    Text,
    DateTime,
    JSON,
    Boolean,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ConfigBase(DeclarativeBase):
    """Separate metadata for config tables (may be in a different DB)."""

    pass


class ConnectorSpec(ConfigBase):
    __tablename__ = "connector_specs"

    id = Column(String, primary_key=True, default=_uuid)
    connector_name = Column(String, unique=True, nullable=False)
    display_name = Column(String, nullable=False)
    category = Column(String)
    execution_mode = Column(String, nullable=False, default="template")
    auth_type = Column(String, nullable=False)
    auth_config = Column(JSON, nullable=False, default=dict)
    base_url_template = Column(String)
    tools = Column(JSON, nullable=False, default=list)
    api_documentation = Column(Text)
    example_requests = Column(JSON, nullable=False, default=list)
    credential_fields = Column(JSON, nullable=False, default=list)
    oauth_config = Column(JSON, nullable=True)
    test_request = Column(JSON, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class AgentConfig(ConfigBase):
    __tablename__ = "agent_configs"

    id = Column(String, primary_key=True, default=_uuid)
    agent_slug = Column(String, unique=True, nullable=False)
    display_name = Column(String, nullable=False)
    system_prompt = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class AgentConnectorBinding(ConfigBase):
    __tablename__ = "agent_connector_bindings"
    __table_args__ = (
        UniqueConstraint("agent_slug", "connector_name", name="uq_agent_connector"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    agent_slug = Column(String, nullable=False)
    connector_name = Column(String, nullable=False)
    capabilities = Column(JSON, nullable=False, default=list)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class ComponentApiConfig(ConfigBase):
    __tablename__ = "component_api_configs"
    __table_args__ = (
        UniqueConstraint(
            "component_key",
            "connector_name",
            "action_name",
            name="uq_component_connector_action",
        ),
    )

    id = Column(String, primary_key=True, default=_uuid)
    component_key = Column(String, nullable=False)
    connector_name = Column(String, nullable=False)
    action_name = Column(String, nullable=False)
    display_label = Column(String, nullable=True)
    method = Column(String, nullable=False, default="GET")
    path_template = Column(String, nullable=False)
    request_body_template = Column(Text, nullable=True)
    headers = Column(JSON, nullable=False, default=dict)
    required_fields = Column(JSON, nullable=False, default=list)
    field_descriptions = Column(JSON, nullable=False, default=dict)
    # Outbound field mapping (for write endpoints — maps component fields to API params)
    field_mapping = Column(JSON, nullable=True)  # {"componentField": "apiParam"}
    ref_fields = Column(JSON, nullable=True)  # {"apiParam": "externalRefKey"}
    id_field = Column(String, nullable=True)  # e.g., "shift_id"
    # Inbound field mapping (for load endpoints — maps API response fields to component fields)
    response_field_mapping = Column(
        JSON, nullable=True
    )  # {"apiField": "componentField"}
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class SystemSecret(ConfigBase):
    __tablename__ = "system_secrets"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=False)
    description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
