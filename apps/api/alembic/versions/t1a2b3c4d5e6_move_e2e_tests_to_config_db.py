"""move e2e_tests to config db

Drops the e2e_tests table from the main DB and the FK on e2e_test_runs.test_id.
E2ETest now lives in the shared config DB (see config_models.E2ETest); the
config table is created at startup via ConfigBase.metadata.create_all().

Revision ID: t1a2b3c4d5e6
Revises: s1a2b3c4d5e6
Create Date: 2026-04-19 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "t1a2b3c4d5e6"
down_revision = "s1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the cross-DB FK first so we can drop the referenced table
    with op.batch_alter_table("e2e_test_runs") as batch:
        batch.drop_constraint("e2e_test_runs_test_id_fkey", type_="foreignkey")
    op.drop_table("e2e_tests")


def downgrade() -> None:
    op.create_table(
        "e2e_tests",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("playwright_script", sa.Text(), nullable=False),
        sa.Column("steps_json", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.String(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("e2e_test_runs") as batch:
        batch.create_foreign_key(
            "e2e_test_runs_test_id_fkey", "e2e_tests", ["test_id"], ["id"]
        )
