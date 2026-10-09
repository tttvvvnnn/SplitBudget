"""Подписки, найденные по истории трат: «Яндекс Плюс 399 уже третий месяц подряд —
сделать обязательным платежом?».

Кандидат — трата с одним и тем же названием (нормализованным, см. autocategory.normalize)
MIN_MONTHS месяцев подряд, заканчивая текущим или прошлым, не чаще MAX_PER_MONTH раз в месяц
(так «Пятёрочка» подпиской не считается) и с похожей суммой (разброс не больше AMOUNT_SPREAD).
Не предлагаем то, что уже заведено обязательным платежом, и то, на что ответили «Не подписка».

Раз в день планировщик спрашивает про новых кандидатов (в личке — для «Моих финансов», в
семейном чате — для его трат), в приложении они видны во вкладке «📌 Платежи».
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.autocategory import normalize
from app.shared.budgets import month_range
from app.shared.models import (
    Chat,
    Expense,
    ExpenseShare,
    RecurringExpense,
    RecurringParticipant,
    SubscriptionHint,
)
from app.shared.obligations import Notice, fmt_amount, month_of

MIN_MONTHS = 3
MAX_PER_MONTH = 2
AMOUNT_SPREAD = Decimal("1.15")


@dataclass
class Candidate:
    keyword: str
    title: str
    amount: Decimal
    category: str
    subcategory: str | None
    day_of_month: int
    months: int
    last_expense_id: int


def _shift(month: str, delta: int) -> str:
    year, mon = (int(p) for p in month.split("-"))
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


async def find_candidates(session: AsyncSession, chat: Chat, today: dt.date) -> list[Candidate]:
    current = month_of(today)
    start, _ = month_range(_shift(current, -MIN_MONTHS))
    expenses = (
        await session.execute(
            select(Expense)
            .where(Expense.chat_id == chat.id, Expense.recurring_id.is_(None), Expense.expense_date >= start)
            .order_by(Expense.expense_date, Expense.id)
        )
    ).scalars().all()
    groups: dict[str, list[Expense]] = {}
    for e in expenses:
        key = normalize(e.title)
        if key:
            groups.setdefault(key, []).append(e)

    known = {
        normalize(r.title)
        for r in (await session.execute(select(RecurringExpense).where(RecurringExpense.chat_id == chat.id))).scalars()
    }
    dismissed = set(
        (
            await session.execute(
                select(SubscriptionHint.keyword).where(
                    SubscriptionHint.chat_id == chat.id, SubscriptionHint.status != "asked"
                )
            )
        ).scalars().all()
    )

    out = []
    for key, items in groups.items():
        if key in known or key in dismissed:
            continue
        by_month: dict[str, list[Expense]] = {}
        for e in items:
            by_month.setdefault(month_of(e.expense_date), []).append(e)
        if any(len(v) > MAX_PER_MONTH for v in by_month.values()):
            continue
        # Подряд, заканчивая текущим или прошлым месяцем
        end = current if current in by_month else _shift(current, -1)
        streak = 0
        while _shift(end, -streak) in by_month:
            streak += 1
        if streak < MIN_MONTHS:
            continue
        amounts = [by_month[_shift(end, -i)][-1].amount for i in range(streak)]
        if min(amounts) <= 0 or max(amounts) > min(amounts) * AMOUNT_SPREAD:
            continue
        last = by_month[end][-1]
        out.append(
            Candidate(
                keyword=key,
                title=last.title,
                amount=last.amount,
                category=last.category,
                subcategory=last.subcategory,
                day_of_month=min(last.expense_date.day, 28),
                months=streak,
                last_expense_id=last.id,
            )
        )
    return out


async def accept(session: AsyncSession, chat: Chat, candidate: Candidate, created_by_member_id: int) -> RecurringExpense:
    """Завести обязательный платёж-подписку по образцу последней траты: тот же плательщик и
    те же участники поровну. Трата этого месяца, если уже записана, засчитается оплатой
    (см. obligations.ensure_payment)."""
    last = await session.get(Expense, candidate.last_expense_id)
    recurring = RecurringExpense(
        chat_id=chat.id,
        title=candidate.title,
        amount=candidate.amount,
        category=candidate.category,
        subcategory=candidate.subcategory,
        payer_member_id=last.payer_member_id,
        split_type="equal",
        day_of_month=candidate.day_of_month,
        kind="subscription",
        created_by_member_id=created_by_member_id,
    )
    session.add(recurring)
    await session.flush()
    member_ids = (
        await session.execute(select(ExpenseShare.member_id).where(ExpenseShare.expense_id == last.id))
    ).scalars().all() or [last.payer_member_id]
    for member_id in member_ids:
        session.add(RecurringParticipant(recurring_id=recurring.id, member_id=member_id))
    await _mark(session, chat.id, candidate.keyword, "accepted")
    await session.flush()
    return recurring


async def dismiss(session: AsyncSession, chat_id: int, keyword: str) -> None:
    await _mark(session, chat_id, keyword, "dismissed")


async def _mark(session: AsyncSession, chat_id: int, keyword: str, status: str) -> SubscriptionHint:
    hint = (
        await session.execute(
            select(SubscriptionHint).where(SubscriptionHint.chat_id == chat_id, SubscriptionHint.keyword == keyword)
        )
    ).scalar_one_or_none()
    if hint is None:
        hint = SubscriptionHint(chat_id=chat_id, keyword=keyword[:255], status=status)
        session.add(hint)
    else:
        hint.status = status
    await session.flush()
    return hint


async def due_subscription_asks(session: AsyncSession, today: dt.date) -> list[Notice]:
    """Новые кандидаты во всех чатах — по одному вопросу на кандидата (помечаются asked,
    коммитит вызывающий)."""
    out = []
    for chat in (await session.execute(select(Chat))).scalars().all():
        candidates = await find_candidates(session, chat, today)
        if not candidates:
            continue
        asked = set(
            (
                await session.execute(select(SubscriptionHint.keyword).where(SubscriptionHint.chat_id == chat.id))
            ).scalars().all()
        )
        for c in candidates:
            if c.keyword in asked:
                continue
            hint = await _mark(session, chat.id, c.keyword, "asked")
            prefix = "Мои финансы · " if chat.is_personal else ""
            out.append(
                Notice(
                    chat.id,
                    f"📺 {prefix}«{escape(c.title)}» — {fmt_amount(c.amount, chat.currency)} уже {c.months}-й месяц "
                    f"подряд. Похоже на подписку: сделать обязательным платежом ({c.day_of_month}-го числа)? "
                    "Тогда бот будет напоминать и спрашивать «Оплачено?», а сумма заранее учтётся в лимитах.",
                    buttons=[[("📌 Сделать платежом", f"sub:add:{hint.id}"), ("✖️ Не подписка", f"sub:no:{hint.id}")]],
                )
            )
    return out
