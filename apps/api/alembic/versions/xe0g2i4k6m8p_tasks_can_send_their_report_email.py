"""Tasks made in chat can send their report email.

Task creation added "send_email" to every new task's tool list as its email
tool, but the report email is ``norm_email.send_report_email`` ("send_email" is
another tool — sending from a connected Gmail). So a task made in chat could
not email: an hourly invoice task's first run reported "the email tool wasn't
available" (20 Oct 2026). Adds send_report_email to every task whose list was
given "send_email" and lacks it. Nothing is removed.

Revision ID: xe0g2i4k6m8p
Revises: wd9f1h3j5l7n
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "xe0g2i4k6m8p"
down_revision = "wd9f1h3j5l7n"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, tool_filter FROM automated_tasks WHERE tool_filter IS NOT NULL"
        )
    ).fetchall()
    for task_id, tools in rows:
        if isinstance(tools, str):
            tools = json.loads(tools)
        if not isinstance(tools, list):
            continue
        if "send_email" in tools and "send_report_email" not in tools:
            conn.execute(
                sa.text(
                    "UPDATE automated_tasks SET tool_filter = CAST(:t AS JSON) WHERE id = :id"
                ),
                {"id": task_id, "t": json.dumps(sorted(tools + ["send_report_email"]))},
            )


def downgrade() -> None:
    pass  # adding the tool a task was meant to have is not undone
