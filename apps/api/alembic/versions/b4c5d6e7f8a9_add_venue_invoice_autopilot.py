"""venues.invoice_autopilot — how far this venue lets Norm receive on its own

The receiving tier used to be a per-USER setting (`users.workflow_modes`), but
invoices belong to a venue and venues differ in how clean their Loaded
catalogue is: one can run on autopilot while another still approves every line.
This holds the tier plus the per-action toggles (create units / items / brands
/ suppliers, receive without a unit, receive without a valid PO).

NULL means "approve_all with every toggle off" — exactly today's behaviour, and
what Norm has actually been doing (no invoice has ever been auto-received), so
there is nothing to backfill.

Revision ID: b4c5d6e7f8a9
Revises: d6e7f8a9b0c1
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa

revision = "b4c5d6e7f8a9"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("venues", sa.Column("invoice_autopilot", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("venues", "invoice_autopilot")
