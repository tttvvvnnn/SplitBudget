"""credit cards

Кредитные карты переезжают из обязательных платежей (recurring_expenses.kind = 'card') в
свои таблицы: у карты плавающие даты (срок без процентов, минимальный платёж), а не
«N-е число каждого месяца». Карта из семейного чата переезжает в «Мои финансы» того, кто
по ней платил. Оплаченные платежи становятся операциями «внесли» — чтобы «План» за прошлые
месяцы не изменился.

Revision ID: f4c8e2a6b910
Revises: e2a6c8f0b154
Create Date: 2026-10-22 18:30:00.000000
"""
from __future__ import annotations

import calendar
import datetime as dt
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4c8e2a6b910'
down_revision: Union[str, None] = 'e2a6c8f0b154'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _next_date(day: int, today: dt.date) -> dt.date:
    """Ближайшее day-е число, начиная с сегодня."""
    year, month = today.year, today.month
    if day < today.day:
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return dt.date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _as_date(value) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def _as_datetime(value) -> dt.datetime | None:
    if value is None or isinstance(value, dt.datetime):
        return value
    return dt.datetime.fromisoformat(str(value))


def upgrade() -> None:
    cards = op.create_table(
        'credit_cards',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('credit_limit', sa.Numeric(12, 2), nullable=True),
        sa.Column('debt', sa.Numeric(12, 2), nullable=False),
        sa.Column('statement_day', sa.Integer(), nullable=True),
        sa.Column('grace_until', sa.Date(), nullable=True),
        sa.Column('min_payment', sa.Numeric(12, 2), nullable=True),
        sa.Column('payment_date', sa.Date(), nullable=True),
        sa.Column('category', sa.String(length=64), nullable=False),
        sa.Column('subcategory', sa.String(length=64), nullable=True),
        sa.Column('is_archived', sa.Boolean(), nullable=False),
        sa.Column('reminders_sent', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    ops = op.create_table(
        'credit_operations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('card_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=True),
        sa.Column('op_date', sa.Date(), nullable=False),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('expense_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['card_id'], ['credit_cards.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['expense_id'], ['expenses.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )

    conn = op.get_bind()
    today = dt.date.today()
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    rows = conn.execute(sa.text(
        "SELECT r.id, r.chat_id, r.title, r.amount, r.category, r.subcategory, r.day_of_month, "
        "r.debt, r.is_active, r.created_at, c.is_personal, m.tg_user_id "
        "FROM recurring_expenses r JOIN chats c ON c.id = r.chat_id "
        "LEFT JOIN members m ON m.id = r.payer_member_id WHERE r.kind = 'card'"
    )).mappings().all()
    for r in rows:
        chat_id = r['chat_id']
        if not r['is_personal'] and r['tg_user_id'] is not None:
            personal = conn.execute(
                sa.text("SELECT id FROM chats WHERE id = :id AND is_personal"), {'id': r['tg_user_id']}
            ).scalar()
            if personal is not None:
                chat_id = personal
        amount = r['amount'] or 0
        due = _next_date(r['day_of_month'] or 1, today)
        result = conn.execute(cards.insert().values(
            chat_id=chat_id,
            title=r['title'],
            debt=r['debt'] or 0,
            min_payment=amount if amount else None,
            payment_date=due,
            category=r['category'] or 'Кредиты',
            subcategory=r['subcategory'],
            is_archived=not r['is_active'],
            reminders_sent='',
            created_at=_as_datetime(r['created_at']) or now,
        ))
        card_id = result.inserted_primary_key[0]
        payments = conn.execute(sa.text(
            "SELECT amount, paid_at, due_date FROM recurring_payments "
            "WHERE recurring_id = :id AND status = 'paid' AND amount IS NOT NULL"
        ), {'id': r['id']}).mappings().all()
        for p in payments:
            conn.execute(ops.insert().values(
                card_id=card_id, kind='payment', amount=p['amount'],
                op_date=_as_date(p['paid_at']) or _as_date(p['due_date']),
                created_at=_as_datetime(p['paid_at']) or now,
            ))
        # SQLite не каскадирует удаление без PRAGMA foreign_keys — чистим руками
        for table in ('recurring_payments', 'recurring_participants'):
            conn.execute(sa.text(f"DELETE FROM {table} WHERE recurring_id = :id"), {'id': r['id']})
        conn.execute(sa.text("DELETE FROM recurring_expenses WHERE id = :id"), {'id': r['id']})


def downgrade() -> None:
    # Карты обратно в обязательные платежи не переносятся
    op.drop_table('credit_operations')
    op.drop_table('credit_cards')
