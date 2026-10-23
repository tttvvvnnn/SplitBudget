"""loan interest

Кредит: сумма кредита, ставка и проценты, которые начисляются на тело каждый день; транши.

Revision ID: a8c4e6f0d273
Revises: f7b3d5e9c162
Create Date: 2026-10-23 14:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8c4e6f0d273'
down_revision: Union[str, None] = 'f7b3d5e9c162'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Простой ADD COLUMN, без пересоздания таблиц: пересоздание recurring_expenses при
    # включённых внешних ключах унесло бы каскадом платежи.
    op.add_column('recurring_expenses', sa.Column('loan_amount', sa.Numeric(12, 2), nullable=True))
    op.add_column('recurring_expenses', sa.Column('rate', sa.Numeric(7, 3), nullable=True))
    op.add_column('recurring_expenses', sa.Column('interest', sa.Numeric(12, 2), nullable=True))
    op.add_column('recurring_expenses', sa.Column('interest_from', sa.Date(), nullable=True))
    op.add_column('recurring_payments', sa.Column('interest_paid', sa.Numeric(12, 2), nullable=True))
    op.create_table(
        'loan_tranches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('recurring_id', sa.Integer(), nullable=False),
        sa.Column('tranche_date', sa.Date(), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['recurring_id'], ['recurring_expenses.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('loan_tranches')
    with op.batch_alter_table('recurring_payments') as batch:
        batch.drop_column('interest_paid')
    with op.batch_alter_table('recurring_expenses') as batch:
        batch.drop_column('interest_from')
        batch.drop_column('interest')
        batch.drop_column('rate')
        batch.drop_column('loan_amount')
