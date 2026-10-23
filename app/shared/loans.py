"""Проценты по кредиту и транши.

Тело кредита — RecurringExpense.debt. Каждый день на тело начисляется debt × rate / 100 / 365;
проценты копятся отдельно (RecurringExpense.interest) и на них самих процент не идёт. Платёж
сначала гасит набежавшие проценты, остаток — тело. Транш увеличивает тело с даты выдачи.

Проценты не пишутся в базу каждый день: interest — сколько набежало к interest_from, а дальше
досчитывается на лету (accrued). Перед любым изменением тела или ставки накопленное
фиксируется (accrue), чтобы новый расчёт шёл от новой суммы.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.crud import CENT
from app.shared.models import LoanTranche, RecurringExpense, RecurringPayment

ZERO = Decimal("0")
DAYS_IN_YEAR = 365


def interest_for(body: Decimal, rate: Decimal | None, days: int) -> Decimal:
    """Проценты на сумму body за days дней."""
    if not rate or not body or days <= 0:
        return ZERO
    return (body * rate / 100 * days / DAYS_IN_YEAR).quantize(CENT)


def accrued(recurring: RecurringExpense, date: dt.date) -> Decimal:
    """Набежавшие и не уплаченные проценты на дату."""
    base = recurring.interest or ZERO
    if recurring.debt is None or recurring.interest_from is None:
        return base
    return base + interest_for(recurring.debt, recurring.rate, (date - recurring.interest_from).days)


def daily(recurring: RecurringExpense) -> Decimal:
    """Сколько процентов набегает за день."""
    return interest_for(recurring.debt or ZERO, recurring.rate, 1)


def remaining(recurring: RecurringExpense, date: dt.date) -> Decimal | None:
    """Остаток долга: тело и набежавшие проценты."""
    if recurring.debt is None:
        return None
    return recurring.debt + accrued(recurring, date)


def accrue(recurring: RecurringExpense, date: dt.date) -> None:
    """Зафиксировать проценты, набежавшие к дате."""
    if recurring.interest_from is not None and date <= recurring.interest_from:
        return
    recurring.interest = accrued(recurring, date)
    recurring.interest_from = date


def apply_payment(recurring: RecurringExpense, payment: RecurringPayment, amount: Decimal, date: dt.date) -> None:
    """Платёж: сначала проценты, потом тело."""
    if recurring.debt is None:
        return
    accrue(recurring, date)
    to_interest = min(recurring.interest or ZERO, amount)
    recurring.interest = (recurring.interest or ZERO) - to_interest
    recurring.debt = max(recurring.debt - (amount - to_interest), ZERO)
    payment.interest_paid = to_interest


def undo_payment(recurring: RecurringExpense, payment: RecurringPayment, date: dt.date) -> None:
    """Отменить оплату: вернуть проценты и тело, и проценты, что набежали бы на это тело."""
    if recurring.debt is None or not payment.amount:
        return
    accrue(recurring, date)
    to_interest = payment.interest_paid or ZERO
    body = payment.amount - to_interest
    recurring.debt += body
    paid_on = payment.paid_at.date() if payment.paid_at else date
    recurring.interest = (recurring.interest or ZERO) + to_interest + interest_for(
        body, recurring.rate, (date - paid_on).days
    )
    payment.interest_paid = None


async def tranches_of(session: AsyncSession, recurring_id: int) -> list[LoanTranche]:
    result = await session.execute(
        select(LoanTranche)
        .where(LoanTranche.recurring_id == recurring_id)
        .order_by(LoanTranche.tranche_date.desc(), LoanTranche.id.desc())
    )
    return list(result.scalars().all())


async def add_tranche(
    session: AsyncSession, recurring: RecurringExpense, amount: Decimal, date: dt.date, today: dt.date
) -> LoanTranche:
    """Новый транш: тело и сумма кредита больше; выданный задним числом — с процентами с даты."""
    if amount <= 0:
        raise ValueError("Укажите сумму больше нуля")
    if date > today:
        raise ValueError("Транш ещё не выдан: дата в будущем")
    accrue(recurring, today)
    recurring.debt = (recurring.debt or ZERO) + amount
    recurring.loan_amount = (recurring.loan_amount or ZERO) + amount
    recurring.interest = (recurring.interest or ZERO) + interest_for(amount, recurring.rate, (today - date).days)
    tranche = LoanTranche(recurring_id=recurring.id, tranche_date=date, amount=amount)
    session.add(tranche)
    await session.flush()
    return tranche


async def delete_tranche(
    session: AsyncSession, recurring: RecurringExpense, tranche: LoanTranche, today: dt.date
) -> None:
    """Удалить ошибочный транш: тело, сумма кредита и проценты на него — как не было."""
    accrue(recurring, today)
    recurring.debt = max((recurring.debt or ZERO) - tranche.amount, ZERO)
    if recurring.loan_amount is not None:
        recurring.loan_amount = max(recurring.loan_amount - tranche.amount, ZERO)
    extra = interest_for(tranche.amount, recurring.rate, (today - tranche.tranche_date).days)
    recurring.interest = max((recurring.interest or ZERO) - extra, ZERO)
    await session.delete(tranche)
    await session.flush()
