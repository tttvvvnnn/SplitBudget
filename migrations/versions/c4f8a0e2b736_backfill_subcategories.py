"""backfill subcategories

Старые траты (записанные до появления подкатегорий и автоподбора) раскладываются по
подкатегориям из словаря магазинов (app/shared/merchants.py): «Винлаб» в «Продуктах» →
«Продукты › Алкоголь». Категорию, которую выбрал человек, не меняем — подкатегория
ставится, только если словарь согласен с категорией траты. Исключение — «Другое»: там
категорию тоже берём из словаря. Траты, у которых подкатегория уже есть, не трогаем.

Revision ID: c4f8a0e2b736
Revises: b3e7c9d1f625
Create Date: 2026-10-09 14:10:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.shared.autocategory import normalize, suggest_from_dictionary
from app.shared.categories import OTHER


# revision identifiers, used by Alembic.
revision: str = 'c4f8a0e2b736'
down_revision: Union[str, None] = 'b3e7c9d1f625'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ('expenses', 'recurring_expenses')


def upgrade() -> None:
    conn = op.get_bind()
    for table in TABLES:
        t = sa.table(
            table,
            sa.column('id', sa.Integer),
            sa.column('title', sa.String),
            sa.column('category', sa.String),
            sa.column('subcategory', sa.String),
        )
        rows = conn.execute(sa.select(t.c.id, t.c.title, t.c.category).where(t.c.subcategory.is_(None))).all()
        for row_id, title, category in rows:
            suggestion = suggest_from_dictionary(normalize(title or ''))
            if suggestion is None or suggestion.subcategory is None:
                continue
            if category == suggestion.category:
                values = {'subcategory': suggestion.subcategory}
            elif category == OTHER:
                values = {'category': suggestion.category, 'subcategory': suggestion.subcategory}
            else:
                continue
            conn.execute(t.update().where(t.c.id == row_id).values(**values))


def downgrade() -> None:
    # Данные не откатываем: подкатегории остаются, старый код их просто не показывает.
    pass
