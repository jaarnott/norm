import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Integer, Text, DateTime, ForeignKey, JSON, Boolean,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=_uuid)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    role = Column(String, nullable=False, default="manager")  # "admin" or "manager"
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    tasks = relationship("Task", back_populates="user")


class Venue(Base):
    __tablename__ = "venues"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    location = Column(String)


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)


class Product(Base):
    __tablename__ = "products"

    id = Column(String, primary_key=True, default=_uuid)
    supplier_id = Column(String, ForeignKey("suppliers.id"))
    name = Column(String, nullable=False)
    category = Column(String)
    unit = Column(String, default="case")
    pack_size = Column(String)

    supplier = relationship("Supplier")
    aliases = relationship("ProductAlias", back_populates="product")


class ProductAlias(Base):
    __tablename__ = "product_aliases"

    id = Column(String, primary_key=True, default=_uuid)
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    alias = Column(String, nullable=False)

    product = relationship("Product", back_populates="aliases")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True, default=_uuid)
    session_id = Column(String, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    intent = Column(String)
    domain = Column(String)
    status = Column(String, nullable=False, default="awaiting_user_input")
    title = Column(String, nullable=True)
    raw_prompt = Column(Text)
    extracted_fields = Column(JSON, default=dict)
    missing_fields = Column(JSON, default=list)
    clarification_question = Column(Text)
    agent_loop_state = Column(JSON, nullable=True)
    pending_tool_call_ids = Column(JSON, nullable=True)
    thinking_steps = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    messages = relationship("Message", back_populates="task", order_by="Message.created_at", cascade="all, delete-orphan")
    order = relationship("Order", back_populates="task", uselist=False, cascade="all, delete-orphan")
    hr_setup = relationship("HrSetup", back_populates="task", uselist=False, cascade="all, delete-orphan")
    approvals = relationship("Approval", back_populates="task", order_by="Approval.performed_at", cascade="all, delete-orphan")
    integration_runs = relationship("IntegrationRun", back_populates="task", order_by="IntegrationRun.created_at", cascade="all, delete-orphan")
    llm_calls = relationship("LlmCall", back_populates="task", order_by="LlmCall.created_at", cascade="all, delete-orphan")
    tool_calls = relationship("ToolCall", back_populates="task", order_by="ToolCall.created_at", cascade="all, delete-orphan")
    working_documents = relationship("WorkingDocument", back_populates="task", cascade="all, delete-orphan")
    user = relationship("User", back_populates="tasks")


class Message(Base):
    __tablename__ = "messages"

    id = Column(String, primary_key=True, default=_uuid)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    role = Column(String, nullable=False)  # "user" or "assistant"
    content = Column(Text, nullable=False)
    display_blocks = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    task = relationship("Task", back_populates="messages")


class Order(Base):
    __tablename__ = "orders"

    id = Column(String, primary_key=True, default=_uuid)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    venue_id = Column(String, ForeignKey("venues.id"))
    supplier_id = Column(String, ForeignKey("suppliers.id"))
    status = Column(String, default="draft")
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    task = relationship("Task", back_populates="order")
    venue = relationship("Venue")
    supplier = relationship("Supplier")
    lines = relationship("OrderLine", back_populates="order")


class OrderLine(Base):
    __tablename__ = "order_lines"

    id = Column(String, primary_key=True, default=_uuid)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False)
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    quantity_cases = Column(Integer)
    quantity_units = Column(Integer)

    order = relationship("Order", back_populates="lines")
    product = relationship("Product")


class Approval(Base):
    __tablename__ = "approvals"

    id = Column(String, primary_key=True, default=_uuid)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    action = Column(String, nullable=False)  # "approved" or "rejected"
    performed_by = Column(String, default="system")
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    performed_at = Column(DateTime(timezone=True), default=_now)
    notes = Column(Text)

    task = relationship("Task", back_populates="approvals")


class IntegrationRun(Base):
    __tablename__ = "integration_runs"

    id = Column(String, primary_key=True, default=_uuid)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    connector_name = Column(String, nullable=False)
    request_payload = Column(JSON)
    response_payload = Column(JSON)
    status = Column(String, nullable=False, default="pending")  # "success", "failed", "pending"
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), default=_now)
    duration_ms = Column(Integer)
    execution_mode = Column(String)       # "template" | "agent" | "legacy"
    rendered_request = Column(JSON)       # {method, url, headers, body}
    spec_version = Column(Integer)        # which version of connector spec was used

    task = relationship("Task", back_populates="integration_runs")


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id = Column(String, primary_key=True, default=_uuid)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=True)
    call_type = Column(String, nullable=False)       # "routing" | "interpretation" | "execution" | "spec_generation"
    model = Column(String, nullable=False)
    system_prompt = Column(Text, nullable=False)
    user_prompt = Column(Text, nullable=False)
    raw_response = Column(Text)
    parsed_response = Column(JSON)
    status = Column(String, nullable=False, default="success")  # "success" | "error"
    error_message = Column(Text)
    duration_ms = Column(Integer)
    tools_provided = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    task = relationship("Task", back_populates="llm_calls")


class ConnectorSpec(Base):
    __tablename__ = "connector_specs"

    id = Column(String, primary_key=True, default=_uuid)
    connector_name = Column(String, unique=True, nullable=False)
    display_name = Column(String, nullable=False)
    category = Column(String)                          # "hr", "procurement"
    execution_mode = Column(String, nullable=False, default="template")  # "template" | "agent"
    auth_type = Column(String, nullable=False)         # "bearer" | "api_key_header" | "basic" | "oauth2"
    auth_config = Column(JSON, nullable=False, default=dict)
    base_url_template = Column(String)                 # Jinja2 template
    tools = Column(JSON, nullable=False, default=list)
    api_documentation = Column(Text)                   # for agent mode
    example_requests = Column(JSON, nullable=False, default=list)
    credential_fields = Column(JSON, nullable=False, default=list)
    oauth_config = Column(JSON, nullable=True)         # {authorize_url, token_url, scopes, client_id, client_secret}
    test_request = Column(JSON, nullable=True)          # {method, path_template, headers, success_status_codes}
    version = Column(Integer, nullable=False, default=1)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class OAuthState(Base):
    """Temporary storage for pending OAuth authorization flows."""
    __tablename__ = "oauth_states"

    id = Column(String, primary_key=True, default=_uuid)
    connector_name = Column(String, nullable=False)
    state = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=_now)


class ConnectorConfig(Base):
    __tablename__ = "connector_configs"

    id = Column(String, primary_key=True, default=_uuid)
    connector_name = Column(String, unique=True, nullable=False)
    config = Column(JSON, nullable=False, default=dict)
    enabled = Column(String, nullable=False, default="true")
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)
    oauth_metadata = Column(JSON, nullable=True)   # extra fields from token response (e.g. venue_id)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class AgentConfig(Base):
    __tablename__ = "agent_configs"

    id = Column(String, primary_key=True, default=_uuid)
    agent_slug = Column(String, unique=True, nullable=False)
    display_name = Column(String, nullable=False)
    system_prompt = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class AgentConnectorBinding(Base):
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


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id = Column(String, primary_key=True, default=_uuid)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    llm_call_id = Column(String, ForeignKey("llm_calls.id", ondelete="SET NULL"), nullable=True)
    iteration = Column(Integer, nullable=False)
    tool_name = Column(String, nullable=False)          # e.g. "bidfood__check_stock"
    connector_name = Column(String, nullable=False)
    action = Column(String, nullable=False)
    method = Column(String, nullable=False)              # GET/POST/PUT/DELETE
    input_params = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="pending")  # "executed", "pending_approval", "approved", "rejected", "failed"
    result_payload = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    rendered_request = Column(JSON, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    task = relationship("Task", back_populates="tool_calls")


class WorkingDocument(Base):
    __tablename__ = "working_documents"

    id = Column(String, primary_key=True, default=_uuid)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False, index=True)
    doc_type = Column(String, nullable=False)          # "roster", "order", etc.
    connector_name = Column(String, nullable=False)
    sync_mode = Column(String, nullable=False, default="auto")  # "auto" | "submit"
    data = Column(JSON, nullable=False, default=dict)
    external_ref = Column(JSON, nullable=True)         # e.g. {"roster_id": "abc", "search_date": "2026-03-09"}
    sync_status = Column(String, nullable=False, default="synced")  # "synced" | "dirty" | "syncing" | "error" | "pending_submit"
    sync_error = Column(Text, nullable=True)
    pending_ops = Column(JSON, nullable=True, default=list)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    task = relationship("Task", back_populates="working_documents")


class HrSetup(Base):
    __tablename__ = "hr_setups"

    id = Column(String, primary_key=True, default=_uuid)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    employee_name = Column(String)
    role = Column(String)
    venue_id = Column(String, ForeignKey("venues.id"))
    start_date = Column(String)
    email = Column(String)
    phone = Column(String)
    employment_type = Column(String)
    status = Column(String, default="draft")

    task = relationship("Task", back_populates="hr_setup")
    venue = relationship("Venue")
