"""add mcp oauth and audit tables

Revision ID: 379f84a5e8af
Revises: t1a2b3c4d5e6
Create Date: 2026-07-18 01:38:55.585880

Hand-trimmed after autogenerate: the config-DB models (connector_specs,
playbooks, mcp_capabilities, ...) are registered on Base.metadata via the
re-export in app/db/models.py but physically live in the config DB, so
autogenerate wanted to drop them. It also surfaced pre-existing schema drift
(reports FK, working_documents index) unrelated to this change. All of that is
removed — this migration only creates the six MCP OAuth/audit tables.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "379f84a5e8af"
down_revision: Union[str, Sequence[str], None] = "t1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_clients",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("client_secret_hash", sa.String(), nullable=True),
        sa.Column("client_name", sa.String(), nullable=False),
        sa.Column("client_uri", sa.String(), nullable=True),
        sa.Column("logo_uri", sa.String(), nullable=True),
        sa.Column("redirect_uris", sa.JSON(), nullable=False),
        sa.Column("grant_types", sa.JSON(), nullable=False),
        sa.Column("response_types", sa.JSON(), nullable=False),
        sa.Column("token_endpoint_auth_method", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_mcp_clients_client_id"), "mcp_clients", ["client_id"], unique=True
    )

    op.create_table(
        "mcp_rate_limits",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("bucket_key", sa.String(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bucket_key", "window_start", name="uq_mcp_rl_window"),
    )
    op.create_index(
        "ix_mcp_rl_window", "mcp_rate_limits", ["window_start"], unique=False
    )

    op.create_table(
        "mcp_authorization_codes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_ids", sa.JSON(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("redirect_uri", sa.String(), nullable=False),
        sa.Column("resource", sa.String(), nullable=True),
        sa.Column("code_challenge", sa.String(), nullable=False),
        sa.Column("code_challenge_method", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["mcp_clients.client_id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_mcp_authorization_codes_code_hash"),
        "mcp_authorization_codes",
        ["code_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_mcp_authorization_codes_expires_at"),
        "mcp_authorization_codes",
        ["expires_at"],
        unique=False,
    )

    op.create_table(
        "mcp_grants",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_ids", sa.JSON(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["mcp_clients.client_id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "client_id", "organization_id", name="uq_mcp_grant"
        ),
    )
    op.create_index(
        op.f("ix_mcp_grants_client_id"), "mcp_grants", ["client_id"], unique=False
    )
    op.create_index(
        op.f("ix_mcp_grants_user_id"), "mcp_grants", ["user_id"], unique=False
    )

    op.create_table(
        "mcp_audit_log",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("client_name", sa.String(), nullable=False),
        sa.Column("token_id", sa.String(), nullable=True),
        sa.Column("grant_id", sa.String(), nullable=True),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=True),
        sa.Column("capability", sa.String(), nullable=False),
        sa.Column("access_level", sa.String(), nullable=False),
        sa.Column("scopes_used", sa.JSON(), nullable=False),
        sa.Column("record_type", sa.String(), nullable=True),
        sa.Column("record_id", sa.String(), nullable=True),
        sa.Column("record_ids", sa.JSON(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("arguments_redacted", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["venue_id"], ["venues.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_mcp_audit_log_created_at"),
        "mcp_audit_log",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mcp_audit_log_request_id"),
        "mcp_audit_log",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_mcp_audit_log_token_id"), "mcp_audit_log", ["token_id"], unique=False
    )
    op.create_index(
        op.f("ix_mcp_audit_log_user_id"), "mcp_audit_log", ["user_id"], unique=False
    )
    op.create_index(
        "ix_mcp_audit_org_time",
        "mcp_audit_log",
        ["organization_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_mcp_audit_record",
        "mcp_audit_log",
        ["record_type", "record_id"],
        unique=False,
    )
    op.create_index(
        "ix_mcp_audit_user_time",
        "mcp_audit_log",
        ["user_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "mcp_tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("grant_id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_ids", sa.JSON(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("audience", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parent_token_id", sa.String(), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["mcp_clients.client_id"]),
        sa.ForeignKeyConstraint(["grant_id"], ["mcp_grants.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["parent_token_id"], ["mcp_tokens.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_mcp_tokens_expires_at"), "mcp_tokens", ["expires_at"], unique=False
    )
    op.create_index(
        "ix_mcp_tokens_grant_kind", "mcp_tokens", ["grant_id", "kind"], unique=False
    )
    op.create_index(
        op.f("ix_mcp_tokens_token_hash"), "mcp_tokens", ["token_hash"], unique=True
    )
    op.create_index(
        op.f("ix_mcp_tokens_user_id"), "mcp_tokens", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("mcp_tokens")
    op.drop_table("mcp_audit_log")
    op.drop_table("mcp_grants")
    op.drop_table("mcp_authorization_codes")
    op.drop_table("mcp_rate_limits")
    op.drop_table("mcp_clients")
