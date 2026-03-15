"""add users table and auth columns

Revision ID: b3f8a1d2e4c6
Revises: 132af2e6c8ac
Create Date: 2026-03-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f8a1d2e4c6'
down_revision: Union[str, Sequence[str], None] = '132af2e6c8ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create users table
    op.create_table(
        'users',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('hashed_password', sa.String(), nullable=False),
        sa.Column('full_name', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=False, server_default='manager'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_users_email', 'users', ['email'], unique=True)

    # Add user_id FK to tasks
    op.add_column('tasks', sa.Column('user_id', sa.String(), nullable=True))
    op.create_foreign_key('fk_tasks_user_id', 'tasks', 'users', ['user_id'], ['id'])
    op.create_index('ix_tasks_user_id', 'tasks', ['user_id'])

    # Add user_id FK to approvals
    op.add_column('approvals', sa.Column('user_id', sa.String(), nullable=True))
    op.create_foreign_key('fk_approvals_user_id', 'approvals', 'users', ['user_id'], ['id'])

    # Seed default admin user
    # Password: changeme123 (bcrypt hash)
    from passlib.context import CryptContext
    import uuid
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    admin_id = str(uuid.uuid4())
    hashed = pwd_context.hash("changeme123")

    op.execute(
        sa.text(
            "INSERT INTO users (id, email, hashed_password, full_name, role, is_active) "
            "VALUES (:id, :email, :hashed_password, :full_name, :role, true)"
        ).bindparams(
            id=admin_id,
            email="admin@norm.local",
            hashed_password=hashed,
            full_name="Admin",
            role="admin",
        )
    )

    # Assign existing tasks with no user_id to the seeded admin
    op.execute(
        sa.text("UPDATE tasks SET user_id = :uid WHERE user_id IS NULL").bindparams(uid=admin_id)
    )


def downgrade() -> None:
    op.drop_constraint('fk_approvals_user_id', 'approvals', type_='foreignkey')
    op.drop_column('approvals', 'user_id')

    op.drop_index('ix_tasks_user_id', 'tasks')
    op.drop_constraint('fk_tasks_user_id', 'tasks', type_='foreignkey')
    op.drop_column('tasks', 'user_id')

    op.drop_index('ix_users_email', 'users')
    op.drop_table('users')
