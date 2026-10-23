"""expense repays card

Трата-платёж по кредитной карте: долг карты уменьшается на её сумму (в отличие от
expenses.card_id — покупки картой, которая долг увеличивает).

Revision ID: d5f9b3a7e142
Revises: c4e8a2f6d031
Create Date: 2026-10-23 10:30:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5f9b3a7e142'
down_revision: Union[str, None] = 'c4e8a2f6d031'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('expenses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('repays_card_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_expenses_repays_card_id', 'credit_cards', ['repays_card_id'], ['id'], ondelete='SET NULL'
        )


def downgrade() -> None:
    with op.batch_alter_table('expenses', schema=None) as batch_op:
        batch_op.drop_constraint('fk_expenses_repays_card_id', type_='foreignkey')
        batch_op.drop_column('repays_card_id')
