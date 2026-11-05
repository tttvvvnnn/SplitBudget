"""Долги во времени для вкладки «Кредиты»: как менялся общий долг по картам и кредитам за 12
месяцев, сколько ушло на проценты и когда всё закроется при нынешнем темпе.

История не хранится — восстанавливается от текущего долга назад, как в итогах месяца
(credits.month_debts): долг на дату = сейчас − всё, что изменило долг позже.
- Карта: покупки (+), погашения (−), сверки (±) и старые «Внести» (−). Проценты по карте —
  покупка «Проценты» в карте, она уже среди покупок.
- Кредит: тело сейчас + тело, погашенное платежами позже (платёж минус ушедшее на проценты) −
  транши, выданные позже. Набежавшие, но не уплаченные проценты в историю не входят — это тело.
До того, как карту или кредит завели в приложении, долга по ним не показываем (данных нет).

Когда закроется:
- кредит — помесячно: тело × ставка / 12 набегает, платёж гасит; если платёж не покрывает
  проценты, кредит не закроется никогда (None);
- карта — долг / сколько в среднем вносили за 3 последних месяца (без нового долга).
"""
from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared import obligations as ob
from app.shared.budgets import month_range
from app.shared.credits import cards_of, debt_of
from app.shared.loans import remaining
from app.shared.models import Chat, CreditOperation, Expense, LoanTranche, RecurringExpense, RecurringPayment
from app.shared.schemas import DebtHistoryOut, DebtPayoffOut
from app.shared.stats import shift_month

ZERO = Decimal("0")
MONTHS = 12
PACE_MONTHS = 3
MAX_MONTHS = 600  # 50 лет: дальше — «не закроется»


def loan_payoff(body: Decimal, rate: Decimal | None, payment: Decimal) -> tuple[int | None, Decimal]:
    """Сколько месяцев до закрытия кредита и сколько ещё уйдёт на проценты."""
    if body <= 0:
        return 0, ZERO
    if payment <= 0:
        return None, ZERO
    monthly = (rate or ZERO) / 100 / 12
    interest_total = ZERO
    for n in range(1, MAX_MONTHS + 1):
        interest = body * monthly
        if interest >= payment:
            return None, ZERO
        interest_total += interest
        body = body + interest - payment
        if body <= 0:
            return n, interest_total.quantize(Decimal("1"))
    return None, ZERO


def card_payoff(debt: Decimal, pace: Decimal) -> int | None:
    if debt <= 0:
        return 0
    if pace <= 0:
        return None
    return math.ceil(debt / pace)


def _month_end(month: str) -> dt.date:
    return month_range(month)[1]


async def history(session: AsyncSession, chat: Chat, today: dt.date) -> DebtHistoryOut:
    current = ob.month_of(today)
    months = [shift_month(current, -i) for i in range(MONTHS - 1, -1, -1)]
    # Точка месяца — его конец (у текущего — сегодня): долг на начало следующего дня
    points = [min(_month_end(m), today + dt.timedelta(days=1)) for m in months]
    pace_from = month_range(shift_month(current, -PACE_MONTHS))[0]
    pace_to = month_range(current)[0]

    cards = [c for c in await cards_of(session, chat.id) if not c.is_archived]
    card_series = [ZERO] * MONTHS
    known = [False] * MONTHS
    interest = [ZERO] * MONTHS
    payoff: list[DebtPayoffOut] = []

    def month_index(date: dt.date) -> int | None:
        key = ob.month_of(date)
        return months.index(key) if key in months else None

    for card in cards:
        now = await debt_of(session, card)
        changes: list[tuple[dt.date, Decimal]] = []
        repaid_recently = ZERO
        for date, amount in (
            await session.execute(select(Expense.expense_date, Expense.amount).where(Expense.card_id == card.id))
        ).all():
            changes.append((date, amount))
        for date, amount in (
            await session.execute(select(Expense.expense_date, Expense.amount).where(Expense.repays_card_id == card.id))
        ).all():
            changes.append((date, -amount))
            if pace_from <= date < pace_to:
                repaid_recently += amount
        for kind, date, amount, expense_id in (
            await session.execute(
                select(CreditOperation.kind, CreditOperation.op_date, CreditOperation.amount, CreditOperation.expense_id)
                .where(CreditOperation.card_id == card.id, CreditOperation.amount.is_not(None))
            )
        ).all():
            if kind == "interest":
                i = month_index(date)
                if i is not None:
                    interest[i] += amount
                if expense_id is not None:
                    continue  # проценты уже среди покупок карты
            if kind == "payment":
                changes.append((date, -amount))
                if pace_from <= date < pace_to:
                    repaid_recently += amount
            else:
                changes.append((date, amount))
        created = card.created_at.date()
        for i, point in enumerate(points):
            if point <= created and not any(d < point for d, _ in changes):
                continue  # карты ещё не было
            known[i] = True
            card_series[i] += now - sum((a for d, a in changes if d >= point), ZERO)
        pace = (repaid_recently / PACE_MONTHS).quantize(Decimal("1"))
        months_left = card_payoff(now, pace)
        payoff.append(DebtPayoffOut(
            kind="card", title=card.title, debt=now, monthly=pace, months_left=months_left,
            payoff_month=shift_month(current, months_left) if months_left else None,
        ))

    loans = (
        await session.execute(
            select(RecurringExpense).where(
                RecurringExpense.chat_id == chat.id,
                RecurringExpense.kind == "loan",
                RecurringExpense.is_active.is_(True),
                RecurringExpense.debt.is_not(None),
            )
        )
    ).scalars().all()
    loan_series = [ZERO] * MONTHS
    for loan in loans:
        body = loan.debt or ZERO
        payments = (
            await session.execute(
                select(RecurringPayment).where(
                    RecurringPayment.recurring_id == loan.id,
                    RecurringPayment.status == "paid",
                    RecurringPayment.amount.is_not(None),
                )
            )
        ).scalars().all()
        changes = []
        for p in payments:
            date = p.paid_at.date() if p.paid_at else p.due_date
            to_interest = p.interest_paid or ZERO
            changes.append((date, -(p.amount - to_interest)))
            i = month_index(date)
            if i is not None:
                interest[i] += to_interest
        for t in (await session.execute(select(LoanTranche).where(LoanTranche.recurring_id == loan.id))).scalars().all():
            changes.append((t.tranche_date, t.amount))
        created = loan.created_at.date()
        for i, point in enumerate(points):
            if point <= created and not any(d < point for d, _ in changes):
                continue
            known[i] = True
            loan_series[i] += max(body - sum((a for d, a in changes if d >= point), ZERO), ZERO)
        debt_now = remaining(loan, today) or ZERO
        months_left, interest_left = loan_payoff(debt_now, loan.rate, loan.amount)
        payoff.append(DebtPayoffOut(
            kind="loan", title=loan.title, debt=debt_now, monthly=loan.amount, months_left=months_left,
            payoff_month=shift_month(current, months_left) if months_left else None,
            interest_left=interest_left if loan.rate else None,
        ))

    payoff.sort(key=lambda p: (p.months_left is None, p.months_left or 0))
    all_known = [p.months_left for p in payoff if p.debt > 0]
    free_month = None
    if all_known and None not in all_known:
        free_month = shift_month(current, max(all_known))
    return DebtHistoryOut(
        months=months,
        cards=[v if k else None for v, k in zip(card_series, known)],
        loans=[v if k else None for v, k in zip(loan_series, known)],
        interest=interest,
        interest_total=sum(interest, ZERO),
        payoff=payoff,
        free_month=free_month,
    )
