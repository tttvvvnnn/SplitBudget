"""budgets

Месячные лимиты трат и отметки об отправленных уведомлениях.

Revision ID: b5e2f7a9c143
Revises: 9c4d1e8a2b37
Create Date: 2026-10-08 20:30:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5e2f7a9c143'
down_revision: Union[str, None] = '9c4d1e8a2b37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'budgets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('category', sa.String(length=64), nullable=False),
        sa.Column('subcategory', sa.String(length=64), nullable=True),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('chat_id', 'category', 'subcategory', name='uq_budget_chat_category'),
    )
    op.create_table(
        'budget_alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('budget_id', sa.Integer(), nullable=False),
        sa.Column('month', sa.String(length=7), nullable=False),
        sa.Column('level', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['budget_id'], ['budgets.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('budget_id', 'month', 'level', name='uq_budget_alert'),
    )


def downgrade() -> None:
    op.drop_table('budget_alerts')
    op.drop_table('budgets')
