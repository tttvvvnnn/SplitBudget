"""subcategories

Подкатегории трат и переход на новое дерево категорий (app/shared/categories.py):
«Еда» → «Продукты», «ЖКХ» → «Дом › Коммуналка», «Одежда» → «Одежда и обувь».

Revision ID: 7b3c9d2e4f10
Revises: 5e1f0a7c3d21
Create Date: 2026-10-08 20:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b3c9d2e4f10'
down_revision: Union[str, None] = '5e1f0a7c3d21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ('expenses', 'recurring_expenses')

# старая категория → (новая категория, подкатегория)
RENAMES = {
    'Еда': ('Продукты', None),
    'ЖКХ': ('Дом', 'Коммуналка'),
    'Одежда': ('Одежда и обувь', None),
}


def upgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('subcategory', sa.String(length=64), nullable=True))
        t = sa.table(table, sa.column('category', sa.String), sa.column('subcategory', sa.String))
        for old, (new, sub) in RENAMES.items():
            op.execute(t.update().where(t.c.category == old).values(category=new, subcategory=sub))


def downgrade() -> None:
    for table in TABLES:
        t = sa.table(table, sa.column('category', sa.String), sa.column('subcategory', sa.String))
        for old, (new, sub) in RENAMES.items():
            cond = t.c.category == new
            if sub is not None:
                cond = cond & (t.c.subcategory == sub)
            op.execute(t.update().where(cond).values(category=old))
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column('subcategory')
