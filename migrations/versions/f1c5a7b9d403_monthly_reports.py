"""monthly reports

Отметки об отправленных итогах месяца.

Revision ID: f1c5a7b9d403
Revises: e8b4d6f0a392
Create Date: 2026-10-09 11:30:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1c5a7b9d403'
down_revision: Union[str, None] = 'e8b4d6f0a392'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'monthly_reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('month', sa.String(length=7), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('chat_id', 'month', name='uq_monthly_report'),
    )


def downgrade() -> None:
    op.drop_table('monthly_reports')
