"""Drop Thread.playbook_id.

Added with playbooks (p1a2b3c4d5e6) so a follow-up could reuse the router's
playbook, but nothing ever wrote or read it — and since Sep 2026 the router
picks no playbook at all: the agent opens one on demand (norm__read_playbook).
The ORM stopped mapping the column in d9e1788, which deployed first, so no
running revision selects it when this migration runs.

Revision ID: r3s4t5u6v7w8
Revises: q2r3s4t5u6v7
"""

import sqlalchemy as sa
from alembic import op

revision = "r3s4t5u6v7w8"
down_revision = "q2r3s4t5u6v7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("threads", "playbook_id")


def downgrade() -> None:
    op.add_column("threads", sa.Column("playbook_id", sa.String, nullable=True))
