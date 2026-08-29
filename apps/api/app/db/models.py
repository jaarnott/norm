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
    LargeBinary,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    #: How far this venue lets Norm go when receiving invoices — the tier plus
    #: the per-action toggles. See services/venue_autopilot for the shape and
    #: the defaults. Venue-scoped rather than per-user because invoices are, and
    #: because venues differ in how clean their Loaded catalogue is: one can run
    #: on autopilot while another is still approving every line by hand.
    invoice_autopilot = Column(JSON, nullable=True)

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
    # Files the user attached to this turn: [{upload_id, filename, content_type,
    # size}]. The bytes live in UploadedDocument; this is the render + rehydrate
    # reference. Only ever set on role="user" messages.
    attachments = Column(JSON, nullable=True)
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
    ConnectionSpec,
    AgentConfig,
    AgentConnectionBinding,
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
    # PKCE (OAuth 2.1 public clients): the code_verifier is generated when the
    # authorize URL is built and must survive the redirect round-trip to be sent
    # on token exchange. Single-use, consumed with the state. Null for legacy /
    # non-PKCE flows.
    code_verifier = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)


class Connection(Base):
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
    # Connection health. Set when a token refresh (or a live call's reactive
    # refresh) is rejected by the provider, cleared on the next successful token
    # store. `bool(access_token)` only says a token was once issued — it stays
    # true after the refresh token dies, which is exactly how a LoadedHub outage
    # read as "Connected" while every fetch failed. This is the durable signal
    # the UI and the in-conversation reconnect card read.
    needs_reconnect = Column(Boolean, nullable=False, default=False)
    last_auth_error = Column(Text, nullable=True)
    last_auth_checked_at = Column(DateTime(timezone=True), nullable=True)
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


class UploadedDocument(Base):
    """A document a user uploaded — a Norm-wide capability.

    The bytes live in the DB (LargeBinary), matching the only in-repo binary
    precedent (SupplierSpecSample). ``extraction_target`` names what the upload
    is for (e.g. "recipe"), so one generic upload endpoint can hand it to the
    right extractor. Kept small — the router caps size; recipe docs are a few MB.
    """

    __tablename__ = "uploaded_documents"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    venue_id = Column(String, ForeignKey("venues.id"), nullable=True)
    # Set when the upload is attached to a chat message, so the model can
    # re-fetch it later in the conversation via the get_attachment tool.
    thread_id = Column(String, ForeignKey("threads.id"), nullable=True)
    filename = Column(String, nullable=True)
    content_type = Column(String, nullable=True)
    size = Column(Integer, nullable=True)
    data = Column(LargeBinary, nullable=False)
    extraction_target = Column(String, nullable=True)  # e.g. "recipe", "chat"
    created_at = Column(DateTime(timezone=True), default=_now)


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


class OrgAppEntitlement(Base):
    """Per-org marketplace state: is App X enabled for org Y?

    The catalog itself is `marketplace_apps` in the CONFIG db (shared, no org
    scoping); this row is the org-scoped half. **Absence of a row means the
    app's `bundled` default applies** — bundled apps are on for every org
    until an owner explicitly turns one off — so seeding the catalog changes
    nothing and there is no backfill to get wrong. Disabling is a
    billing/visibility act, never a deletion: app data is retained and
    re-enabling restores the app over it.

    Absorbs (and will retire) the three `Organization.*_agent_enabled`
    billing booleans, which were read only by billing math and enforced
    nowhere. (docs/apps-marketplace-plan.md Phase 1.)
    """

    __tablename__ = "org_app_entitlements"
    __table_args__ = (
        UniqueConstraint("organization_id", "app_slug", name="uq_org_app"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(
        String, ForeignKey("organizations.id"), nullable=False, index=True
    )
    #: MarketplaceApp.slug (config db — deliberately not a FK across databases).
    app_slug = Column(String, nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=True)
    #: Stripe subscription item backing a paid app's line item, when priced.
    stripe_subscription_item_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    organization = relationship("Organization")


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


class InvoiceAutopilotOutcome(Base):
    """One row per receive attempt: would autopilot have got this invoice right?

    Autopilot means "accept every suggestion, then receive". So every human
    receive is a free experiment in that counterfactual — provided we record
    whether the human did anything autopilot would NOT have done. A row is
    therefore the answer to one question, not an audit log:

      clean          every suggestion accepted, nothing dismissed or left
                     pending, no hand edits          → autopilot: right
      no_suggestions reviewed, Norm had nothing to say, no hand edits
                                                     → autopilot: right
      edited         a suggestion was dismissed or ignored, OR the human
                     typed a value themselves        → autopilot: WRONG
      not_reviewed   never reviewed (legacy/reset)   → unknown, no rate
      dojo           "Cannot receive" — filed for training

    Two traps this shape exists to avoid. Autopilot's OWN receives are
    self-fulfilling (it accepted everything a line earlier, so every such row
    is clean by construction) — hence ``actor``, and a report that defaults to
    humans only. And "Norm had nothing to say" is not the same as "nobody ever
    reviewed it", which is why those are separate outcomes rather than one
    flattering bucket.

    Deliberately holds no full payload copies. ``detail.manual_fields`` keeps
    field NAMES only; ``detail.auto`` (the end-state verdict) keeps the
    sent-vs-simulated DIFF — path plus both values per divergent field, the
    evidence behind "autopilot would have sent something else" — and the flag
    set autopilot needed. The working document and Loaded stay the source of
    truth for everything else.
    """

    __tablename__ = "invoice_autopilot_outcomes"
    __table_args__ = (
        # Loaded receives an invoice once, so a second row with the same
        # outcome is always a retry — make double-counting impossible rather
        # than hope the pre-check wins the race.
        UniqueConstraint(
            "venue_id", "invoice_id", "outcome", name="uq_autopilot_outcome"
        ),
    )

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(
        String, ForeignKey("organizations.id"), nullable=True, index=True
    )
    #: Nullable so the measurement outlives a deleted venue — deleting a
    #: venue is not a reason to erase the evidence of how well Norm did.
    venue_id = Column(String, ForeignKey("venues.id"), nullable=True, index=True)
    #: Loaded's invoice id — a foreign system's key, so no FK (same rule as
    #: E2ETestRun.test_id).
    invoice_id = Column(String, nullable=False, index=True)
    reference_number = Column(String, nullable=True)
    #: The report's grouping key. No FK: supplier specs live in the config DB.
    supplier_name = Column(String, nullable=True, index=True)
    linked_supplier_id = Column(String, nullable=True)

    #: clean | no_suggestions | edited | not_reviewed | dojo
    outcome = Column(String, nullable=False)
    received = Column(Boolean, nullable=False, default=False)
    #: interactive | mcp_card | autopilot | approve_fixes
    mode = Column(String, nullable=False)
    #: user | norm — norm rows are excluded from the readiness rates.
    actor = Column(String, nullable=False)

    user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    working_document_id = Column(
        String, ForeignKey("working_documents.id", ondelete="SET NULL"), nullable=True
    )
    thread_id = Column(
        String, ForeignKey("threads.id", ondelete="SET NULL"), nullable=True
    )

    #: delete_invoice is excluded from every count — autopilot skips it too.
    suggestion_count = Column(Integer, nullable=False, default=0)
    accepted_count = Column(Integer, nullable=False, default=0)
    dismissed_count = Column(Integer, nullable=False, default=0)
    pending_count = Column(Integer, nullable=False, default=0)
    manual_edit_count = Column(Integer, nullable=False, default=0)
    blocking_issue_count = Column(Integer, nullable=False, default=0)
    #: Blocking issues a human waved through ("I've checked this") — autopilot
    #: would have STOPPED, a different and milder failure than being wrong.
    issues_waved_count = Column(Integer, nullable=False, default=0)
    #: ready | needs_review, as of the receive.
    confidence = Column(String, nullable=True)

    #: manual_fields, suggestion_kinds, dismissed/pending ids, baseline_fresh,
    #: is_credit_note, reviewed_at, and the dojo block.
    detail = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, index=True)


# ── The app platform ──────────────────────────────────────────────────────
#
# A Norm app is user-authored software that runs inside Norm's own
# infrastructure. Two sandboxes already existed and are reused wholesale: the
# consolidator runtime (`connectors/function_executor.execute_function`) for
# logic, and the MCP-app iframe (`apps/mcp-ui` + `mcp/ui_apps.py`) for UI. What
# these tables add is the missing object — an app with a version, a declared
# reach, and an audience.
#
# They live in the MAIN db, not the config db: the config db is shared by every
# environment and has no org scoping, and an app is org data. Publishing to a
# marketplace later promotes a version into a global registry — a separate and
# deliberate step, not an accident of where the row happens to sit.


class App(Base):
    """A user-built app: what it is for, who may see it, and which version runs.

    The app owns identity and audience only. Everything executable — the UI, the
    logic, and crucially the DECLARED REACH (which connector actions and scopes
    it may use) — lives on an immutable ``AppVersion``, so sharing can pin a
    version and the author can keep editing without changing what anyone else
    is running.
    """

    __tablename__ = "apps"
    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_app_org_slug"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(
        String, ForeignKey("organizations.id"), nullable=False, index=True
    )
    #: The author. Kept even after they leave — an app outlives its builder,
    #: and "who made this" is the first question anyone asks of a shared app.
    created_by = Column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    slug = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    #: Emoji or lucide icon name — apps sit beside Norm's own pages in the nav.
    icon = Column(String, nullable=True)
    #: Which agent's menu this app's pages join — "hr", "procurement", … NULL
    #: means the App Builder, which is where apps lived before they could
    #: choose. Validated against the registered agents on save: a slug with no
    #: agent behind it would be unreachable (no sidebar button can select it)
    #: AND would silently break chat, because page_context.agent is taken on
    #: faith by the supervisor.
    agent = Column(String, nullable=True)
    #: The plain-language brief the user gave the builder. Kept verbatim: it is
    #: what the builder re-reads to revise the app, and the honest answer to
    #: "what was this meant to do".
    purpose = Column(Text, nullable=True)
    #: private | users | venue | organization. Private on creation, always —
    #: widening is an explicit act recorded in AppShare.
    visibility = Column(String, nullable=False, default="private")
    #: Plain String, not an FK: apps and app_versions reference each other, and
    #: a circular FK pair costs a use_alter dance for no protection we need.
    current_version_id = Column(String, nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, index=True)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class AppVersion(Base):
    """One immutable build of an app: its UI, its logic, and its declared reach.

    ``spec`` is the security-relevant half and the reason versions are frozen:

        actions   [{connector, action}]  the ONLY calls this version may make
        playbooks [slug]                 the ONLY playbooks it may run
        scopes    [permission scope]     the most it may ever do
        writes    [{connector, action}]  the non-GET subset, approved separately
        components[name]                 Norm components its UI mounts
        params    {name: description}    what the app asks the viewer for

    Reach is declared per VERSION rather than per app so that editing an app
    can never silently widen what an already-shared copy is allowed to touch.
    A viewer always runs a pinned version; a wider reach means a new version
    and a fresh approval.
    """

    __tablename__ = "app_versions"
    __table_args__ = (UniqueConstraint("app_id", "version", name="uq_app_version"),)

    id = Column(String, primary_key=True, default=_uuid)
    app_id = Column(
        String, ForeignKey("apps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version = Column(Integer, nullable=False, default=1)
    spec = Column(JSON, nullable=False, default=dict)
    #: The app's own markup/JS. Untrusted by construction — it runs in the same
    #: sandboxed iframe MCP apps use, holding no session token. The data door,
    #: not this string, is the security boundary.
    ui_source = Column(Text, nullable=True)
    #: Optional server-side `run(params, call_api, log)`, executed by the
    #: consolidator sandbox with call_api bound to this version's allowlist.
    logic_source = Column(Text, nullable=True)
    changelog = Column(Text, nullable=True)
    created_by = Column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), default=_now, index=True)


class AppShare(Base):
    """Who may run an app, and whether its writes were approved.

    Sharing and write-approval are deliberately ONE row rather than two
    concepts: the question "may Sam run this?" and "may it place orders when
    Sam runs it?" are answered by the same person at the same moment, and
    splitting them invites an app being shared widely with its write grant
    quietly inherited from somewhere else.

    Effective permission is always the INTERSECTION of this grant with the
    viewer's own org permissions — a share can widen who runs an app, never
    what that person is allowed to do.
    """

    __tablename__ = "app_shares"
    __table_args__ = (
        UniqueConstraint(
            "app_id", "principal_type", "principal_id", name="uq_app_share_principal"
        ),
    )

    id = Column(String, primary_key=True, default=_uuid)
    app_id = Column(
        String, ForeignKey("apps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: user | venue | organization
    principal_type = Column(String, nullable=False)
    #: The user/venue/org id. No FK — one column cannot reference three tables,
    #: and the resolver checks membership at call time anyway.
    principal_id = Column(String, nullable=False, index=True)
    #: view | edit
    access = Column(String, nullable=False, default="view")
    #: Default FALSE. A shared app may read on day one; completing a write for
    #: this audience is a second, explicit decision naming the actions.
    write_actions_approved = Column(Boolean, nullable=False, default=False)
    granted_by = Column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), default=_now)


class AppRecord(Base):
    """A row an app owns — the platform's storage primitive.

    Until this existed an app could only ever be a VIEW over connector actions:
    a training sign-off or a candidate note had nowhere to live, so every
    non-trivial app needed someone else's system of record behind it (which is
    exactly why the Cook Brothers domains still sit in Supabase). This is the
    table that makes Norm the system of record for them.

    **Keyed by namespace, not by app.** Two apps that belong to one domain —
    Hiring and Training over the same people — must share rows, and the honest
    way to allow that is an explicit shared name rather than one app reaching
    into another's tables. A namespace is owned by whichever app claims it
    first; another app may join only if the owner's spec names it in
    ``storage.shared_with`` (enforced in ``app_runtime.save_app``). Nobody
    reads a namespace they did not declare.

    ``data`` is JSONB and deliberately schemaless: it is the Supabase-shaped
    surface the migrating apps were written against, it needs no per-app
    migration, and a collection that earns a real table can graduate later.

    Scoping mirrors the rest of Norm: ``organization_id`` is the hard tenancy
    boundary and is never optional; ``venue_id`` is nullable because plenty of
    real rows are group-wide — Orbit's training programs are exactly that, and
    a venue filter that cannot express "global" is the bug that hides them from
    its own API.
    """

    __tablename__ = "app_records"
    __table_args__ = (
        Index(
            "ix_app_records_org_collection",
            "namespace",
            "collection",
            "organization_id",
        ),
        Index("ix_app_records_venue_collection", "namespace", "collection", "venue_id"),
        # Containment index: what makes `where={"program_id": …}` an index hit
        # rather than a sequential scan with a per-row JSON parse.
        Index("ix_app_records_data", "data", postgresql_using="gin"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    namespace = Column(String, nullable=False, index=True)
    organization_id = Column(
        String,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: NULL means the row belongs to the whole organization, not one venue.
    venue_id = Column(
        String, ForeignKey("venues.id", ondelete="SET NULL"), nullable=True, index=True
    )
    collection = Column(String, nullable=False)
    #: JSONB, not the generic JSON the rest of Norm uses: this is the one
    #: table that is QUERIED THROUGH its document, and `astext`, nested paths
    #: and a GIN index are all impossible on plain JSON.
    data = Column(JSONB, nullable=False, default=dict)
    created_by = Column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by = Column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class AppFile(Base):
    """A file an app owns — evidence, a CV, an attachment.

    Apps had storage for rows and nowhere at all to put bytes, so a training
    sign-off photo or a candidate's CV could only live in someone else's
    bucket. That is what kept Orbit's Supabase alive: 412 completion records
    point at evidence in a **public** bucket, referenced from inside a JSONB
    blob with no table, no signed URLs and no way to delete.

    Bytes in the column, following ``UploadedDocument`` and
    ``SupplierSpecSample`` — the only binary precedent in this repo — because
    the volumes are operational (a few MB per file, a few thousand files) and a
    bucket would add an access-control surface separate from the one that
    already guards everything else here. A file is reached through the same
    door as a record, so "who may see this" has exactly one answer.

    ``namespace`` (not app slug) for the same reason records use it: a suite of
    apps shares its files. ``collection``/``record_id`` tie a file to the row it
    belongs to, so deleting that row can take its files with it.
    """

    __tablename__ = "app_files"
    __table_args__ = (
        Index("ix_app_files_owner", "namespace", "collection", "record_id"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    namespace = Column(String, nullable=False, index=True)
    organization_id = Column(
        String,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    venue_id = Column(
        String, ForeignKey("venues.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: Which record this file belongs to, so it can be found and cleaned up.
    collection = Column(String, nullable=True)
    record_id = Column(String, nullable=True)
    filename = Column(String, nullable=False)
    content_type = Column(String, nullable=True)
    size_bytes = Column(Integer, nullable=False, default=0)
    data = Column(LargeBinary, nullable=False)
    #: Where this came from if it was not uploaded here — e.g. the Orbit URL a
    #: migration pulled it from, kept so a re-run can recognise it.
    source_ref = Column(String, nullable=True, index=True)
    created_by = Column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), default=_now)


class AppCall(Base):
    """One row per action an app performed — the app platform's audit trail.

    Not ``ToolCall``: that table requires a ``thread_id`` (an agent's
    conversation) and an app has none. Loosening a NOT NULL on the busiest
    table in the schema to fit a new caller is the wrong trade, so apps get
    their own table with the columns that actually matter for them — which
    version ran, as whom, against which venue, and whether it worked.
    """

    __tablename__ = "app_calls"

    id = Column(String, primary_key=True, default=_uuid)
    app_id = Column(
        String, ForeignKey("apps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    app_version_id = Column(String, nullable=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    #: Nullable so the record outlives a deleted venue — same rule as
    #: InvoiceAutopilotOutcome: deleting a venue is not a reason to erase what
    #: an app did while it existed.
    venue_id = Column(
        String, ForeignKey("venues.id", ondelete="SET NULL"), nullable=True, index=True
    )
    connector = Column(String, nullable=False)
    action = Column(String, nullable=False)
    #: GET means a read. Anything else passed the write gate, and this column is
    #: how you find every write an app has ever made.
    method = Column(String, nullable=False, default="GET")
    ok = Column(Boolean, nullable=False, default=True)
    error = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, index=True)


# ── Transitional alias (connector → Connection rename, 29 Aug 2026) ──
ConnectorConfig = Connection
