"""chat is_personal

Revision ID: 5e1f0a7c3d21
Revises: c6ba57efc203
Create Date: 2026-10-08 19:30:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5e1f0a7c3d21'
down_revision: Union[str, None] = 'c6ba57efc203'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('chats', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('is_personal', sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table('chats', schema=None) as batch_op:
        batch_op.drop_column('is_personal')
