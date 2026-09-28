"""Built-in apps: audit rows that don't point at an app row.

Norm Hiring and Norm Training are now built into Norm
(app/services/builtin_apps.py): their code ships with every deploy and they
have no ``apps`` row in any org. So ``app_calls`` — the app audit trail — can
no longer always point at an ``apps`` row: ``app_id`` becomes nullable and a
built-in is named by ``builtin_slug``. Every row also gets
``organization_id`` (backfilled from the app for existing rows): a built-in
row has no app to find its org through, and it must go when its org does
(CASCADE), as app rows' audit already does via ``apps``.

Deliberately schema-only. The per-org copies the old installer made (in
production, one org's ``hiring`` and ``training``) are NOT archived here: the
new code already ignores them everywhere and the runtime refuses their
``hr_suite`` storage, while archiving them in the same deploy would break
Hiring and Training for that org under a fast image-only rollback.

Revision ID: s4t5u6v7w8x9
Revises: r3s4t5u6v7w8
"""

import sqlalchemy as sa
from alembic import op

revision = "s4t5u6v7w8x9"
down_revision = "r3s4t5u6v7w8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("app_calls", "app_id", existing_type=sa.String(), nullable=True)
    op.add_column("app_calls", sa.Column("builtin_slug", sa.String(), nullable=True))
    op.add_column(
        "app_calls",
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_app_calls_builtin_slug", "app_calls", ["builtin_slug"])
    op.create_index("ix_app_calls_organization_id", "app_calls", ["organization_id"])
    op.execute(
        "UPDATE app_calls SET organization_id = apps.organization_id "
        "FROM apps WHERE apps.id = app_calls.app_id"
    )
    op.create_check_constraint(
        "ck_app_calls_names_an_app",
        "app_calls",
        "app_id IS NOT NULL OR builtin_slug IS NOT NULL",
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_calls WHERE app_id IS NULL")
    op.drop_constraint("ck_app_calls_names_an_app", "app_calls", type_="check")
    op.drop_index("ix_app_calls_organization_id", table_name="app_calls")
    op.drop_index("ix_app_calls_builtin_slug", table_name="app_calls")
    op.drop_column("app_calls", "organization_id")
    op.drop_column("app_calls", "builtin_slug")
    op.alter_column("app_calls", "app_id", existing_type=sa.String(), nullable=False)
