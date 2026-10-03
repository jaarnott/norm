"""A conversation's notebook: files behind Anthropic's memory tool

One row per (thread, path). The model requests file operations; Norm executes
them against this table. Scoped to the thread and gone with it (ON DELETE
CASCADE) — working notes for a job, not long-term memory, which stays in
`memories` with its admission rules.

Revision ID: v7w8x9y0z1a2
Revises: u6v7w8x9y0z1
Create Date: 2026-10-03 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "v7w8x9y0z1a2"
down_revision = "u6v7w8x9y0z1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "thread_memory_files",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id", "path"),
    )
    op.create_index(
        "ix_thread_memory_files_thread_id", "thread_memory_files", ["thread_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_thread_memory_files_thread_id", table_name="thread_memory_files")
    op.drop_table("thread_memory_files")
