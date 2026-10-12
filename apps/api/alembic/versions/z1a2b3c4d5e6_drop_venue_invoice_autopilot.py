"""drop venues.invoice_autopilot — receiving settings are per person

How far Norm may go when receiving invoices moved from the venue to the person
on 6 Oct 2026 (users.approval_preferences, read through
services.approvals.receiving_settings); migration w8x9y0z1a2b3 copied each
venue's setting to its users. Nothing has read the venue column since. The
production values were saved before this ran
(~/norm-split-backups/venues-invoice-autopilot-20261011T210138Z.json on
norm-dev); the downgrade restores the column, not its values.

Revision ID: z1a2b3c4d5e6
Revises: y0z1a2b3c4d5
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op

revision = "z1a2b3c4d5e6"
down_revision = "y0z1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("venues", "invoice_autopilot")


def downgrade() -> None:
    op.add_column("venues", sa.Column("invoice_autopilot", sa.JSON(), nullable=True))
