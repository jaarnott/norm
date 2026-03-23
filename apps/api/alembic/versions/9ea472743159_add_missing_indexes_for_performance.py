"""add_missing_indexes_for_performance

Revision ID: 9ea472743159
Revises: d33b87cfd58b
Create Date: 2026-03-23 00:30:23.317411

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '9ea472743159'
down_revision: Union[str, Sequence[str], None] = 'd33b87cfd58b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_tool_calls_task_id", "tool_calls", ["task_id"])
    op.create_index("ix_tool_calls_connector_name", "tool_calls", ["connector_name"])
    op.create_index("ix_llm_calls_task_id", "llm_calls", ["task_id"])
    op.create_index("ix_messages_task_id", "messages", ["task_id"])
    op.create_index("ix_orders_task_id", "orders", ["task_id"])
    op.create_index("ix_approvals_task_id", "approvals", ["task_id"])
    op.create_index("ix_tasks_venue_id", "tasks", ["venue_id"])
    op.create_index("ix_working_documents_venue_id", "working_documents", ["venue_id"])
    op.create_index("ix_automated_task_runs_automated_task_id", "automated_task_runs", ["automated_task_id"])


def downgrade() -> None:
    op.drop_index("ix_automated_task_runs_automated_task_id", "automated_task_runs")
    op.drop_index("ix_working_documents_venue_id", "working_documents")
    op.drop_index("ix_tasks_venue_id", "tasks")
    op.drop_index("ix_approvals_task_id", "approvals")
    op.drop_index("ix_orders_task_id", "orders")
    op.drop_index("ix_messages_task_id", "messages")
    op.drop_index("ix_llm_calls_task_id", "llm_calls")
    op.drop_index("ix_tool_calls_connector_name", "tool_calls")
    op.drop_index("ix_tool_calls_task_id", "tool_calls")
