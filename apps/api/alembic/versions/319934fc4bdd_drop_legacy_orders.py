"""Drop the legacy draft-order tables (``orders``, ``order_lines``).

They backed Norm's first stock-ordering flow — a draft order kept in Norm,
approved in the chat, then submitted — and the ``/api/orders`` endpoints that
listed them (removed with this migration, 17 Oct 2026). Stock orders are placed
from the purchase-order card straight to LoadedHub, and production never held a
single row in either table.

Revision ID: 319934fc4bdd
Revises: fb1c2d3e4f5a
"""

import sqlalchemy as sa
from alembic import op

revision = "319934fc4bdd"
down_revision = "fb1c2d3e4f5a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("order_lines")
    op.drop_table("orders")


def downgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "thread_id", sa.String(), sa.ForeignKey("threads.id"), nullable=False
        ),
        sa.Column("venue_id", sa.String(), sa.ForeignKey("venues.id")),
        sa.Column("supplier_id", sa.String(), sa.ForeignKey("suppliers.id")),
        sa.Column("status", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "order_lines",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column(
            "product_id", sa.String(), sa.ForeignKey("products.id"), nullable=False
        ),
        sa.Column("quantity_cases", sa.Integer()),
        sa.Column("quantity_units", sa.Integer()),
    )
