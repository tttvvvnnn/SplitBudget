"""member is_hidden

Пользователь может убрать семейный чат из своего списка в приложении.

Revision ID: b3e7c9d1f625
Revises: a2d6b8c0e514
Create Date: 2026-10-09 13:40:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3e7c9d1f625'
down_revision: Union[str, None] = 'a2d6b8c0e514'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('members') as batch_op:
        batch_op.add_column(sa.Column('is_hidden', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('members') as batch_op:
        batch_op.drop_column('is_hidden')
