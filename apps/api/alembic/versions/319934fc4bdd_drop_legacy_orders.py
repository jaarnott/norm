"""Drop the legacy draft-order tables (``orders``, ``order_lines``) — if it
can be done without blocking anyone.

They backed Norm's first stock-ordering flow — a draft order kept in Norm,
approved in the chat, then submitted — and the ``/api/orders`` endpoints that
listed them (removed with this migration, 17 Oct 2026). Stock orders are placed
from the purchase-order card straight to LoadedHub, and production never held a
single row in either table.

Revision ID: 319934fc4bdd
Revises: fb1c2d3e4f5a
"""

import logging
import time

import sqlalchemy as sa
from alembic import op

revision = "319934fc4bdd"
down_revision = "fb1c2d3e4f5a"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

_ATTEMPTS = 6
_LOCK_TIMEOUT = "2s"
_PAUSE_SECONDS = 3


def upgrade() -> None:
    """Dropping a table takes an ACCESS EXCLUSIVE lock on every table its
    foreign keys reference — threads, venues, suppliers, products. While it
    waits for one, every other query on the tables it already holds queues
    behind it. On 17 Oct 2026 the first version of this migration waited half
    an hour behind a transaction left idle in production, holding the app's
    venue and thread reads the whole time.

    So: ask for the locks briefly, a few times, and if they can't be had, leave
    the two empty, unread tables for a quieter deploy rather than block anyone.
    """
    conn = op.get_bind()
    outer = conn.execute(sa.text("SELECT current_setting('lock_timeout')")).scalar()
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            with conn.begin_nested():
                conn.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
                conn.execute(sa.text("DROP TABLE IF EXISTS order_lines"))
                conn.execute(sa.text("DROP TABLE IF EXISTS orders"))
            # the setting outlives the savepoint: give later migrations back the
            # run's own timeout (alembic/env.py)
            conn.execute(sa.text(f"SET LOCAL lock_timeout = '{outer}'"))
            return
        except sa.exc.OperationalError as e:
            if getattr(e.orig, "pgcode", None) != "55P03":  # lock_not_available
                raise
            log.warning(
                "orders tables are locked (attempt %s of %s) — trying again",
                attempt,
                _ATTEMPTS,
            )
            time.sleep(_PAUSE_SECONDS)
    log.warning(
        "orders / order_lines left in place: their locks were never free. "
        "They are empty and unread; drop them in a quiet deploy."
    )


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("orders"):
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
    if not sa.inspect(op.get_bind()).has_table("order_lines"):
        op.create_table(
            "order_lines",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column(
                "order_id", sa.String(), sa.ForeignKey("orders.id"), nullable=False
            ),
            sa.Column(
                "product_id", sa.String(), sa.ForeignKey("products.id"), nullable=False
            ),
            sa.Column("quantity_cases", sa.Integer()),
            sa.Column("quantity_units", sa.Integer()),
        )
