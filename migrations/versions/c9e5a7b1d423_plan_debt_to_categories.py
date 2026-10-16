"""plan debt items to categories

Долговые статьи плана становятся обычными расходными: вместо привязки к платежам у них
категории этих платежей. Кредитным картам без своей категории ставится
«Кредиты › Кредитная карта» — платёж по карте теперь попадает в план по категории.

Revision ID: c9e5a7b1d423
Revises: b8d4f6a0c312
Create Date: 2026-10-16 20:00:00.000000
"""
from __future__ import annotations

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9e5a7b1d423'
down_revision: Union[str, None] = 'b8d4f6a0c312'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SEP = " › "


def _loads(raw):
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE recurring_expenses SET category = 'Кредиты', subcategory = 'Кредитная карта' "
        "WHERE kind = 'card' AND (category IS NULL OR category = 'Другое')"
    ))
    items = bind.execute(sa.text("SELECT id, chat_id, kind, categories, recurring_ids FROM plan_items")).fetchall()
    for item_id, chat_id, kind, _, recurring_ids in items:
        if kind != "debt":
            continue
        taken = {
            str(k)
            for other_id, other_chat, other_kind, cats, _ in items
            if other_chat == chat_id and other_kind == "spend" and other_id != item_id
            for k in _loads(cats)
        }
        keys = []
        for rid in _loads(recurring_ids):
            row = bind.execute(
                sa.text("SELECT category, subcategory FROM recurring_expenses WHERE id = :id"), {"id": int(rid)}
            ).first()
            if row is None:
                continue
            key = row[0] + (SEP + row[1] if row[1] else "")
            if key not in taken and key not in keys:
                keys.append(key)
        bind.execute(
            sa.text("UPDATE plan_items SET kind = 'spend', categories = :c, recurring_ids = '[]' WHERE id = :id"),
            {"c": json.dumps(keys, ensure_ascii=False), "id": item_id},
        )
        items = [
            (i, c, "spend", json.dumps(keys, ensure_ascii=False), r) if i == item_id else (i, c, k, cats, r)
            for i, c, k, cats, r in items
        ]


def downgrade() -> None:
    pass
