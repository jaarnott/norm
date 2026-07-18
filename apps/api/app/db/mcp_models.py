"""OAuth 2.1 authorization-server and audit tables for the MCP surface.

These live in the **main per-environment database**, not the shared config DB.
The config DB is one physical database shared by local/testing/staging/
production — a token minted on a dev laptop would otherwise be a valid
credential against production. These tables also FK to ``users`` /
``organizations`` / ``venues``, which are per-environment. (Curation is the
opposite case — it's env-invariant *definition*, so ``McpCapability`` correctly
lives in the config DB.)

Everything here is on ``Base`` and imported into ``app.db.models`` so
``alembic/env.py`` (which reads ``Base.metadata``) generates migrations for it.

Token hashing: SHA-256 for tokens and codes, bcrypt for client secrets. Tokens
are 256-bit CSPRNG values, so a single SHA-256 is cryptographically sufficient
and — unlike bcrypt — indexable, which matters because a bearer token is the
only lookup key you have. bcrypt's ~250ms cost per verify on every request
would also block the event loop. Client secrets are the reverse case (verified
once an hour at /token), so bcrypt fits.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)

from app.db.models import Base, _now, _uuid


class McpClient(Base):
    """An OAuth client, dynamically registered via RFC 7591 (e.g. claude.ai)."""

    __tablename__ = "mcp_clients"

    id = Column(String, primary_key=True, default=_uuid)
    client_id = Column(String, unique=True, nullable=False, index=True)
    # bcrypt hash; NULL for public clients (claude.ai is public + PKCE).
    client_secret_hash = Column(String, nullable=True)
    client_name = Column(String, nullable=False)
    client_uri = Column(String, nullable=True)
    logo_uri = Column(String, nullable=True)
    redirect_uris = Column(JSON, nullable=False, default=list)  # exact-match only
    grant_types = Column(
        JSON, nullable=False, default=lambda: ["authorization_code", "refresh_token"]
    )
    response_types = Column(JSON, nullable=False, default=lambda: ["code"])
    token_endpoint_auth_method = Column(String, nullable=False, default="none")
    scope = Column(String, nullable=True)  # space-delimited requested set
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)


class McpAuthorizationCode(Base):
    """A single-use authorization code with its PKCE challenge. TTL ~60s."""

    __tablename__ = "mcp_authorization_codes"

    id = Column(String, primary_key=True, default=_uuid)
    code_hash = Column(String, unique=True, nullable=False, index=True)  # sha256 hex
    client_id = Column(String, ForeignKey("mcp_clients.client_id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    venue_ids = Column(JSON, nullable=False, default=list)  # consented subset
    scopes = Column(JSON, nullable=False, default=list)  # granted subset
    redirect_uri = Column(String, nullable=False)  # must match again at /token
    resource = Column(String, nullable=True)  # RFC 8707 resource indicator
    code_challenge = Column(String, nullable=False)
    code_challenge_method = Column(String, nullable=False, default="S256")
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at = Column(DateTime(timezone=True), nullable=True)  # single-use marker
    created_at = Column(DateTime(timezone=True), default=_now)


class McpGrant(Base):
    """A durable consent record: this user granted this client this access.

    Unique per (user, client, org). Refresh re-derives scopes/venues from this
    row against the user's *current* role, so revoking here takes effect on the
    next refresh rather than waiting out a token's lifetime.
    """

    __tablename__ = "mcp_grants"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "client_id", "organization_id", name="uq_mcp_grant"
        ),
    )

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    client_id = Column(
        String, ForeignKey("mcp_clients.client_id"), nullable=False, index=True
    )
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    venue_ids = Column(JSON, nullable=False, default=list)
    scopes = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class McpToken(Base):
    """An opaque access or refresh token. One row per issued token.

    Opaque, not JWT: a JWT signed with JWT_SECRET would authenticate against
    every /api route via decode_access_token's missing type check. An opaque
    token can't be fed to jwt.decode at all, so that whole failure class is
    structurally unreachable. Opaqueness also makes revocation immediate.

    org/venue/scope are frozen at issuance; refresh re-derives from the grant.
    """

    __tablename__ = "mcp_tokens"
    __table_args__ = (Index("ix_mcp_tokens_grant_kind", "grant_id", "kind"),)

    id = Column(String, primary_key=True, default=_uuid)  # the `tid`
    token_hash = Column(String, unique=True, nullable=False, index=True)  # sha256 hex
    kind = Column(String, nullable=False)  # "access" | "refresh"
    grant_id = Column(String, ForeignKey("mcp_grants.id"), nullable=False)
    client_id = Column(String, ForeignKey("mcp_clients.client_id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    venue_ids = Column(JSON, nullable=False, default=list)  # frozen at issuance
    scopes = Column(JSON, nullable=False, default=list)  # frozen at issuance
    audience = Column(String, nullable=True)  # RFC 8707 resource
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    # Refresh rotation lineage — reuse detection (RFC 9700 §4.14.2).
    parent_token_id = Column(String, ForeignKey("mcp_tokens.id"), nullable=True)
    rotated_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now)


class McpAuditLog(Base):
    """Append-only record of what an external client did on a user's behalf.

    Greenfield — Norm has no audit table. client_id/token_id carry NO foreign
    keys so the trail survives token GC and client deletion; client_name is
    denormalised so it stays readable after a rename.
    """

    __tablename__ = "mcp_audit_log"
    __table_args__ = (
        Index("ix_mcp_audit_org_time", "organization_id", "created_at"),
        Index("ix_mcp_audit_user_time", "user_id", "created_at"),
        Index("ix_mcp_audit_record", "record_type", "record_id"),
    )

    id = Column(String, primary_key=True, default=_uuid)

    # WHO
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    client_id = Column(String, nullable=False)  # no FK: outlives client deletion
    client_name = Column(String, nullable=False)  # denormalised: outlives rename
    token_id = Column(String, nullable=True, index=True)  # mcp_tokens.id; no FK
    grant_id = Column(String, nullable=True)

    # WHERE
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    venue_id = Column(String, ForeignKey("venues.id"), nullable=True)

    # WHAT
    capability = Column(String, nullable=False)  # "get_orders", "mcp.consent.granted"
    access_level = Column(String, nullable=False)  # "read" | "draft" | "write"
    scopes_used = Column(JSON, nullable=False, default=list)

    # WHICH BUSINESS RECORD
    record_type = Column(String, nullable=True)  # "order_draft", "report", "shift"
    record_id = Column(String, nullable=True)
    record_ids = Column(JSON, nullable=True)  # bulk reads: ids touched

    # OUTCOME
    success = Column(Boolean, nullable=False)
    error_code = Column(String, nullable=True)  # "insufficient_scope", "venue_denied"
    error_message = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    # CONTEXT
    request_id = Column(String, nullable=True, index=True)  # from request_tracing
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    arguments_redacted = Column(JSON, nullable=True)  # allowlisted arg keys only

    created_at = Column(
        DateTime(timezone=True), default=_now, nullable=False, index=True
    )


class McpRateLimit(Base):
    """Fixed-window rate-limit counters, keyed per token/user/org/IP.

    Postgres, not Redis: the atomic ``INSERT ... ON CONFLICT DO UPDATE ...
    RETURNING count`` upsert is correct across any number of Cloud Run
    instances with no coordination, and adds no new infra. GC'd by the same
    /internal/mcp-gc job that cleans expired codes and tokens.
    """

    __tablename__ = "mcp_rate_limits"
    __table_args__ = (
        UniqueConstraint("bucket_key", "window_start", name="uq_mcp_rl_window"),
        Index("ix_mcp_rl_window", "window_start"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    bucket_key = Column(String, nullable=False)  # "tok:<id>:call", "ip:<ip>:token", ...
    window_start = Column(DateTime(timezone=True), nullable=False)
    count = Column(Integer, nullable=False, default=0)
