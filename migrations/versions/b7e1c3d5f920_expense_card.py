"""expense card

Трата, оплаченная кредитной картой: долг карты = её собственный учёт (credit_cards.debt —
стартовый долг, платежи, проценты, сверки) плюс сумма таких трат.

Revision ID: b7e1c3d5f920
Revises: f4c8e2a6b910
Create Date: 2026-10-22 19:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e1c3d5f920'
down_revision: Union[str, None] = 'f4c8e2a6b910'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('expenses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('card_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_expenses_card_id', 'credit_cards', ['card_id'], ['id'], ondelete='SET NULL'
        )


def downgrade() -> None:
    with op.batch_alter_table('expenses', schema=None) as batch_op:
        batch_op.drop_constraint('fk_expenses_card_id', type_='foreignkey')
        batch_op.drop_column('card_id')
