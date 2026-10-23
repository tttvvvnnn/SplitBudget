"""interest as card purchase

Покупки кредиткой больше не траты «Моих финансов»: они живут в карте, а тратой становится
погашение. Проценты тоже покупка в карте — у старых процентов трата получает card_id, а долг
карты без покупок (credit_cards.debt) уменьшается на ту же сумму, чтобы общий долг не изменился.

Revision ID: e6a2c4f8b051
Revises: d5f9b3a7e142
Create Date: 2026-10-23 12:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6a2c4f8b051'
down_revision: Union[str, None] = 'd5f9b3a7e142'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT o.card_id, o.amount, e.id FROM credit_operations o "
        "JOIN expenses e ON e.id = o.expense_id "
        "WHERE o.kind = 'interest' AND o.amount IS NOT NULL AND e.card_id IS NULL"
    )).all()
    for card_id, amount, expense_id in rows:
        conn.execute(sa.text("UPDATE expenses SET card_id = :card WHERE id = :id"), {"card": card_id, "id": expense_id})
        conn.execute(
            sa.text("UPDATE credit_cards SET debt = debt - :amount WHERE id = :card"), {"amount": amount, "card": card_id}
        )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT o.card_id, o.amount, e.id FROM credit_operations o "
        "JOIN expenses e ON e.id = o.expense_id "
        "WHERE o.kind = 'interest' AND o.amount IS NOT NULL AND e.card_id = o.card_id"
    )).all()
    for card_id, amount, expense_id in rows:
        conn.execute(sa.text("UPDATE expenses SET card_id = NULL WHERE id = :id"), {"id": expense_id})
        conn.execute(
            sa.text("UPDATE credit_cards SET debt = debt + :amount WHERE id = :card"), {"amount": amount, "card": card_id}
        )
