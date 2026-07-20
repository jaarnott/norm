import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    String,
    Integer,
    Text,
    DateTime,
    ForeignKey,
    JSON,
    Boolean,
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
    role = Column(String, nullable=False, default="user")  # "admin" or "user"
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    dashboard_preferences = Column(JSON, nullable=True)  # {"hr": "report-id", ...}
    # Per-workflow run mode, keyed by consolidator action name, e.g.
    # {"review_and_receive_invoices": "autopilot"}. See services/workflow_modes.
    workflow_modes = Column(JSON, nullable=True)

    threads = relationship("Thread", back_populates="user")
    memberships = relationship("OrganizationMembership", back_populates="user")


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False)
    billing_email = Column(String, nullable=True)
    plan = Column(String, nullable=False, default="starter")  # starter|pro|enterprise
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    hr_agent_enabled = Column(Boolean, nullable=False, default=False)
    procurement_agent_enabled = Column(Boolean, nullable=False, default=False)
    reports_agent_enabled = Column(Boolean, nullable=False, default=True)

    venues = relationship("Venue", back_populates="organization")
    memberships = relationship("OrganizationMembership", back_populates="organization")
    roles = relationship("Role", back_populates="organization")


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_org_role_name"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(
        String, ForeignKey("organizations.id"), nullable=True
    )  # NULL = system default role
    name = Column(String, nullable=False)
    display_name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    is_system = Column(Boolean, nullable=False, default=False)
    permissions = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    organization = relationship("Organization", back_populates="roles")
    memberships = relationship("OrganizationMembership", back_populates="role_obj")


class OrganizationMembership(Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", name="uq_user_org"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    role = Column(
        String, nullable=False, default="member"
    )  # legacy: owner|admin|member
    role_id = Column(String, ForeignKey("roles.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    user = relationship("User", back_populates="memberships")
    organization = relationship("Organization", back_populates="memberships")
    role_obj = relationship("Role", back_populates="memberships")


class UserVenueAccess(Base):
    __tablename__ = "user_venue_access"
    __table_args__ = (UniqueConstraint("user_id", "venue_id", name="uq_user_venue"),)

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    venue_id = Column(String, ForeignKey("venues.id"), nullable=False)
    granted_at = Column(DateTime(timezone=True), default=_now)

    user = relationship("User")
    venue = relationship("Venue")


class Venue(Base):
    __tablename__ = "venues"

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=True)
    name = Column(String, nullable=False)
    location = Column(String)
    timezone = Column(String, nullable=True)  # IANA timezone e.g. "Pacific/Auckland"
    day_start_time = Column(String, nullable=True)  # HH:MM e.g. "07:00"

    organization = relationship("Organization", back_populates="venues")


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


class Thread(Base):
    __tablename__ = "threads"

    id = Column(String, primary_key=True, default=_uuid)
    session_id = Column(String, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    venue_id = Column(String, ForeignKey("venues.id"), nullable=True)
    intent = Column(String)
    domain = Column(String)
    status = Column(String, nullable=False, default="awaiting_user_input")
    tags = Column(JSON, nullable=False, default=list)
    title = Column(String, nullable=True)
    raw_prompt = Column(Text)
    extracted_fields = Column(JSON, default=dict)
    missing_fields = Column(JSON, default=list)
    clarification_question = Column(Text)
    agent_loop_state = Column(JSON, nullable=True)
    pending_tool_call_ids = Column(JSON, nullable=True)
    thinking_steps = Column(JSON, nullable=True)
    conversation_summary = Column(Text, nullable=True)
    summary_through_count = Column(Integer, nullable=True)
    playbook_id = Column(String, nullable=True)
    # Delegation: set when this thread is a sub-run created by another agent's
    # delegate_to_agent call. Deliberately NOT a ForeignKey — the parent row is
    # still uncommitted when the child is created inside the parent's turn.
    # Depth is derived server-side by walking this chain, never taken from
    # model-supplied params, so an agent can't talk its way past the cap.
    parent_thread_id = Column(String, nullable=True, index=True)
    delegation_depth = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    messages = relationship(
        "Message",
        back_populates="thread",
        order_by="Message.created_at",
        cascade="all, delete-orphan",
    )
    order = relationship(
        "Order", back_populates="thread", uselist=False, cascade="all, delete-orphan"
    )
    hr_setup = relationship(
        "HrSetup", back_populates="thread", uselist=False, cascade="all, delete-orphan"
    )
    approvals = relationship(
        "Approval",
        back_populates="thread",
        order_by="Approval.performed_at",
        cascade="all, delete-orphan",
    )
    integration_runs = relationship(
        "IntegrationRun",
        back_populates="thread",
        order_by="IntegrationRun.created_at",
        cascade="all, delete-orphan",
    )
    llm_calls = relationship(
        "LlmCall",
        back_populates="thread",
        order_by="LlmCall.created_at",
        cascade="all, delete-orphan",
    )
    tool_calls = relationship(
        "ToolCall",
        back_populates="thread",
        order_by="ToolCall.created_at",
        cascade="all, delete-orphan",
    )
    working_documents = relationship(
        "WorkingDocument", back_populates="thread", cascade="all, delete-orphan"
    )
    user = relationship("User", back_populates="threads")

    # ── Tag helpers ──────────────────────────────────────────────
    def add_tag(self, tag: str) -> None:
        """Add a tag if not already present."""
        current = list(self.tags or [])
        if tag not in current:
            current.append(tag)
            self.tags = current

    def remove_tag(self, tag: str) -> None:
        """Remove a tag if present."""
        self.tags = [t for t in (self.tags or []) if t != tag]

    def has_tag(self, tag: str) -> bool:
        """Check if a tag is present."""
        return tag in (self.tags or [])


class Message(Base):
    __tablename__ = "messages"

    id = Column(String, primary_key=True, default=_uuid)
    thread_id = Column(String, ForeignKey("threads.id"), nullable=False)
    role = Column(String, nullable=False)  # "user" or "assistant"
    content = Column(Text, nullable=False)
    display_blocks = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    thread = relationship("Thread", back_populates="messages")


class Order(Base):
    __tablename__ = "orders"

    id = Column(String, primary_key=True, default=_uuid)
    thread_id = Column(String, ForeignKey("threads.id"), nullable=False)
    venue_id = Column(String, ForeignKey("venues.id"))
    supplier_id = Column(String, ForeignKey("suppliers.id"))
    status = Column(String, default="draft")
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    thread = relationship("Thread", back_populates="order")
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
    thread_id = Column(String, ForeignKey("threads.id"), nullable=False)
    action = Column(String, nullable=False)  # "approved" or "rejected"
    performed_by = Column(String, default="system")
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    performed_at = Column(DateTime(timezone=True), default=_now)
    notes = Column(Text)

    thread = relationship("Thread", back_populates="approvals")


class IntegrationRun(Base):
    __tablename__ = "integration_runs"

    id = Column(String, primary_key=True, default=_uuid)
    thread_id = Column(String, ForeignKey("threads.id"), nullable=False)
    connector_name = Column(String, nullable=False)
    request_payload = Column(JSON)
    response_payload = Column(JSON)
    status = Column(
        String, nullable=False, default="pending"
    )  # "success", "failed", "pending"
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), default=_now)
    duration_ms = Column(Integer)
    execution_mode = Column(String)  # "template" | "agent" | "legacy"
    rendered_request = Column(JSON)  # {method, url, headers, body}
    spec_version = Column(Integer)  # which version of connector spec was used

    thread = relationship("Thread", back_populates="integration_runs")


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id = Column(String, primary_key=True, default=_uuid)
    thread_id = Column(String, ForeignKey("threads.id"), nullable=True)
    call_type = Column(
        String, nullable=False
    )  # "routing" | "interpretation" | "execution" | "spec_generation"
    model = Column(String, nullable=False)
    system_prompt = Column(Text, nullable=False)
    user_prompt = Column(Text, nullable=False)
    raw_response = Column(Text)
    parsed_response = Column(JSON)
    status = Column(String, nullable=False, default="success")  # "success" | "error"
    error_message = Column(Text)
    duration_ms = Column(Integer)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    tools_provided = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    thread = relationship("Thread", back_populates="llm_calls")


# Config models live in config_models.py (may be in a separate DB).
# Re-exported here for backwards compatibility.
from app.db.config_models import (  # noqa: F401, E402
    ConnectorSpec,
    AgentConfig,
    AgentConnectorBinding,
    McpCapability,
    SystemSecret,
)

# NOTE: the MCP OAuth/audit models (app/db/mcp_models.py) are NOT re-exported
# here. They import Base from this module, so re-importing them at the bottom
# would create a circular import whenever mcp_models is imported first. They
# are instead imported explicitly where Base.metadata must include them:
# alembic/env.py (migrations), tests/conftest.py (create_all), and app.main
# (startup). Import them directly: `from app.db.mcp_models import McpToken`.


class OAuthState(Base):
    """Temporary storage for pending OAuth authorization flows."""

    __tablename__ = "oauth_states"

    id = Column(String, primary_key=True, default=_uuid)
    connector_name = Column(String, nullable=False)
    venue_id = Column(String, ForeignKey("venues.id"), nullable=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    state = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=_now)


class ConnectorConfig(Base):
    __tablename__ = "connector_configs"
    __table_args__ = (
        UniqueConstraint("connector_name", "venue_id", name="uq_connector_venue"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    connector_name = Column(String, nullable=False)
    venue_id = Column(
        String, ForeignKey("venues.id"), nullable=True
    )  # NULL for platform connectors (e.g., Anthropic)
    user_id = Column(
        String, ForeignKey("users.id"), nullable=True
    )  # for per-user OAuth (email connectors)
    config = Column(JSON, nullable=False, default=dict)
    enabled = Column(String, nullable=False, default="true")
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)
    oauth_metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    venue = relationship("Venue")
    user = relationship("User")


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id = Column(String, primary_key=True, default=_uuid)
    thread_id = Column(String, ForeignKey("threads.id"), nullable=False)
    llm_call_id = Column(
        String, ForeignKey("llm_calls.id", ondelete="SET NULL"), nullable=True
    )
    venue_id = Column(String, ForeignKey("venues.id"), nullable=True)
    iteration = Column(Integer, nullable=False)
    tool_name = Column(String, nullable=False)  # e.g. "bidfood__check_stock"
    connector_name = Column(String, nullable=False)
    action = Column(String, nullable=False)
    method = Column(String, nullable=False)  # GET/POST/PUT/DELETE
    input_params = Column(JSON, nullable=True)
    status = Column(
        String, nullable=False, default="pending"
    )  # "executed", "pending_approval", "approved", "rejected", "failed"
    result_payload = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    rendered_request = Column(JSON, nullable=True)
    slimmed_content = Column(
        Text, nullable=True
    )  # What the LLM actually saw (after slim/truncation)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    thread = relationship("Thread", back_populates="tool_calls")


class WorkingDocument(Base):
    __tablename__ = "working_documents"

    id = Column(String, primary_key=True, default=_uuid)
    thread_id = Column(String, ForeignKey("threads.id"), nullable=True, index=True)
    venue_id = Column(String, ForeignKey("venues.id"), nullable=True)
    doc_type = Column(String, nullable=False)  # "roster", "order", etc.
    connector_name = Column(String, nullable=False)
    sync_mode = Column(String, nullable=False, default="auto")  # "auto" | "submit"
    data = Column(JSON, nullable=False, default=dict)
    external_ref = Column(
        JSON, nullable=True
    )  # e.g. {"roster_id": "abc", "search_date": "2026-03-09"}
    sync_status = Column(
        String, nullable=False, default="synced"
    )  # "synced" | "dirty" | "syncing" | "error" | "pending_submit"
    sync_error = Column(Text, nullable=True)
    pending_ops = Column(JSON, nullable=True, default=list)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    thread = relationship("Thread", back_populates="working_documents")


class DocumentExtraction(Base):
    """Cache of LLM extractions from binary documents (e.g. invoice PDFs).

    Extracting a supplier invoice copy is the expensive step of the receiving
    review — an LLM call per invoice, per run. The source document is immutable
    (a file is uploaded once under a stable id), so the extracted fields can be
    cached indefinitely and reused across runs instead of re-extracting every
    time. Keyed by a hash of (connector, action, api_params, schema,
    instructions) so the same file extracted with a different schema is a
    distinct entry.
    """

    __tablename__ = "document_extractions"

    id = Column(String, primary_key=True, default=_uuid)
    cache_key = Column(String, nullable=False, unique=True, index=True)
    connector = Column(String, nullable=False)
    action = Column(String, nullable=False)
    data = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now)


class HrSetup(Base):
    __tablename__ = "hr_setups"

    id = Column(String, primary_key=True, default=_uuid)
    thread_id = Column(String, ForeignKey("threads.id"), nullable=False)
    employee_name = Column(String)
    role = Column(String)
    venue_id = Column(String, ForeignKey("venues.id"))
    start_date = Column(String)
    email = Column(String)
    phone = Column(String)
    employment_type = Column(String)
    status = Column(String, default="draft")

    thread = relationship("Thread", back_populates="hr_setup")
    venue = relationship("Venue")


class HiringCriteria(Base):
    __tablename__ = "hiring_criteria"

    id = Column(String, primary_key=True, default=_uuid)
    venue_id = Column(String, ForeignKey("venues.id"), nullable=True)
    scope = Column(String, nullable=False)  # "company" | "position"
    position_name = Column(String, nullable=True)  # null for company-level
    criteria = Column(
        JSON, nullable=False, default=list
    )  # [{id, text, required, category}]
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=_uuid)
    venue_id = Column(String, ForeignKey("venues.id"), nullable=True)
    title = Column(String, nullable=False)
    department = Column(String, nullable=True)
    status = Column(
        String, nullable=False, default="open"
    )  # "draft" | "open" | "closed"
    description = Column(Text, nullable=True)
    criteria_id = Column(String, ForeignKey("hiring_criteria.id"), nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    criteria = relationship("HiringCriteria")
    applications = relationship(
        "Application", back_populates="job", cascade="all, delete-orphan"
    )


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    source = Column(String, nullable=True)  # "referral" | "seek" | "walk-in" | etc
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    applications = relationship(
        "Application", back_populates="candidate", cascade="all, delete-orphan"
    )


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("job_id", "candidate_id", name="uq_job_candidate"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    job_id = Column(String, ForeignKey("jobs.id"), nullable=False)
    candidate_id = Column(String, ForeignKey("candidates.id"), nullable=False)
    status = Column(
        String, nullable=False, default="applied"
    )  # applied|screening|interview|offer|hired|rejected
    score = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    applied_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    job = relationship("Job", back_populates="applications")
    candidate = relationship("Candidate", back_populates="applications")


class AutomatedTask(Base):
    __tablename__ = "automated_tasks"

    id = Column(String, primary_key=True, default=_uuid)
    venue_id = Column(String, ForeignKey("venues.id"), nullable=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    agent_slug = Column(String, nullable=False)
    prompt = Column(Text, nullable=False)
    schedule_type = Column(
        String, nullable=False, default="manual"
    )  # manual|hourly|daily|weekly|monthly
    schedule_config = Column(
        JSON, nullable=False, default=dict
    )  # {hour, minute, day_of_week, day_of_month}
    status = Column(String, nullable=False, default="draft")  # active|paused|draft
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    # Structured context for scheduled runs
    task_config = Column(
        JSON, nullable=False, default=dict
    )  # persistent instructions from conversation
    thread_summary = Column(Text, nullable=True)  # rolling summary of key decisions
    overrides_next_run = Column(
        JSON, nullable=True
    )  # one-off instructions, cleared after execution
    tool_filter = Column(
        JSON, nullable=True
    )  # list of action names to include, or null for all
    conversation_thread_id = Column(
        String, ForeignKey("threads.id"), nullable=True
    )  # persistent conversation

    runs = relationship(
        "AutomatedTaskRun",
        back_populates="automated_task",
        cascade="all, delete-orphan",
        order_by="AutomatedTaskRun.started_at.desc()",
    )
    creator = relationship("User")
    conversation_thread = relationship("Thread", foreign_keys=[conversation_thread_id])


class AutomatedTaskRun(Base):
    __tablename__ = "automated_task_runs"

    id = Column(String, primary_key=True, default=_uuid)
    automated_task_id = Column(String, ForeignKey("automated_tasks.id"), nullable=False)
    thread_id = Column(
        String, ForeignKey("threads.id"), nullable=True
    )  # execution Thread record
    status = Column(String, nullable=False, default="running")  # running|success|error
    mode = Column(String, nullable=False, default="live")  # live|test
    result_summary = Column(Text, nullable=True)
    tool_calls_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), default=_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)

    automated_task = relationship("AutomatedTask", back_populates="runs")
    thread = relationship("Thread", foreign_keys=[thread_id])


class TokenUsage(Base):
    __tablename__ = "token_usage"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", "date", name="uq_org_user_date"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    date = Column(String, nullable=False)  # YYYY-MM-DD
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    llm_call_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(
        String, ForeignKey("organizations.id"), nullable=False, unique=True
    )
    stripe_customer_id = Column(String, nullable=True, unique=True)
    stripe_subscription_id = Column(String, nullable=True, unique=True)
    token_plan = Column(String, nullable=False, default="basic")  # basic|standard|max
    token_quota = Column(Integer, nullable=False, default=1_000_000)
    billing_cycle_start = Column(DateTime(timezone=True), nullable=True)
    status = Column(
        String, nullable=False, default="trialing"
    )  # active|past_due|canceled|trialing
    payment_method_last4 = Column(String, nullable=True)
    payment_method_brand = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    organization = relationship("Organization", backref="subscription")


class TokenTopUp(Base):
    __tablename__ = "token_top_ups"

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    tokens = Column(Integer, nullable=False)
    amount_cents = Column(Integer, nullable=False)
    stripe_payment_intent_id = Column(String, nullable=True)
    status = Column(
        String, nullable=False, default="pending"
    )  # pending|completed|failed
    purchased_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    organization = relationship("Organization")


class BillingEvent(Base):
    __tablename__ = "billing_events"

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    event_type = Column(
        String, nullable=False
    )  # subscription_created|payment_succeeded|payment_failed|plan_changed|topup_purchased|quota_exceeded
    stripe_event_id = Column(String, nullable=True, unique=True)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)


class Report(Base):
    __tablename__ = "reports"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    venue_id = Column(String, ForeignKey("venues.id"), nullable=True)
    title = Column(String, nullable=False, default="Untitled Report")
    description = Column(Text, nullable=True)
    layout = Column(JSON, nullable=False, default=list)  # [{chart_id, x, y, w, h}]
    status = Column(String, nullable=False, default="draft")  # draft|saved
    # Dashboard fields
    is_dashboard = Column(Boolean, nullable=False, default=False)
    agent_slug = Column(String, nullable=True)  # hr, procurement, reports
    is_published = Column(Boolean, nullable=False, default=False)
    is_template = Column(Boolean, nullable=False, default=False)
    refresh_interval_seconds = Column(Integer, nullable=True)  # null = manual
    global_filters = Column(JSON, nullable=True)  # for reports: date range, venue
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    charts = relationship(
        "ReportChart",
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="ReportChart.position",
    )
    user = relationship("User")


class ReportChart(Base):
    __tablename__ = "report_charts"

    id = Column(String, primary_key=True, default=_uuid)
    report_id = Column(String, ForeignKey("reports.id"), nullable=False)
    title = Column(String, nullable=False)
    chart_type = Column(
        String, nullable=False, default="bar"
    )  # bar|stacked_bar|line|pie|scatter|bubble|table
    chart_spec = Column(
        JSON, nullable=False, default=dict
    )  # {x_axis, y_axis, series, orientation}
    data = Column(JSON, nullable=False, default=list)  # row data
    script = Column(
        JSON, nullable=False, default=dict
    )  # {connector, action, params} replayable recipe
    position = Column(Integer, nullable=False, default=0)
    source_thread_id = Column(String, ForeignKey("threads.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    report = relationship("Report", back_populates="charts")


# ── DevOps ──────────────────────────────────────────────────────────
class Deployment(Base):
    __tablename__ = "deployments"

    id = Column(String, primary_key=True, default=_uuid)
    environment = Column(String, nullable=False)  # testing|staging|production
    image_tag = Column(String, nullable=False)
    git_sha = Column(String, nullable=False)
    commit_message = Column(Text, nullable=True)
    status = Column(
        String, nullable=False, default="pending"
    )  # pending|running|success|failed
    started_at = Column(DateTime(timezone=True), default=_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    logs_url = Column(String, nullable=True)
    triggered_by = Column(String, nullable=True)  # ci|manual|webhook


class E2ETestRun(Base):
    """Per-environment run history for E2E tests.

    Test definitions live in the shared config DB (see E2ETest in
    config_models.py); this table tracks executions and stays per-env.
    test_id is a plain string reference — no DB-level FK since the
    referenced table is in a different database.
    """

    __tablename__ = "e2e_test_runs"

    id = Column(String, primary_key=True, default=_uuid)
    test_id = Column(
        String, nullable=True
    )  # null for suite runs; references E2ETest.id in config DB
    environment = Column(String, nullable=False)
    status = Column(
        String, nullable=False, default="pending"
    )  # pending | running | passed | failed | error
    started_at = Column(DateTime(timezone=True), default=_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    stdout = Column(Text, nullable=True)  # combined stdout/stderr from test run
    screenshots_json = Column(JSON, default=list)
    video_url = Column(String, nullable=True)
    triggered_by = Column(String, nullable=True)  # ci | manual
    git_sha = Column(String, nullable=True)


class EmailLog(Base):
    __tablename__ = "email_logs"

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=True)
    venue_id = Column(String, ForeignKey("venues.id"), nullable=True)
    thread_id = Column(String, ForeignKey("threads.id"), nullable=True)
    sender_type = Column(String, nullable=False)  # system|on_behalf
    sender_email = Column(String, nullable=False)
    sender_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    to_addresses = Column(JSON, nullable=False)
    cc_addresses = Column(JSON, nullable=True)
    bcc_addresses = Column(JSON, nullable=True)
    subject = Column(String, nullable=False)
    template_name = Column(String, nullable=True)
    html_body = Column(Text, nullable=True)
    has_attachments = Column(Boolean, default=False)
    status = Column(
        String, nullable=False, default="queued"
    )  # queued|sent|failed|bounced
    provider = Column(String, nullable=True)  # resend|gmail|microsoft_graph
    provider_message_id = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=_now)
    sent_at = Column(DateTime(timezone=True), nullable=True)


class EmailTemplate(Base):
    __tablename__ = "email_templates"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, unique=True, nullable=False)
    subject_template = Column(String, nullable=False)
    html_template = Column(Text, nullable=False)
    category = Column(String, nullable=False)  # billing|task|auth|report
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class Memory(Base):
    """A durable fact Norm has learned about a user or an organisation.

    Deliberately in the MAIN database, not the config DB. The config DB has no
    ``organization_id`` and is shared across every environment *and* every
    organisation, so an org-scoped row there would be visible to other tenants.

    Scope is decided by one question: would a colleague asking the same thing
    want a different answer? Yes → ``user``. No → ``org``. Venue-specific facts
    are org memories tagged with ``venue_id`` rather than a third scope, because
    a venue fact is an org fact that happens to be narrow.

    What may live here is deliberately narrow — see ``memory_rules``. Nothing
    that changes a number, gates money, or can be queried from a connector: the
    trading-day work proved that a rule expressed as advice fails silently, so
    those belong in enforced code, not here. Memory carries judgement only.
    """

    __tablename__ = "memories"

    id = Column(String, primary_key=True, default=_uuid)

    # Scope. Exactly one of user_id / organization_id drives visibility, but
    # organization_id is always set so an org admin can see what their people
    # have taught Norm.
    scope = Column(String, nullable=False)  # "user" | "org"
    user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    organization_id = Column(
        String, ForeignKey("organizations.id"), nullable=False, index=True
    )
    venue_id = Column(String, ForeignKey("venues.id"), nullable=True)

    # Content. `title` is what goes in the always-loaded index; the rest is
    # fetched on demand, which is what keeps the per-turn cost bounded.
    type = Column(String, nullable=False)  # vocabulary|preference|context|correction
    title = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    why = Column(Text, nullable=True)
    how_to_apply = Column(Text, nullable=True)

    # Provenance. Without it a memory is an unattributable assertion that
    # quietly shapes answers, which is the thing that makes learned state
    # untrustworthy.
    thread_id = Column(
        String, ForeignKey("threads.id", ondelete="SET NULL"), nullable=True
    )
    created_by = Column(String, nullable=False, default="agent")  # agent|user
    trigger = Column(String, nullable=True)  # explicit|correction|draft_edit|rejection

    # Lifecycle. Candidates are proposed but do not influence answers until
    # confirmed — org-scoped writes always land here first, because a shared
    # write changes other people's answers.
    status = Column(
        String, nullable=False, default="active"
    )  # candidate|active|archived|superseded
    superseded_by = Column(
        String, ForeignKey("memories.id", ondelete="SET NULL"), nullable=True
    )
    #: `context` memories rot ("Mr Murdochs is closed" has a shelf life);
    #: preferences and vocabulary do not, and leave this null.
    review_after = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class MemorySignal(Base):
    """Raw evidence that something might be worth remembering.

    Deliberately not a ``Memory``. A single draft edit — "changed quantity from
    5 to 8" — is not a fact about the business; it becomes one only when it
    recurs or a human confirms it. Writing straight to memory would fill the
    store with noise that shapes answers, which is the failure mode the
    admission rules exist to prevent.

    So signals are captured verbatim and interpreted later. The important part
    is capture: these were previously **destroyed**. ``pending_ops`` on a
    working document is a sync outbox — it is drained and cleared once the
    connector accepts the change, taking with it the delta between what Norm
    drafted and what the human actually wanted. That delta is the highest-value
    learning signal in the product and nothing was keeping it.
    """

    __tablename__ = "memory_signals"

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(
        String, ForeignKey("organizations.id"), nullable=False, index=True
    )
    user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    thread_id = Column(
        String, ForeignKey("threads.id", ondelete="SET NULL"), nullable=True
    )

    #: draft_edit | rejection
    kind = Column(String, nullable=False)
    #: Human-readable one-liner — what changed, in words.
    summary = Column(Text, nullable=False)
    #: The structured evidence (the ops, or the approval note).
    detail = Column(JSON, nullable=True)

    #: Set once this signal has been turned into a candidate memory, so the
    #: same evidence is not proposed twice.
    promoted_to_memory_id = Column(
        String, ForeignKey("memories.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), default=_now)
