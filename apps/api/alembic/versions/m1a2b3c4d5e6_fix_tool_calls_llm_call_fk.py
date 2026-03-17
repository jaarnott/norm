"""fix tool_calls.llm_call_id foreign key to SET NULL on delete

Revision ID: m1a2b3c4d5e6
Revises: l1a2b3c4d5e6
Create Date: 2026-03-17 12:00:00.000000
"""
from alembic import op

revision = "m1a2b3c4d5e6"
down_revision = "l1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("tool_calls_llm_call_id_fkey", "tool_calls", type_="foreignkey")
    op.create_foreign_key(
        "tool_calls_llm_call_id_fkey", "tool_calls", "llm_calls",
        ["llm_call_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("tool_calls_llm_call_id_fkey", "tool_calls", type_="foreignkey")
    op.create_foreign_key(
        "tool_calls_llm_call_id_fkey", "tool_calls", "llm_calls",
        ["llm_call_id"], ["id"],
    )
