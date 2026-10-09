"""Доходы в «Моих финансах» и сводка «сколько свободно».

Регулярный доход (IncomeSource: зарплата 10-го, аванс 25-го) в свой день ждёт
подтверждения: бот спрашивает «Пришла?», и после ответа записывается Income. Пока не
пришёл — считается ожидаемым.

Сводка месяца:
    свободно = получено + ожидается − траты − обязательные платежи впереди − отложено на цели
А «до следующего поступления» — сколько можно тратить в день из уже полученных денег,
с учётом обязательных платежей, которые придётся оплатить раньше него.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.autocategory import normalize
from app.shared.budgets import month_range, spending
from app.shared.goals import saved_in_month
from app.shared.models import Chat, Income, IncomeAsk, IncomeSource
from app.shared.obligations import Notice, fmt_amount, month_of, next_month, obligations

ZERO = Decimal("0")


def source_date(source: IncomeSource, month: str) -> dt.date:
    start, _ = month_range(month)
    return start.replace(day=source.day_of_month)


async def _incomes(session: AsyncSession, chat_id: int, month: str) -> list[Income]:
    start, end = month_range(month)
    result = await session.execute(
        select(Income)
        .where(Income.chat_id == chat_id, Income.income_date >= start, Income.income_date < end)
        .order_by(Income.income_date.desc(), Income.id.desc())
    )
    return list(result.scalars().all())


async def _sources(session: AsyncSession, chat_id: int) -> list[IncomeSource]:
    result = await session.execute(
        select(IncomeSource).where(IncomeSource.chat_id == chat_id).order_by(IncomeSource.day_of_month)
    )
    return list(result.scalars().all())


def _expected_in(source: IncomeSource, month: str) -> bool:
    """Ждём ли этот доход в месяце: активен и заведён не позже его дня в этом месяце."""
    if not source.is_active:
        return False
    created = source.created_at.date() if source.created_at else dt.date.min
    return source_date(source, month) >= created or month_of(created) < month


@dataclass
class SourceStatus:
    source: IncomeSource
    date: dt.date
    income: Income | None  # пришедший в этом месяце


@dataclass
class IncomeSummary:
    incomes: list[Income]
    sources: list[SourceStatus]
    received: Decimal
    expected: Decimal
    spent: Decimal
    obligations_pending: Decimal
    saved: Decimal  # отложено на цели в этом месяце (app/shared/goals.py)
    free: Decimal
    next_title: str | None = None
    next_date: dt.date | None = None
    next_amount: Decimal | None = None
    days_to_next: int | None = None
    obligations_before_next: Decimal = ZERO
    per_day: Decimal | None = None


async def _source_statuses(session: AsyncSession, chat_id: int, month: str, incomes: list[Income]) -> list[SourceStatus]:
    by_source = {i.source_id: i for i in incomes if i.source_id}
    return [
        SourceStatus(s, source_date(s, month), by_source.get(s.id))
        for s in await _sources(session, chat_id)
        if _expected_in(s, month) or s.id in by_source
    ]


async def summary(session: AsyncSession, chat: Chat, month: str, today: dt.date) -> IncomeSummary:
    incomes = await _incomes(session, chat.id, month)
    statuses = await _source_statuses(session, chat.id, month, incomes)
    received = sum((i.amount for i in incomes), ZERO)
    expected = sum((st.source.amount for st in statuses if st.income is None and st.source.is_active), ZERO)
    spent = (await spending(session, chat, month)).get(("", None), ZERO)
    items = await obligations(session, chat, month)
    pending = [o for o in items if o.counts and o.payment.status == "pending"]
    obligations_pending = sum((o.share for o in pending), ZERO)
    saved = await saved_in_month(session, chat.id, month)
    result = IncomeSummary(
        incomes=incomes,
        sources=statuses,
        received=received,
        expected=expected,
        spent=spent,
        obligations_pending=obligations_pending,
        saved=saved,
        free=received + expected - spent - obligations_pending - saved,
    )
    if month != month_of(today):
        return result

    # Ближайшее ожидаемое поступление: в этом месяце или в начале следующего
    upcoming: list[tuple[dt.date, IncomeSource]] = [
        (st.date, st.source) for st in statuses if st.income is None and st.source.is_active and st.date >= today
    ]
    nm = next_month(month)
    upcoming += [(source_date(s, nm), s) for s in await _sources(session, chat.id) if s.is_active]
    if upcoming:
        date, source = min(upcoming, key=lambda x: x[0])
        before = sum((o.share for o in pending if o.payment.due_date < date), ZERO)
        if date > month_range(month)[1] - dt.timedelta(days=1):
            # Следующее поступление уже в следующем месяце — добавим его платежи до этой даты
            next_items = await obligations(session, chat, nm)
            before += sum(
                (o.share for o in next_items if o.counts and o.payment.status == "pending" and o.payment.due_date < date),
                ZERO,
            )
        days = max((date - today).days, 1)
        result.next_title = source.title
        result.next_date = date
        result.next_amount = source.amount
        result.days_to_next = days
        result.obligations_before_next = before
        result.per_day = ((received - spent - saved - before) / days).quantize(Decimal("0.01"))
    return result


async def record_income(
    session: AsyncSession,
    chat: Chat,
    title: str,
    amount: Decimal,
    income_date: dt.date,
    source_id: int | None = None,
) -> Income:
    """Записать поступление. Без source_id — привязываем к регулярному доходу с тем же
    первым словом названия («зарплата», «аванс»), если в этом месяце он ещё не пришёл."""
    month = month_of(income_date)
    if source_id is None:
        incomes = await _incomes(session, chat.id, month)
        taken = {i.source_id for i in incomes if i.source_id}
        first = (normalize(title).split() or [""])[0]
        for source in await _sources(session, chat.id):
            source_first = (normalize(source.title).split() or [""])[0]
            if source.is_active and source.id not in taken and first and first == source_first:
                source_id = source.id
                break
    income = Income(
        chat_id=chat.id, title=title.strip()[:255] or "Доход", amount=amount, income_date=income_date, source_id=source_id
    )
    session.add(income)
    await session.flush()
    return income


async def due_income_asks(session: AsyncSession, today: dt.date) -> list[Notice]:
    """В день регулярного дохода — вопрос «Пришла?» в личку владельцу (один раз за месяц)."""
    month = month_of(today)
    result = await session.execute(select(IncomeSource).where(IncomeSource.is_active.is_(True)))
    notices = []
    for source in result.scalars().all():
        if not _expected_in(source, month) or source_date(source, month) > today:
            continue
        asked = await session.execute(
            select(IncomeAsk).where(IncomeAsk.source_id == source.id, IncomeAsk.month == month)
        )
        if asked.scalar_one_or_none() is not None:
            continue
        got = await session.execute(
            select(Income.id).where(Income.source_id == source.id, Income.income_date >= month_range(month)[0])
        )
        session.add(IncomeAsk(source_id=source.id, month=month))
        if got.first() is not None:
            continue
        chat = await session.get(Chat, source.chat_id)
        if chat is None:
            continue
        notices.append(
            Notice(
                chat.id,
                f"💰 Пришла «{source.title}» — <b>{fmt_amount(source.amount, chat.currency)}</b>?",
                buttons=[[("✅ Пришла", f"inc:yes:{source.id}:{month}"), ("⏳ Ещё нет", f"inc:no:{source.id}:{month}")]],
                app_param=f"{chat.id}_income",
            )
        )
    await session.flush()
    return notices
