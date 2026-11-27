"""add task deleted_at and goal archived_at

Soft delete for tasks and archive for goals (D-200, D-201, #114). Both columns are
nullable: existing rows stay live and unarchived.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-27 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("learningtask", schema=None) as batch_op:
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_learningtask_deleted_at"), ["deleted_at"], unique=False
        )
    with op.batch_alter_table("learninggoal", schema=None) as batch_op:
        batch_op.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("learninggoal", schema=None) as batch_op:
        batch_op.drop_column("archived_at")
    with op.batch_alter_table("learningtask", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_learningtask_deleted_at"))
        batch_op.drop_column("deleted_at")
