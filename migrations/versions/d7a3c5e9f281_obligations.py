"""obligations

Обязательные платежи: тип повтора, срок кредита, долг по карте и помесячные платежи
с отметкой «оплачено» и напоминаниями.

Revision ID: d7a3c5e9f281
Revises: b5e2f7a9c143
Create Date: 2026-10-09 09:30:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7a3c5e9f281'
down_revision: Union[str, None] = 'b5e2f7a9c143'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Тип уже существующих повторов угадываем по названию
KIND_KEYWORDS = (
    ("rent", ("аренд", "квартир")),
    ("loan", ("кредит", "ипотек", "заём", "займ")),
    ("subscription", ("подписк", "netflix", "spotify", "яндекс плюс", "кинопоиск", "icloud", "youtube")),
)


def upgrade() -> None:
    with op.batch_alter_table('recurring_expenses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=16), nullable=False, server_default='other'))
        batch_op.add_column(sa.Column('end_month', sa.String(length=7), nullable=True))
        batch_op.add_column(sa.Column('debt', sa.Numeric(12, 2), nullable=True))

    op.create_table(
        'recurring_payments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('recurring_id', sa.Integer(), nullable=False),
        sa.Column('month', sa.String(length=7), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=True),
        sa.Column('expense_id', sa.Integer(), nullable=True),
        sa.Column('reminders_sent', sa.String(length=32), nullable=False),
        sa.Column('asked', sa.Boolean(), nullable=False),
        sa.Column('paid_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['recurring_id'], ['recurring_expenses.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['expense_id'], ['expenses.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('recurring_id', 'month', name='uq_recurring_payment_month'),
    )

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, title FROM recurring_expenses")).all()
    for rid, title in rows:
        lowered = (title or "").lower()
        kind = next((k for k, words in KIND_KEYWORDS if any(w in lowered for w in words)), None)
        if kind:
            conn.execute(
                sa.text("UPDATE recurring_expenses SET kind = :kind WHERE id = :id"), {"kind": kind, "id": rid}
            )


def downgrade() -> None:
    op.drop_table('recurring_payments')
    with op.batch_alter_table('recurring_expenses', schema=None) as batch_op:
        batch_op.drop_column('debt')
        batch_op.drop_column('end_month')
        batch_op.drop_column('kind')
