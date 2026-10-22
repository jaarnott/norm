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
    CheckConstraint,
    Numeric,
    Date,
    BigInteger,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, relationship

from app.db.encrypted import EncryptedSecretsJSON, EncryptedText


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
    # Superseded by approval_preferences (Oct 2026), which the migration filled
    # from it. Kept until a later release drops it; nothing reads it.
    workflow_modes = Column(JSON, nullable=True)
    # What Norm may do without asking this person, per write tool
    # ("connector.action"): {"always": true}, or for a tool with levels
    # {"level": "autopilot", "options": {...}}. See services/approvals.py.
    approval_preferences = Column(JSON, nullable=True)

    threads = relationship("Thread", back_populates="user")
    memberships = relationship("OrganizationMembership", back_populates="user")


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint(
            "functions_mode IN ('off', 'shadow', 'live')",
            name="ck_organizations_functions_mode",
        ),
    )

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False)
    billing_email = Column(String, nullable=True)
    plan = Column(String, nullable=False, default="starter")  # starter|pro|enterprise
    is_active = Column(Boolean, nullable=False, default=True)
    # Company settings (Oct 2026, Norm Functions). Money is stored in cents
    # with no currency on the row — the company's currency and tax wording
    # apply. default_timezone is for org-level dates only: anything at a venue
    # uses venues.timezone (services/functions/timeutil.py).
    currency = Column(String(3), nullable=False, default="NZD", server_default="NZD")
    default_timezone = Column(
        String,
        nullable=False,
        default="Pacific/Auckland",
        server_default="Pacific/Auckland",
    )
    tax_label = Column(
        String, nullable=False, default="incl. GST", server_default="incl. GST"
    )
    #: Norm Functions switch: off | shadow (imported, read-only) | live.
    #: Checked in the service layer, the public router and the Gmail client.
    functions_mode = Column(String, nullable=False, default="off", server_default="off")
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

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
    __table_args__ = (
        # Redundant with the primary key as a uniqueness rule — it exists so
        # Norm Functions tables can reference (organization_id, venue_id)
        # together, which makes a cross-organisation venue reference
        # impossible rather than merely unlikely.
        UniqueConstraint("organization_id", "id", name="uq_venues_org_id"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=True)
    name = Column(String, nullable=False)
    location = Column(String)
    timezone = Column(String, nullable=True)  # IANA timezone e.g. "Pacific/Auckland"
    day_start_time = Column(String, nullable=True)  # HH:MM e.g. "07:00"
    # (invoice_autopilot was here until Oct 2026 — receiving settings are the
    # person's now; see services/venue_autopilot.)

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
    #: The organisation this conversation was started in (stamped on insert from
    #: the active organisation). Its usage is billed here; it never follows the
    #: user's other memberships.
    organization_id = Column(String, nullable=True, index=True)
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
    # Delegation: set when this thread is a sub-run created by another agent's
    # delegate_to_agent call. Deliberately NOT a ForeignKey — the parent row is
    # still uncommitted when the child is created inside the parent's turn.
    # Depth is derived server-side by walking this chain, never taken from
    # model-supplied params, so an agent can't talk its way past the cap.
    parent_thread_id = Column(String, nullable=True, index=True)
    delegation_depth = Column(Integer, nullable=False, default=0)
    # Set when the conversation began from an insight suggestion on Home: every
    # turn of it is told the insight's facts, and what the person says about it
    # ("it's a promo") is recorded against it (services/insights).
    insight_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    messages = relationship(
        "Message",
        back_populates="thread",
        order_by="Message.created_at",
        cascade="all, delete-orphan",
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
    # Not deleted with the thread: a deleted conversation was still paid for,
    # and deleting its calls erased it from every usage figure (a thread
    # deleted on 7 Oct 2026 took 31 calls with it). Deleting the thread
    # unlinks them instead (thread_id → NULL).
    llm_calls = relationship(
        "LlmCall",
        back_populates="thread",
        order_by="LlmCall.created_at",
        cascade="save-update, merge",
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
    #: A user turn exactly as the model was sent it — the text with the
    #: context, memory index and manifests appended. Later turns replay it
    #: verbatim so the conversation stays a byte-identical prefix and is read
    #: from the prompt cache rather than written again. Replaying the bare
    #: ``content`` instead changed the prompt at the very first message, so
    #: every turn rewrote the whole history (Oct 2026). NULL for older rows.
    sent_content = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)

    thread = relationship("Thread", back_populates="messages")


class Approval(Base):
    __tablename__ = "approvals"

    id = Column(String, primary_key=True, default=_uuid)
    # Optional since Oct 2026: changing an approval preference in Settings is
    # recorded here too, and happens outside any conversation.
    thread_id = Column(String, ForeignKey("threads.id"), nullable=True)
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
    #: Whose call it was — from the thread's user, else the caller's scope
    #: (an extraction has no thread). Usage totals and breakdowns read it.
    organization_id = Column(String, nullable=True)
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
    # Full-price input only — Anthropic's ``usage.input_tokens`` excludes the
    # cache. The cache parts, and what the call counted and cost, are below
    # (app/services/llm_cost.py).
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    cache_read_tokens = Column(Integer, nullable=True)
    cache_write_tokens = Column(Integer, nullable=True)
    billable_tokens = Column(Integer, nullable=True)
    cost_usd = Column(Numeric(14, 6), nullable=True)
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
    #: Typed credentials; secret-named values are stored encrypted (encrypted.py).
    config = Column(EncryptedSecretsJSON, nullable=False, default=dict)
    enabled = Column(String, nullable=False, default="true")
    # Encrypted at rest when TOKEN_ENCRYPTION_KEYS is set (app/db/encrypted.py):
    # still TEXT in the database and plain strings in Python, so nothing that
    # reads or writes them changes. Plaintext rows read back as they are.
    access_token = Column(EncryptedText, nullable=True)
    refresh_token = Column(EncryptedText, nullable=True)
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
    # Optional since Oct 2026: a write synced from a document opened outside a
    # conversation (the Roster page) has no thread, and must still be recorded.
    thread_id = Column(String, ForeignKey("threads.id"), nullable=True)
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
    # A write waiting for approval: what it will change, as the person saw it
    # (services/previews.py). Its fingerprint lets the approved run notice the
    # live data moved since.
    preview = Column(JSON, nullable=True)
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


class InvoicePrewarmAttempt(Base):
    """Failure memory for the background extraction pre-warm.

    The pre-warm picks candidates by "outstanding in Loaded, not yet in the
    extraction cache", newest first. Success needs no row — the extraction
    cache itself is the record. FAILURE does: without this table an invoice
    that can never be extracted (unreadable copy, missing file) stays a
    candidate forever and, being newest-first, consumes a slot on every tick.
    A handful of them would starve the feature completely while looking
    perfectly healthy.

    It records successes too (``warmed_at``), because selection has no cheaper
    way to know: the extraction cache is keyed on the composed supplier
    instructions, so asking it directly would mean fetching every outstanding
    invoice's detail every tick.

    Known limitation: if a sensei study later changes a supplier's spec, the
    cache key changes but ``warmed_at`` still reads warm, so the pre-warm will
    not redo it and that invoice's next open pays a cold extraction — exactly
    today's behaviour, never worse.

    Between them the two columns are also the observability surface: "what is
    failing and why" and "what has been warmed" are one query each.
    """

    __tablename__ = "invoice_prewarm_attempts"
    __table_args__ = (
        UniqueConstraint("venue_id", "invoice_id", name="uq_prewarm_attempt"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    venue_id = Column(String, nullable=False, index=True)
    invoice_id = Column(String, nullable=False)
    # Set once the extraction landed. Selection skips these — without it a
    # tick would have to fetch every outstanding invoice's detail just to
    # compute its cache key and discover it was already warm.
    warmed_at = Column(DateTime(timezone=True))
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(String)
    # NULL means "never retry" — the terminal state after MAX_ATTEMPTS.
    next_attempt_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


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
    #: The organisation the task was made in (stamped on insert). Its runs act
    #: there: its venues, and its creator's settings THERE — a person in two
    #: organisations may run Autopilot in one and not the other (Oct 2026).
    organization_id = Column(String, nullable=True, index=True)
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


class BackgroundJob(Base):
    """A long piece of work that runs outside the chat request.

    A chat request is cut off after 25 minutes (Cloud Run), and a batch that
    big — receiving 200 invoices — was losing everything it had done (14 Oct
    2026: two runs, ~$15, nothing saved). A job is claimed by a worker
    (services/background_jobs), heartbeats while it runs, saves its progress
    as it goes, and is picked up again by another worker if its heartbeat goes
    stale (a deploy restarted the instance). The thread it started from gets a
    live card for it and, at the end, Norm's summary.
    """

    __tablename__ = "background_jobs"

    id = Column(String, primary_key=True, default=_uuid)
    kind = Column(String, nullable=False)  # e.g. receive_invoices
    title = Column(String, nullable=False, default="")
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    venue_id = Column(String, ForeignKey("venues.id"), nullable=True)
    thread_id = Column(String, ForeignKey("threads.id"), nullable=True)
    # queued | running | done | failed | cancelled
    status = Column(String, nullable=False, default="queued")
    params = Column(JSON, nullable=False, default=dict)
    # {"total", "done", counters..., "current", "done_ids": [...]}
    progress = Column(JSON, nullable=False, default=dict)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    claimed_by = Column(String, nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_now)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_background_jobs_status", "status"),
        Index("ix_background_jobs_thread", "thread_id"),
    )


class AutomatedTaskRun(Base):
    __tablename__ = "automated_task_runs"

    id = Column(String, primary_key=True, default=_uuid)
    automated_task_id = Column(String, ForeignKey("automated_tasks.id"), nullable=False)
    thread_id = Column(
        String, ForeignKey("threads.id"), nullable=True
    )  # execution Thread record
    # running | success | error | skipped_unhired | waiting_for_approval (it
    # stopped at a write that asks) | declined (its changes were all declined)
    # | superseded (a newer run replaced it while it waited)
    status = Column(String, nullable=False, default="running")
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
    input_tokens = Column(Integer, default=0)  # full-price input only
    output_tokens = Column(Integer, default=0)
    cache_read_tokens = Column(Integer, default=0)
    cache_write_tokens = Column(Integer, default=0)
    #: What plan limits count — see app/services/llm_cost.py.
    billable_tokens = Column(Integer, default=0)
    cost_usd = Column(Numeric(14, 6), default=0)
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
    token_plan = Column(String, nullable=False, default="core")  # core|pro|max
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
    tokens = Column(Integer, nullable=False)  # token-era top-ups; 0 since Oct 2026
    usage_usd = Column(Numeric(10, 2), nullable=True)  # usage added (USD of model cost)
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
    # What `data` is for: {"refreshed_at", "venue_id" ("__all__" for every
    # venue), "period"} — so a page can tell a fresh cache from a stale one.
    data_meta = Column(JSON, nullable=True)
    script = Column(
        JSON, nullable=False, default=dict
    )  # {connector, action, params} replayable recipe
    position = Column(Integer, nullable=False, default=0)
    source_thread_id = Column(String, ForeignKey("threads.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    report = relationship("Report", back_populates="charts")


class ReportVersion(Base):
    """A dashboard as it stood after one change: every version is kept, as an
    App's are, and any one can be brought back (Oct 2026).

    ``snapshot`` holds the content, not the figures: title, layout, period and
    each tile's id, title, type, spec, tool and position."""

    __tablename__ = "report_versions"
    __table_args__ = (
        UniqueConstraint("report_id", "version", name="uq_report_version"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    report_id = Column(
        String, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False)
    note = Column(Text, nullable=True)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)


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
    __table_args__ = (
        # One claimed-or-sent email per (org, app, idempotency key) — the database is the
        # guard, so two concurrent runs can't both send (services/app_email.py).
        Index(
            "uq_email_logs_app_key",
            "organization_id",
            "app_slug",
            "idempotency_key",
            unique=True,
            postgresql_where=sa_text(
                "idempotency_key IS NOT NULL AND status IN ('sending', 'sent') AND is_test = false"
            ),
        ),
    )

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
    #: Sent by an app's logic (services/app_email.py) — which app, and the
    #: app's own idempotency key, so a re-run never sends the same email twice.
    app_slug = Column(String, nullable=True, index=True)
    idempotency_key = Column(String, nullable=True)
    is_test = Column(Boolean, nullable=False, default=False)


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
        # One row per time it happened: Loaded can restore an invoice that
        # was received or deleted, and it can go the same way again
        # (``occurrence`` 1, 2, …). A retry of the same act records nothing
        # (record_receive_outcome decides), and the key makes a race between
        # two writes of one occurrence impossible rather than hoping the
        # pre-check wins it.
        UniqueConstraint(
            "venue_id",
            "invoice_id",
            "outcome",
            "occurrence",
            name="uq_autopilot_outcome",
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
    #: 0 the first time this invoice went this way; 1, 2, … each time it came
    #: back (restored in Loaded) and went the same way again.
    occurrence = Column(Integer, nullable=False, default=0, server_default="0")
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
    #: app | dashboard. Both do the same things; they differ in where they live
    #: and how they're shared. An App sits in its agent's menu, is shared with
    #: people, venues or the organisation, and can be published. A Dashboard
    #: sits on its agent's Dashboard page: only its owner changes it, and anyone
    #: it's shared with uses it as it is or makes their own copy. Changed only
    #: as a deliberate step (save_app's change_kind), never by an edit.
    kind = Column(String, nullable=False, default="app", server_default="app")
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


class AppViewerSetting(Base):
    """One person's own choices on one page — colours, % or $, the last period.

    Records (``AppRecord``) are shared by everyone using a page; these are not.
    Rich picked chart colours on his dashboard and they were saved as one row
    for the whole organisation, so anyone else's pick would have changed his
    screen (Oct 2026). A page reads and writes these with ``norm.mine``.

    ``app_key`` is the app's id, or ``template:<slug>`` / ``builtin:<slug>`` for
    a page with no ``apps`` row, so a copy of a dashboard starts with none."""

    __tablename__ = "app_viewer_settings"
    __table_args__ = (
        UniqueConstraint(
            "app_key", "organization_id", "user_id", name="uq_app_viewer_setting"
        ),
    )

    id = Column(String, primary_key=True, default=_uuid)
    app_key = Column(String, nullable=False)
    organization_id = Column(
        String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    data = Column(JSONB, nullable=False, default=dict)
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
    __table_args__ = (
        CheckConstraint(
            "app_id IS NOT NULL OR builtin_slug IS NOT NULL",
            name="ck_app_calls_names_an_app",
        ),
    )

    id = Column(String, primary_key=True, default=_uuid)
    #: NULL for an app built into Norm (services/builtin_apps.py) — it has no
    #: ``apps`` row, and is named by ``builtin_slug`` instead.
    app_id = Column(
        String, ForeignKey("apps.id", ondelete="CASCADE"), nullable=True, index=True
    )
    builtin_slug = Column(String, nullable=True, index=True)
    #: The org the action ran in — for a built-in, the only link to it.
    organization_id = Column(
        String,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
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


class ThreadMemoryFile(Base):
    """One file in a conversation's notebook — Anthropic's memory tool, backed
    by Norm.

    The memory tool is client-side: the model only REQUESTS file operations
    (view / create / str_replace / insert / delete / rename) and Norm executes
    them here. Scope is the conversation, deliberately: this is the model's
    working notes for the task at hand, and it dies with the thread. Durable
    facts about the business go through `remember`, which has admission rules
    and a candidate-until-confirmed lifecycle that a free-text file does not.

    Why it exists: a long job's state — which tender line matched which item,
    what was decided, what was written — lived only in the conversation, so a
    dead turn lost all of it (17 minutes on 1 Oct 2026, thread c6aad2d5). With
    the memory tool present Anthropic injects "view your memory before anything
    else; record progress; assume interruption" into every call, which is the
    discipline Norm's own note tools never got (18 calls in 90 days).
    """

    __tablename__ = "thread_memory_files"
    __table_args__ = (UniqueConstraint("thread_id", "path"),)

    id = Column(String, primary_key=True, default=_uuid)
    thread_id = Column(
        String, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Always under /memories — the handler rejects anything else.
    path = Column(String, nullable=False)
    content = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class AppScheduleRun(Base):
    """One scheduled run of a built-in app's op, for one org, for one slot.

    The unique (organization, app, op, slot) row IS the once-per-slot guard:
    the scheduler inserts it before running, so a second API instance — or the
    next 5-minute tick — finds it taken and skips (services/app_schedules.py).
    """

    __tablename__ = "app_schedule_runs"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "app_slug", "op", "slot", name="uq_app_schedule_slot"
        ),
    )

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(
        String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    app_slug = Column(String, nullable=False)
    op = Column(String, nullable=False)
    #: The local date the run belongs to (YYYY-MM-DD in the schedule's tz).
    slot = Column(String, nullable=False)
    started_at = Column(DateTime(timezone=True), default=_now)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    ok = Column(Boolean, nullable=True)
    error = Column(Text, nullable=True)
    summary = Column(JSON, nullable=True)


class AppEmailSetting(Base):
    """Whether an app may send email for one org. Off unless an org admin
    turns it on — an app that emails people is a decision, not a default."""

    __tablename__ = "app_email_settings"
    __table_args__ = (
        UniqueConstraint("organization_id", "app_slug", name="uq_app_email_setting"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(
        String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    app_slug = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)
    updated_by = Column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class Insight(Base):
    """Something Norm noticed in a venue's numbers, worth putting in front of a
    person as a suggestion (Oct 2026; docs/plans/insights-research.md).

    One row per subject: the daily run updates the row for the same
    ``subject_key`` (venue, detector, subject) instead of adding another, so
    "Tuesdays run wages at 70% of sales" is one insight that changes value,
    not a new one every week.
    """

    __tablename__ = "insights"
    __table_args__ = (UniqueConstraint("subject_key", name="uq_insight_subject"),)

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(
        String,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    venue_id = Column(
        String, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    #: The detector's name (wage_weekday, price_rise, …) and what it is about
    #: within the venue ("Tuesday", "CHIP SHOESTRING · Bidfood").
    detector = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    subject_key = Column(String, nullable=False)
    #: sales | cogs | wages | data — what it is about, for ranking and the member
    #: whose new chat shows it.
    area = Column(String, nullable=False)
    #: Who may see it: a permission scope (reports:read, roster:read, …).
    audience = Column(String, nullable=False)
    headline = Column(Text, nullable=False)
    #: What a click sends: the headline's question with the facts Norm needs.
    prompt = Column(Text, nullable=False)
    #: Dollars per week (the common scale insights rank on); 0 when it is not
    #: about money (a data gap).
    value = Column(Numeric(12, 2), nullable=False, default=0)
    data = Column(JSON, nullable=True)
    #: The last trading day the numbers run to.
    as_of = Column(String, nullable=False)
    first_seen_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    expires_at = Column(DateTime(timezone=True), nullable=False)


class InsightRun(Base):
    """One venue's insight run for one local day: the unique (venue, slot) row
    is the once-a-day guard, inserted before the run (as AppScheduleRun)."""

    __tablename__ = "insight_runs"
    __table_args__ = (UniqueConstraint("venue_id", "slot", name="uq_insight_run_slot"),)

    id = Column(String, primary_key=True, default=_uuid)
    venue_id = Column(
        String, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    #: The venue's local date (YYYY-MM-DD) the run belongs to.
    slot = Column(String, nullable=False)
    #: Which detector sets ran: daily, weekly, roster.
    cadences = Column(JSON, nullable=True)
    started_at = Column(DateTime(timezone=True), default=_now)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    ok = Column(Boolean, nullable=True)
    error = Column(Text, nullable=True)
    summary = Column(JSON, nullable=True)


class InsightNote(Base):
    """What a person said about an insight, so Norm stops (or keeps) raising
    it: expected ("it's a First Table promo until 30 Nov"), acting ("we'll cut
    the Tuesday roster"), or not_useful. Recorded by ``norm.note_insight``."""

    __tablename__ = "insight_notes"

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(
        String,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    venue_id = Column(
        String, ForeignKey("venues.id", ondelete="CASCADE"), nullable=True
    )
    subject_key = Column(String, nullable=False, index=True)
    detector = Column(String, nullable=False)
    verdict = Column(String, nullable=False)  # expected | acting | not_useful
    reason = Column(Text, nullable=True)
    #: Quiet (expected) or follow up (acting) until this local date.
    until = Column(String, nullable=True)
    #: The insight's value when the note was made — an "expected" insight
    #: speaks again only if it moves well away from this.
    baseline = Column(Numeric(12, 2), nullable=True)
    created_by = Column(
        String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    thread_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)


class SuggestionEvent(Base):
    """A suggestion shown, clicked or dismissed — how Home learns what each
    person finds useful and stops repeating itself."""

    __tablename__ = "suggestion_events"
    __table_args__ = (Index("ix_suggestion_events_user_time", "user_id", "created_at"),)

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    organization_id = Column(String, nullable=True)
    suggestion_id = Column(String, nullable=False)
    #: Set for an insight: repeats are judged per subject, not per row.
    subject_key = Column(String, nullable=True)
    event = Column(String, nullable=False)  # shown | clicked | dismissed
    venue_id = Column(String, nullable=True)
    #: The insight's value when shown, so a material change can resurface it.
    value = Column(Numeric(12, 2), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)


class UnitCheckRun(Base):
    """One daily unit check (services/unit_check.py): every line Norm received
    since the last run, compared with the invoice Norm read and with Norm's
    catalogue. The unique ``slot`` (a UTC date) is the once-a-day guard,
    inserted before the run, as InsightRun."""

    __tablename__ = "unit_check_runs"
    __table_args__ = (UniqueConstraint("slot", name="uq_unit_check_run_slot"),)

    id = Column(String, primary_key=True, default=_uuid)
    slot = Column(String, nullable=False)
    #: The receipts checked: Norm's receives from window_start to window_end.
    window_start = Column(DateTime(timezone=True), nullable=True)
    window_end = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), default=_now)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    ok = Column(Boolean, nullable=True)
    error = Column(Text, nullable=True)
    #: {"venues": [{venue_id, name, invoices, lines, proposals, errors}], ...}
    summary = Column(JSON, nullable=True)
    emailed_at = Column(DateTime(timezone=True), nullable=True)


class UnitCheckProposal(Base):
    """One line for a person to review in the Dojo's daily unit check. Either a
    correction (``kind`` "correct": the invoice or Norm's catalogue says another
    unit) or a line Norm booked on an answer it wasn't sure of (``kind``
    "confirm"), which a person confirms or changes once, and Norm's catalogue
    keeps. The check itself never writes to Loaded."""

    __tablename__ = "unit_check_proposals"

    id = Column(String, primary_key=True, default=_uuid)
    run_id = Column(
        String,
        ForeignKey("unit_check_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    venue_id = Column(String, nullable=False, index=True)
    venue_name = Column(String, nullable=True)
    invoice_id = Column(String, nullable=False)
    invoice_number = Column(String, nullable=True)
    supplier_name = Column(String, nullable=True)
    invoice_date = Column(String, nullable=True)
    line_id = Column(String, nullable=False)
    item_id = Column(String, nullable=True)
    item_name = Column(String, nullable=True)
    code = Column(String, nullable=True)
    #: The line as printed on the invoice Norm read (None for Ordermentum).
    printed = Column(JSON, nullable=True)
    quantity = Column(Numeric(14, 4), nullable=True)
    unit_cost = Column(Numeric(14, 4), nullable=True)
    booked_unit_id = Column(String, nullable=True)
    booked_unit_name = Column(String, nullable=True)
    #: What the invoice shows, and what Norm's catalogue says, in words.
    invoice_says = Column(String, nullable=True)
    catalogue_says = Column(String, nullable=True)
    proposed_unit_id = Column(String, nullable=True)
    proposed_unit_name = Column(String, nullable=True)
    reason = Column(Text, nullable=True)
    #: correct | confirm
    kind = Column(String, nullable=False, default="correct")
    #: The product in Norm's catalogue (supplier layout + product key): where a
    #: person's answer is kept.
    catalog_key = Column(String, nullable=True)
    product_key = Column(String, nullable=True)
    #: Norm's own estimate for a "confirm" line: {unit, confidence, why}.
    norm_estimate = Column(JSON, nullable=True)
    #: open | applied | confirmed | dismissed | failed
    status = Column(String, nullable=False, default="open")
    decided_by = Column(String, nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    note = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)


from sqlalchemy import event as _event  # noqa: E402


@_event.listens_for(Thread, "before_insert")
def _stamp_thread_organization(mapper, connection, target):
    """Every creator of a thread (chat, tasks, MCP, schedules) gets the same
    answer: the venue's organisation, else the one the user is acting in."""
    if target.organization_id:
        return
    from sqlalchemy import select

    if target.venue_id:
        org = connection.execute(
            select(Venue.organization_id).where(Venue.id == target.venue_id)
        ).scalar()
        if org:
            target.organization_id = org
            return
    if target.user_id:
        target.organization_id = _acting_org(connection, target.user_id)


def _acting_org(connection, user_id: str) -> str | None:
    """The organisation a person is acting in when something of theirs is
    made: the caller's scope (a conversation's or a task run's), else the one
    the request asked for, among the person's memberships; else their oldest."""
    from sqlalchemy import select

    from app.services.active_org import requested_org
    from app.services.caller_scope import current

    rows = [
        r[0]
        for r in connection.execute(
            select(OrganizationMembership.organization_id)
            .where(OrganizationMembership.user_id == user_id)
            .order_by(
                OrganizationMembership.created_at.asc().nulls_last(),
                OrganizationMembership.id,
            )
        )
    ]
    scope = current()
    for want in ((scope.org_id if scope else None), requested_org.get()):
        if want and want in rows:
            return want
    return rows[0] if rows else None


@_event.listens_for(AutomatedTask, "before_insert")
def _stamp_task_organization(mapper, connection, target):
    """A task belongs to its venue's organisation, else the one its creator
    was acting in when they made it."""
    if target.organization_id:
        return
    from sqlalchemy import select

    if target.venue_id:
        org = connection.execute(
            select(Venue.organization_id).where(Venue.id == target.venue_id)
        ).scalar()
        if org:
            target.organization_id = org
            return
    if target.created_by:
        target.organization_id = _acting_org(connection, target.created_by)


class OrdermentumPair(Base):
    """The two copies of one Ordermentum delivery, once Norm has settled them:
    Ordermentum's own copy (Loaded ``source = Ordermentum``, no file) and the
    supplier's emailed invoice, which prints Ordermentum's order number.

    Two jobs (services/ordermentum_pairs, docs/plans/ordermentum-pairing.md):
    a copy already in a pair is never offered as anyone else's twin (that
    alone stopped most wrong pairs when a copy arrived late), and each
    supplier's number gap — its order number minus Ordermentum's invoice
    number, 450 for every Sonoma pair — is learned from these rows.

    ``status``: ``paired`` (settled, a duplicate raised), ``suspected`` (a
    steady supplier's number didn't line up but the goods did: held, nothing
    deleted), ``confirmed`` (a suspected pair proved right when the supplier's
    gap was seen to drift). Loaded ids carry no FK — a foreign system's keys.
    """

    __tablename__ = "ordermentum_pairs"
    __table_args__ = (
        UniqueConstraint(
            "ordermentum_invoice_id", "supplier_invoice_id", name="uq_ordermentum_pair"
        ),
    )

    id = Column(String, primary_key=True, default=_uuid)
    venue_id = Column(String, ForeignKey("venues.id"), nullable=True, index=True)
    #: The supplier's name, folded (``supplier_key``): the gap is the
    #: supplier's, the same at every venue.
    supplier_key = Column(String, nullable=False, index=True)
    supplier_name = Column(String, nullable=True)
    ordermentum_invoice_id = Column(String, nullable=False, index=True)
    ordermentum_number = Column(BigInteger, nullable=True)
    supplier_invoice_id = Column(String, nullable=False, index=True)
    supplier_order_number = Column(BigInteger, nullable=True)
    #: supplier_order_number − ordermentum_number, when both are known.
    gap = Column(Integer, nullable=True)
    #: The supplier's copy's date: the history is read in date order.
    delivered_on = Column(Date, nullable=True)
    status = Column(String, nullable=False, default="paired")
    #: How it was found: exact number | same goods | seed.
    how = Column(String, nullable=True)
    #: The copy received second — the one that goes (first received stays).
    later_invoice_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)


class InvoiceActivityEvent(Base):
    """An invoice Norm handled that neither went in nor was deleted: it stopped
    for a reason, or is waiting for a person to approve it. Received and
    deleted invoices are already recorded once each in
    ``invoice_autopilot_outcomes``; this holds the rest, so the invoice activity
    page can show every invoice Norm touched (owner, 19 Oct 2026).

    One row per invoice and kind, refreshed each time the invoice is reviewed
    again: ``created_at`` is when it first stopped, ``updated_at`` the latest.
    """

    __tablename__ = "invoice_activity_events"
    __table_args__ = (
        UniqueConstraint(
            "venue_id", "invoice_id", "kind", name="uq_invoice_activity_event"
        ),
    )

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, nullable=True, index=True)
    venue_id = Column(String, nullable=False, index=True)
    invoice_id = Column(String, nullable=False, index=True)
    reference_number = Column(String, nullable=True)
    supplier_name = Column(String, nullable=True)
    total = Column(Numeric(14, 2), nullable=True)
    #: stopped (Norm couldn't finish it) | waiting (ready, a person approves)
    #: | failed (Loaded refused the receive, or the review itself failed)
    kind = Column(String, nullable=False)
    #: The run that handled it: autopilot | approve_fixes | approve_all.
    mode = Column(String, nullable=True)
    actor = Column(String, nullable=False, default="norm")
    #: {reasons: [...], gates_needed: [...], level: {...}, activity: {...}}
    detail = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, index=True)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
