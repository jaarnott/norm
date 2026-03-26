"""Rename tasks to threads

Revision ID: c3d4e5f6g7h8
Revises: b40fcd1e0b8f
Create Date: 2026-03-26 00:00:00.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "c3d4e5f6g7h8"
down_revision = "b40fcd1e0b8f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Rename the main table
    op.rename_table("tasks", "threads")

    # 2. Rename task_id FK columns across child tables
    op.alter_column("messages", "task_id", new_column_name="thread_id")
    op.alter_column("orders", "task_id", new_column_name="thread_id")
    op.alter_column("approvals", "task_id", new_column_name="thread_id")
    op.alter_column("integration_runs", "task_id", new_column_name="thread_id")
    op.alter_column("llm_calls", "task_id", new_column_name="thread_id")
    op.alter_column("tool_calls", "task_id", new_column_name="thread_id")
    op.alter_column("working_documents", "task_id", new_column_name="thread_id")
    op.alter_column("hr_setups", "task_id", new_column_name="thread_id")
    op.alter_column("automated_task_runs", "task_id", new_column_name="thread_id")
    op.alter_column("reports", "task_id", new_column_name="thread_id") if _column_exists("reports", "task_id") else None
    op.alter_column("report_charts", "source_task_id", new_column_name="source_thread_id")
    op.alter_column("email_logs", "task_id", new_column_name="thread_id")

    # 3. Rename conversation_task_id on automated_tasks
    op.alter_column("automated_tasks", "conversation_task_id", new_column_name="conversation_thread_id")

    # 4. Rename indexes on the threads table
    op.execute("ALTER INDEX IF EXISTS ix_tasks_user_id RENAME TO ix_threads_user_id")
    op.execute("ALTER INDEX IF EXISTS ix_tasks_session_id RENAME TO ix_threads_session_id")


def downgrade() -> None:
    # Reverse all renames
    op.rename_table("threads", "tasks")

    op.alter_column("messages", "thread_id", new_column_name="task_id")
    op.alter_column("orders", "thread_id", new_column_name="task_id")
    op.alter_column("approvals", "thread_id", new_column_name="task_id")
    op.alter_column("integration_runs", "thread_id", new_column_name="task_id")
    op.alter_column("llm_calls", "thread_id", new_column_name="task_id")
    op.alter_column("tool_calls", "thread_id", new_column_name="task_id")
    op.alter_column("working_documents", "thread_id", new_column_name="task_id")
    op.alter_column("hr_setups", "thread_id", new_column_name="task_id")
    op.alter_column("automated_task_runs", "thread_id", new_column_name="task_id")
    op.alter_column("report_charts", "source_thread_id", new_column_name="source_task_id")
    op.alter_column("email_logs", "thread_id", new_column_name="task_id")

    op.alter_column("automated_tasks", "conversation_thread_id", new_column_name="conversation_task_id")

    op.execute("ALTER INDEX IF EXISTS ix_threads_user_id RENAME TO ix_tasks_user_id")
    op.execute("ALTER INDEX IF EXISTS ix_threads_session_id RENAME TO ix_tasks_session_id")


def _column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists (used for optional renames)."""
    from alembic import op
    from sqlalchemy import inspect
    conn = op.get_bind()
    insp = inspect(conn)
    columns = [c["name"] for c in insp.get_columns(table_name)]
    return column_name in columns
