"""Кредиты и кредитные карты «Моих финансов».

Кредитная карта (CreditCard) — счёт с долгом. Долг меняют операции (CreditOperation):
«Внести» уменьшает долг и тратой не считается, «Продлить срок» переносит даты, не трогая
долг, «Начислили %» записывает трату в категории карты и увеличивает долг, «Сверить с
банком» правит долг руками. Даты хранятся датами, а не днём месяца: льготный период у банков
не ровно месяц, и после каждой выписки срок свой.

Кредиты остаются обязательными платежами (RecurringExpense, kind = loan): у них фиксированный
день и сумма, платёж — трата целиком, а остаток долга (debt) уменьшается при оплате
(app/shared/obligations.py). Здесь они только собираются в общий список.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared import obligations as ob
from app.shared.models import (
    Chat,
    CreditCard,
    CreditOperation,
    Expense,
    ExpenseShare,
    Member,
    RecurringExpense,
    RecurringPayment,
)

ZERO = Decimal("0")
REMINDER_DAYS = (7, 3, 1, 0)


def deadlines(card: CreditCard) -> list[tuple[dt.date, str]]:
    """Сроки карты по возрастанию: минимальный платёж и конец беспроцентного периода."""
    out = []
    if card.payment_date:
        out.append((card.payment_date, "min"))
    if card.grace_until:
        out.append((card.grace_until, "grace"))
    return sorted(out)


def next_deadline(card: CreditCard, today: dt.date) -> dt.date | None:
    """Ближайший срок, который ещё не прошёл. Без долга сроков нет."""
    if card.debt <= 0:
        return None
    upcoming = [d for d, _ in deadlines(card) if d >= today]
    return upcoming[0] if upcoming else None


async def operations_of(session: AsyncSession, card_id: int, limit: int | None = None) -> list[CreditOperation]:
    query = (
        select(CreditOperation)
        .where(CreditOperation.card_id == card_id)
        .order_by(CreditOperation.op_date.desc(), CreditOperation.id.desc())
    )
    if limit:
        query = query.limit(limit)
    return list((await session.execute(query)).scalars().all())


def _check_amount(amount: Decimal) -> None:
    if amount is None or amount <= 0:
        raise ValueError("Укажите сумму больше нуля")


async def pay(session: AsyncSession, card: CreditCard, amount: Decimal, date: dt.date | None = None) -> CreditOperation:
    """Внесли деньги на карту: долг уменьшается, тратой это не считается. Внести больше долга
    нельзя — переплата на карте здесь не учитывается."""
    _check_amount(amount)
    if amount > card.debt:
        raise ValueError(f"Это больше долга ({ob.fmt_amount(card.debt, '').strip()})")
    card.debt -= amount
    operation = CreditOperation(card_id=card.id, kind="payment", amount=amount, op_date=date or dt.date.today())
    session.add(operation)
    await session.flush()
    return operation


async def extend(
    session: AsyncSession,
    card: CreditCard,
    grace_until: dt.date | None,
    payment_date: dt.date | None,
    min_payment: Decimal | None,
) -> CreditOperation:
    """Продлить срок: долг тот же, даты новые. Старые даты запоминаются, чтобы операцию можно
    было отменить."""
    if grace_until is None and payment_date is None:
        raise ValueError("Укажите новую дату")
    old = _dates(card)
    card.grace_until = grace_until
    card.payment_date = payment_date
    card.min_payment = min_payment if payment_date else None
    card.reminders_sent = ""
    operation = CreditOperation(
        card_id=card.id, kind="extend", op_date=dt.date.today(),
        note=json.dumps({"old": old, "new": _dates(card)}),
    )
    session.add(operation)
    await session.flush()
    return operation


async def interest(session: AsyncSession, card: CreditCard, member: Member, amount: Decimal) -> CreditOperation:
    """Банк начислил проценты или комиссию: это трата в категории карты, долг растёт."""
    _check_amount(amount)
    today = dt.date.today()
    expense = Expense(
        chat_id=card.chat_id,
        title=f"Проценты · {card.title}"[:255],
        amount=amount,
        category=card.category,
        subcategory=card.subcategory,
        expense_date=today,
        payer_member_id=member.id,
        split_type="equal",
        created_by_member_id=member.id,
    )
    session.add(expense)
    await session.flush()
    session.add(ExpenseShare(expense_id=expense.id, member_id=member.id, amount=amount))
    card.debt += amount
    operation = CreditOperation(card_id=card.id, kind="interest", amount=amount, op_date=today, expense_id=expense.id)
    session.add(operation)
    await session.flush()
    return operation


async def adjust(session: AsyncSession, card: CreditCard, debt: Decimal) -> CreditOperation:
    """Сверка с банком: долг такой, как в приложении банка."""
    if debt is None or debt < 0:
        raise ValueError("Долг не может быть меньше нуля")
    delta = debt - card.debt
    card.debt = debt
    operation = CreditOperation(card_id=card.id, kind="adjust", amount=delta, op_date=dt.date.today())
    session.add(operation)
    await session.flush()
    return operation


async def undo(session: AsyncSession, card: CreditCard, operation: CreditOperation) -> None:
    """Отменить операцию: вернуть долг (или даты) как было и удалить трату процентов."""
    if operation.kind == "payment":
        card.debt += operation.amount
    elif operation.kind in ("interest", "adjust"):
        card.debt = max(card.debt - operation.amount, ZERO)
        if operation.expense_id:
            expense = await session.get(Expense, operation.expense_id)
            if expense is not None:
                await session.execute(delete(ExpenseShare).where(ExpenseShare.expense_id == expense.id))
                await session.delete(expense)
    elif operation.kind == "extend" and operation.note:
        old = json.loads(operation.note)["old"]
        card.grace_until = _parse(old.get("grace_until"))
        card.payment_date = _parse(old.get("payment_date"))
        card.min_payment = Decimal(old["min_payment"]) if old.get("min_payment") else None
    await session.delete(operation)
    await session.flush()


async def delete_card(session: AsyncSession, card: CreditCard) -> None:
    # Каскад в SQLite срабатывает не на каждом соединении — операции удаляем сами.
    # Траты процентов остаются: деньги на них потрачены.
    await session.execute(delete(CreditOperation).where(CreditOperation.card_id == card.id))
    await session.delete(card)
    await session.flush()


def _dates(card: CreditCard) -> dict:
    return {
        "grace_until": card.grace_until.isoformat() if card.grace_until else None,
        "payment_date": card.payment_date.isoformat() if card.payment_date else None,
        "min_payment": str(card.min_payment) if card.min_payment is not None else None,
    }


def _parse(value: str | None) -> dt.date | None:
    return dt.date.fromisoformat(value) if value else None


async def cards_of(session: AsyncSession, chat_id: int) -> list[CreditCard]:
    result = await session.execute(
        select(CreditCard).where(CreditCard.chat_id == chat_id).order_by(CreditCard.is_archived, CreditCard.id)
    )
    return list(result.scalars().all())


@dataclass
class LoanStatus:
    recurring: RecurringExpense
    payment: RecurringPayment | None  # платёж этого месяца, если он есть
    next_due: dt.date | None  # ближайший неоплаченный платёж


async def loans_of(session: AsyncSession, chat: Chat, today: dt.date) -> list[LoanStatus]:
    """Кредиты пространства с платежом этого месяца и датой следующего."""
    result = await session.execute(
        select(RecurringExpense)
        .where(RecurringExpense.chat_id == chat.id, RecurringExpense.kind == "loan")
        .order_by(RecurringExpense.is_active.desc(), RecurringExpense.day_of_month, RecurringExpense.id)
    )
    month = ob.month_of(today)
    out = []
    for recurring in result.scalars().all():
        payment = await ob.ensure_payment(session, recurring, month)
        next_due = None
        if payment is not None and payment.status == "pending":
            next_due = payment.due_date
        elif ob.applies_in_month(recurring, ob.next_month(month)):
            next_due = ob.due_date_for(recurring, ob.next_month(month))
        out.append(LoanStatus(recurring, payment, next_due))
    return out


def _days_text(days: int) -> str:
    if days == 0:
        return "Сегодня"
    return ob._days_label(days).capitalize()


def card_notice_text(card: CreditCard, chat: Chat, date: dt.date, what: str, today: dt.date) -> str:
    days = (date - today).days
    when = f"{_days_text(days)} ({ob._date_label(date)})"
    if what == "grace":
        text = f"💳 {when} заканчивается срок без процентов по «{card.title}»"
        text += f"\nДолг: <b>{ob.fmt_amount(card.debt, chat.currency)}</b>"
    else:
        text = f"💳 {when} минимальный платёж по «{card.title}»"
        if card.min_payment:
            text += f": <b>{ob.fmt_amount(card.min_payment, chat.currency)}</b>"
        text += f"\nДолг: {ob.fmt_amount(card.debt, chat.currency)}"
    return text


async def due_card_notices(session: AsyncSession, today: dt.date) -> list[ob.Notice]:
    """Напоминания по картам за 7, 3, 1 день и в день срока — в личку владельцу. Отправленное
    помечается в карте (вызывающий коммитит). Пропущенные дни (сервер лежал) догоняются одним
    напоминанием, как у обязательных платежей."""
    result = await session.execute(
        select(CreditCard).where(CreditCard.is_archived.is_(False), CreditCard.debt > 0)
    )
    notices = []
    for card in result.scalars().all():
        chat = await session.get(Chat, card.chat_id)
        if chat is None:
            continue
        sent = {k for k in card.reminders_sent.split(",") if k}
        for date, what in deadlines(card):
            days = (date - today).days
            if days < 0 or days > max(REMINDER_DAYS):
                continue
            keys = {f"{date.isoformat()}:{d}" for d in REMINDER_DAYS if d >= days}
            if keys <= sent:
                continue
            sent |= keys
            notices.append(ob.Notice(
                chat.id, card_notice_text(card, chat, date, what, today),
                buttons=[], app_param=f"{chat.id}_credits", app_text="💳 Открыть «Кредиты»",
            ))
        # Ключи прошедших сроков больше не нужны
        actual = {d.isoformat() for d, _ in deadlines(card)}
        card.reminders_sent = ",".join(sorted(k for k in sent if k.split(":")[0] in actual))
    await session.flush()
    return notices
