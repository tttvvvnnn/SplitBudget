"""forget other rules

Забыть выученные «название → Другое» без подкатегории: раньше быстрый ввод запоминал их,
когда не угадывал категорию, и они закрывали словарь и ИИ (см. app/shared/autocategory.py).

Revision ID: c4e8a2f6d031
Revises: b7e1c3d5f920
Create Date: 2026-10-23 09:40:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4e8a2f6d031'
down_revision: Union[str, None] = 'b7e1c3d5f920'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM category_rules WHERE category = 'Другое' AND subcategory IS NULL")


def downgrade() -> None:
    pass
