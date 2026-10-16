"""income plan

План распределения дохода: статьи в процентах, уведомления по статьям, день начала
месяца плана и связь пополнения цели с поступлением.

Revision ID: a7c3e5f9b201
Revises: d5a9b1c3e847
Create Date: 2026-10-16 16:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e5f9b201'
down_revision: Union[str, None] = 'd5a9b1c3e847'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'plan_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('percent', sa.Numeric(5, 2), nullable=False),
        sa.Column('categories', sa.String(length=1024), nullable=False),
        sa.Column('goal_id', sa.Integer(), nullable=True),
        sa.Column('recurring_ids', sa.String(length=255), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['goal_id'], ['goals.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'plan_alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('period', sa.String(length=10), nullable=False),
        sa.Column('level', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['item_id'], ['plan_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('item_id', 'period', 'level', name='uq_plan_alert'),
    )
    with op.batch_alter_table('chats') as batch_op:
        batch_op.add_column(sa.Column('plan_start_day', sa.Integer(), nullable=False, server_default='1'))
    with op.batch_alter_table('goal_deposits') as batch_op:
        batch_op.add_column(sa.Column('income_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_goal_deposits_income_id', 'incomes', ['income_id'], ['id'], ondelete='SET NULL'
        )


def downgrade() -> None:
    with op.batch_alter_table('goal_deposits') as batch_op:
        batch_op.drop_constraint('fk_goal_deposits_income_id', type_='foreignkey')
        batch_op.drop_column('income_id')
    with op.batch_alter_table('chats') as batch_op:
        batch_op.drop_column('plan_start_day')
    op.drop_table('plan_alerts')
    op.drop_table('plan_items')
