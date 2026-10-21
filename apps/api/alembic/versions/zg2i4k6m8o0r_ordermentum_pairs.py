"""The two copies of one Ordermentum delivery, once settled.

Norm learns each supplier's number gap (its order number minus Ordermentum's
invoice number) from these rows, and never offers a copy that already has its
partner as anyone else's twin (docs/plans/ordermentum-pairing.md).

Revision ID: zg2i4k6m8o0r
Revises: yf1h3j5l7n9q
"""

import sqlalchemy as sa
from alembic import op

revision = "zg2i4k6m8o0r"
down_revision = "yf1h3j5l7n9q"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ordermentum_pairs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("venue_id", sa.String(), sa.ForeignKey("venues.id"), nullable=True),
        sa.Column("supplier_key", sa.String(), nullable=False),
        sa.Column("supplier_name", sa.String(), nullable=True),
        sa.Column("ordermentum_invoice_id", sa.String(), nullable=False),
        sa.Column("ordermentum_number", sa.BigInteger(), nullable=True),
        sa.Column("supplier_invoice_id", sa.String(), nullable=False),
        sa.Column("supplier_order_number", sa.BigInteger(), nullable=True),
        sa.Column("gap", sa.Integer(), nullable=True),
        sa.Column("delivered_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="paired"),
        sa.Column("how", sa.String(), nullable=True),
        sa.Column("later_invoice_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "ordermentum_invoice_id", "supplier_invoice_id", name="uq_ordermentum_pair"
        ),
    )
    op.create_index("ix_ordermentum_pairs_venue_id", "ordermentum_pairs", ["venue_id"])
    op.create_index(
        "ix_ordermentum_pairs_supplier_key", "ordermentum_pairs", ["supplier_key"]
    )
    op.create_index(
        "ix_ordermentum_pairs_ordermentum_invoice_id",
        "ordermentum_pairs",
        ["ordermentum_invoice_id"],
    )
    op.create_index(
        "ix_ordermentum_pairs_supplier_invoice_id",
        "ordermentum_pairs",
        ["supplier_invoice_id"],
    )


def downgrade() -> None:
    op.drop_table("ordermentum_pairs")
