"""plan item months

Проценты статей плана по месяцам: правка в месяце не меняет прошлые месяцы.

Revision ID: b3d7f1a5c829
Revises: a8c4e6f0d273
Create Date: 2026-11-01 10:30:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3d7f1a5c829'
down_revision: Union[str, None] = 'a8c4e6f0d273'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Записи для старых статей не нужны: статья без записей во всех месяцах живёт с
    # PlanItem.percent, а при первой правке её процент закрепляется за прошлыми месяцами.
    op.create_table(
        'plan_item_months',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('period', sa.Date(), nullable=False),
        sa.Column('percent', sa.Numeric(5, 2), nullable=True),
        sa.ForeignKeyConstraint(['item_id'], ['plan_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('item_id', 'period', name='uq_plan_item_month'),
    )


def downgrade() -> None:
    op.drop_table('plan_item_months')
