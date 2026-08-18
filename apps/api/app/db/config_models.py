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
    LargeBinary,
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


class Playbook(ConfigBase):
    __tablename__ = "playbooks"

    id = Column(String, primary_key=True, default=_uuid)
    slug = Column(String, unique=True, nullable=False)
    agent_slug = Column(String, nullable=False)
    display_name = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    instructions = Column(Text, nullable=False)
    tool_filter = Column(JSON, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class SupplierInvoiceSpec(ConfigBase):
    """Per-supplier invoice-extraction instructions, admin-maintained.

    A supplier's printed invoices can have quirks the generic extraction rules
    can't express (split quantity columns, odd unit notation). The review
    engine matches the invoice's supplierName against ``name``/``aliases``
    (normalized, substring) and appends ``instructions`` to the PDF-extraction
    prompt — deterministic, no extra LLM turns, and the extraction cache keys
    on instructions so an edited spec re-extracts automatically. Aliases exist
    because suppliers appear under variant names ("Service Foods" vs
    "Service Foods Auckland"). Extraction-scope ONLY: review checks and gates
    stay identical for every supplier.
    """

    __tablename__ = "supplier_invoice_specs"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, unique=True, nullable=False)
    aliases = Column(JSON, nullable=False, default=list)
    instructions = Column(Text, nullable=False, default="")
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class SupplierSpecSample(ConfigBase):
    """A sample invoice PDF + expected extraction for a supplier spec (Dojo).

    Admin-uploaded regression fixtures: each sample stores the PDF bytes and,
    once an admin has reviewed a run and accepted it, the expected extraction.
    Every subsequent run re-extracts with the CURRENT prompts and diffs
    against ``expected`` — so a prompt edit can be regression-tested from the
    Supplier Specs screen before it misreads a real invoice. Site-wide like
    the specs themselves (shared config DB); the PDFs are small (~30–200KB)
    and few, so bytes live inline rather than in object storage.
    """

    __tablename__ = "supplier_spec_samples"

    id = Column(String, primary_key=True, default=_uuid)
    spec_id = Column(String, nullable=False, index=True)
    label = Column(String, nullable=False, default="")
    content_type = Column(String, nullable=False, default="application/pdf")
    pdf_bytes = Column(LargeBinary, nullable=False)
    # The admin-accepted baseline extraction; null until first accepted.
    expected = Column(JSON, nullable=True)
    # Latest run: {"extraction": {...}, "diffs": [...]} — viewable without
    # re-running (each run costs an LLM extraction).
    last_run = Column(JSON, nullable=True)
    last_status = Column(String, nullable=False, default="new")
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    # Where the sample came from (Add-to-Dojo on an invoice card) — lets the
    # analysis agent fetch the Loaded draft's structured lines as an
    # independent reference reading of the same paper. Null for hand uploads.
    # NOTE: added after first ship — created via guarded ALTERs in
    # main._ensure_config_tables (the config DB has no Alembic).
    source_venue_id = Column(String, nullable=True)
    source_invoice_id = Column(String, nullable=True)
    # The Loaded COMPANY the source venue was bound to at filing time. Venue
    # ids are per-environment (the config DB is shared but venues live in
    # each env's main DB), so a sample filed in production is unreadable by
    # local's venue id — the company id is the env-independent key that lets
    # any environment resolve "which of MY venues talks to this company"
    # (16 Aug 2026: every prod-filed sample failed its replica build locally
    # with "loadedhub not connected for venue <prod-id>").
    source_company_id = Column(String, nullable=True)
    # The analysis agent's latest output: {status, rationale,
    # proposed_instructions, ground_truth, candidate_results, green, model, at}.
    analysis = Column(JSON, nullable=True)
    # Admin-blessed replica values — adjudication when Loaded's own
    # resolution (the replica scorecard's ground truth) is itself wrong.
    expected_replica = Column(JSON, nullable=True)
    # Dojo-page triage staging: a draft sample gets the full toolkit (run /
    # analyse / apply) but is EXCLUDED from the per-spec lists, Run Dojo and
    # the summary until promoted (draft -> False) via "Keep as sample".
    draft = Column(Boolean, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class McpCapability(ConfigBase):
    """Curation for Norm's outward-facing MCP surface.

    A thin enable+scope layer, nothing more. This table holds **no schema** —
    MCP tool schemas always project from ``ConnectorSpec.tools`` / ``Playbook``
    at request time, so a capability can never drift from the definition it
    exposes. Adding a connector or playbook makes it a *candidate* the moment
    it exists; a missing row means "not exposed" (fail closed).

    What is deliberately NOT a column here:

    - **access (read/draft)** — derived from the tool's ``method``, so it can't
      be mis-set. A non-GET action can only be exposed via a playbook.
    - **the scope vocabulary** — ``scopes`` picks from ``app.mcp.scopes``;
      unknown values are rejected on write. The vocabulary itself, and its
      mapping onto org permission scopes, stays in code.

    Those two rules are what keep "v1 is read + draft only" a property of the
    system rather than a promise. See app/routers/mcp_admin.py for the
    write-time validation and app/services/config_validator.py for the daily
    drift check.
    """

    __tablename__ = "mcp_capabilities"
    __table_args__ = (
        UniqueConstraint("kind", "target", "action", name="uq_mcp_capability"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    kind = Column(String, nullable=False)  # "connector" | "playbook"
    target = Column(String, nullable=False)  # connector_name | playbook.slug
    action = Column(String, nullable=False, default="")  # "" for playbooks
    scopes = Column(JSON, nullable=False, default=list)  # ⊆ app.mcp.scopes
    description_override = Column(Text, nullable=True)  # prose only, never schema
    tool_name_override = Column(String, nullable=True)  # public alias
    enabled = Column(Boolean, nullable=False, default=False)  # fail closed
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class DashboardTemplate(ConfigBase):
    __tablename__ = "dashboard_templates"

    id = Column(String, primary_key=True, default=_uuid)
    slug = Column(String, unique=True, nullable=False)
    agent_slug = Column(String, nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    charts = Column(JSON, nullable=False, default=list)
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


class E2ETest(ConfigBase):
    """E2E test definitions — shared across all environments.

    Stored in the config DB so a test written once runs everywhere. Run
    history lives in the main DB per-env (see E2ETestRun in models.py).
    """

    __tablename__ = "e2e_tests"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    playwright_script = Column(Text, nullable=False)
    steps_json = Column(JSON, default=list)
    # created_by is a plain string (user email or id) — no DB-level FK since
    # users live in the main DB.
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    last_run_status = Column(String, nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)


class SupplierProduct(ConfigBase):
    """Norm's own supplier-product catalogue — global physical facts.

    Keyed by (supplier_key, code): Trents code 4230513 is the same 700ml
    bottle at every venue, so what one delivered item IS lives here once,
    venue-independent. ``supplier_key`` is the SupplierInvoiceSpec name — the
    spec registry is deliberately the cross-venue supplier identity gate.

    Truth never comes from venue practice (all venues can share one mistake —
    a venue stocking a syrup as Each is an error, not a preference).
    ``provenance`` ranks where the current unit came from and lower tiers can
    NEVER overwrite higher ones:
      human    — a Norm admin verified it (dojo baselines seed this)
      printed  — the supplier printed the size on an invoice page
      enriched — LLM/world-knowledge enrichment (later phase)
      practice — what venues chose at receive time (advisory only, later)

    ``pack_type`` keeps food honest: 'fixed' (one physical size — resolvable
    from here), 'random_weight' (meat/produce — the unit is Kilo, always),
    'variable' (case sizes change — read each invoice; this table only
    contributes the type), 'unknown'. Conflicting evidence within a tier is
    a QUESTION (unit_name null, both sightings kept in ``evidence``), never
    a majority vote.
    """

    __tablename__ = "supplier_products"
    __table_args__ = (
        UniqueConstraint("supplier_key", "code", name="uq_supplier_product"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    supplier_key = Column(String, nullable=False, index=True)
    code = Column(String, nullable=False)
    # Latest printed description — display + drift detection (a code whose
    # description stops resembling this one has been REUSED by the supplier).
    description = Column(String, nullable=False, default="")
    pack_type = Column(String, nullable=False, default="unknown")
    # The physical delivered unit as printed (e.g. '700ml', '5x3kg') — null
    # while unknown or in conflict. Compare via invoice_units._unit_norm.
    unit_name = Column(String, nullable=True)
    unit_type = Column(String, nullable=True)  # volume | weight | count
    category = Column(String, nullable=False, default="unknown")
    provenance = Column(String, nullable=False, default="printed")
    # {"printed": {"<unit_norm>": {"count": N, "invoices": [...], "name": ...}},
    #  "human": {...}, "descriptions": [...], "count_only": N}
    evidence = Column(JSON, nullable=False, default=dict)
    first_seen = Column(DateTime(timezone=True), default=_now)
    last_seen = Column(DateTime(timezone=True), default=_now)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
