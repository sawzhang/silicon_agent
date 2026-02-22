"""add estimated_hours to task_templates

Revision ID: a1b2c3d4e5f6
Revises: e3a0a6449e35
Create Date: 2026-02-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'e3a0a6449e35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('task_templates', sa.Column('estimated_hours', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('task_templates', 'estimated_hours')
