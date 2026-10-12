"""Built-in apps can run on a schedule and send email.

* ``app_schedule_runs`` — one row per (org, app, op, slot); its unique key is
  the once-per-slot guard for scheduled runs (services/app_schedules.py).
* ``app_email_settings`` — per org, per app: may this app send email? Off
  unless an org admin turns it on (services/app_email.py).
* ``email_logs`` gains ``app_slug``, ``idempotency_key`` and ``is_test`` so an
  app's emails are attributable and a re-run never sends the same one twice.

Revision ID: z1a2b3c4d5e6
Revises: y0z1a2b3c4d5
"""

import sqlalchemy as sa
from alembic import op

revision = "z1a2b3c4d5e6"
down_revision = "y0z1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_schedule_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("app_slug", sa.String(), nullable=False),
        sa.Column("op", sa.String(), nullable=False),
        sa.Column("slot", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("ok", sa.Boolean()),
        sa.Column("error", sa.Text()),
        sa.Column("summary", sa.JSON()),
        sa.UniqueConstraint(
            "organization_id", "app_slug", "op", "slot", name="uq_app_schedule_slot"
        ),
    )
    op.create_table(
        "app_email_settings",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("app_slug", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "updated_by",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("organization_id", "app_slug", name="uq_app_email_setting"),
    )
    op.add_column("email_logs", sa.Column("app_slug", sa.String(), nullable=True))
    op.add_column("email_logs", sa.Column("idempotency_key", sa.String(), nullable=True))
    op.add_column(
        "email_logs",
        sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_email_logs_app_slug", "email_logs", ["app_slug"])
    # The idempotency guard, enforced by the database: one email being sent
    # or sent per (org, app, key). Partial, so failed attempts can be retried.
    op.create_index(
        "uq_email_logs_app_key",
        "email_logs",
        ["organization_id", "app_slug", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text(
            "idempotency_key IS NOT NULL AND status IN ('sending', 'sent') AND is_test = false"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_email_logs_app_key", table_name="email_logs")
    op.drop_index("ix_email_logs_app_slug", table_name="email_logs")
    op.drop_column("email_logs", "is_test")
    op.drop_column("email_logs", "idempotency_key")
    op.drop_column("email_logs", "app_slug")
    op.drop_table("app_email_settings")
    op.drop_table("app_schedule_runs")
