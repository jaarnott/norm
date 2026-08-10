"""Add code_verifier to oauth_states (PKCE)

OAuth 2.1 public clients (e.g. the Cook Brothers App MCP connector) use PKCE:
the code_verifier is generated when the authorize URL is built and must survive
the redirect round-trip to be replayed on token exchange. Store it alongside the
single-use state row. Nullable — legacy/confidential-client flows leave it null.

Revision ID: a8b9c0d1e2f3
Revises: z7a8b9c0d1e2
Create Date: 2026-08-10 02:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "a8b9c0d1e2f3"
down_revision = "z7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "oauth_states",
        sa.Column("code_verifier", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("oauth_states", "code_verifier")
