"""orphan rows

SQLite включал внешние ключи только на одном соединении, поэтому удаления не каскадились:
от удалённых шаблонов, трат, карт и целей остались строки-сироты. Хуже того, SQLite отдаёт
освободившийся id новой записи — новый кредит получал платёж удалённого (с его датой).
Сироты удаляем (или обнуляем ссылку там, где ON DELETE SET NULL), а платежи, которые
достались шаблону от предшественника, чиним.

Revision ID: f7b3d5e9c162
Revises: e6a2c4f8b051
Create Date: 2026-10-23 13:00:00.000000
"""
from __future__ import annotations

import calendar
import datetime as dt
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7b3d5e9c162'
down_revision: Union[str, None] = 'e6a2c4f8b051'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (таблица, колонка, родительская таблица): ON DELETE CASCADE
CASCADE = [
    ("members", "chat_id", "chats"),
    ("expenses", "chat_id", "chats"),
    ("expense_shares", "expense_id", "expenses"),
    ("expense_shares", "member_id", "members"),
    ("recurring_participants", "recurring_id", "recurring_expenses"),
    ("recurring_participants", "member_id", "members"),
    ("recurring_payments", "recurring_id", "recurring_expenses"),
    ("budget_alerts", "budget_id", "budgets"),
    ("income_asks", "source_id", "income_sources"),
    ("goal_deposits", "goal_id", "goals"),
    ("plan_alerts", "item_id", "plan_items"),
    ("credit_operations", "card_id", "credit_cards"),
]
# ON DELETE SET NULL
SET_NULL = [
    ("expenses", "recurring_id", "recurring_expenses"),
    ("expenses", "card_id", "credit_cards"),
    ("expenses", "repays_card_id", "credit_cards"),
    ("recurring_payments", "expense_id", "expenses"),
    ("incomes", "source_id", "income_sources"),
    ("goal_deposits", "income_id", "incomes"),
    ("plan_items", "goal_id", "goals"),
    ("credit_operations", "expense_id", "expenses"),
]


def _day_in_month(day: int, month: str) -> dt.date:
    year, mon = map(int, month.split("-"))
    return dt.date(year, mon, min(day, calendar.monthrange(year, mon)[1]))


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(sa.inspect(conn).get_table_names())
    for table, column, parent in CASCADE:
        if table in tables and parent in tables:
            conn.execute(sa.text(
                f"DELETE FROM {table} WHERE {column} IS NOT NULL "
                f"AND {column} NOT IN (SELECT id FROM {parent})"
            ))
    for table, column, parent in SET_NULL:
        if table in tables and parent in tables:
            conn.execute(sa.text(
                f"UPDATE {table} SET {column} = NULL WHERE {column} IS NOT NULL "
                f"AND {column} NOT IN (SELECT id FROM {parent})"
            ))

    # Платежи, доставшиеся шаблону от удалённого предшественника с тем же id, за месяцы до
    # того, как шаблон завели.
    conn.execute(sa.text(
        "DELETE FROM recurring_payments WHERE id IN ("
        " SELECT p.id FROM recurring_payments p JOIN recurring_expenses r ON r.id = p.recurring_id"
        " WHERE p.month < substr(r.created_at, 1, 7))"
    ))
    # Ждущие оплаты платежи — на день шаблона (у чужого платежа дата предшественника)
    rows = conn.execute(sa.text(
        "SELECT p.id, p.month, p.due_date, r.day_of_month FROM recurring_payments p "
        "JOIN recurring_expenses r ON r.id = p.recurring_id WHERE p.status = 'pending'"
    )).all()
    for pid, month, due, day in rows:
        right = _day_in_month(day, month)
        if str(due) != right.isoformat():
            conn.execute(
                sa.text("UPDATE recurring_payments SET due_date = :d, reminders_sent = '', asked = 0 WHERE id = :id"),
                {"d": right, "id": pid},
            )


def downgrade() -> None:
    pass
