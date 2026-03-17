"""rename operations to tools and add tool_calls table

Revision ID: g1a2b3c4d5e6
Revises: f1a2b3c4d5e6
Create Date: 2026-03-15 16:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "g1a2b3c4d5e6"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Phase 1: Rename operations -> tools column
    op.alter_column("connector_specs", "operations", new_column_name="tools")

    # Phase 2: Add tool_calls table
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("task_id", sa.String(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("llm_call_id", sa.String(), sa.ForeignKey("llm_calls.id"), nullable=True),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("connector_name", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("input_params", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("rendered_request", sa.JSON(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Phase 2: Add new columns to tasks
    op.add_column("tasks", sa.Column("agent_loop_state", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("pending_tool_call_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "pending_tool_call_ids")
    op.drop_column("tasks", "agent_loop_state")
    op.drop_table("tool_calls")
    op.alter_column("connector_specs", "tools", new_column_name="operations")
