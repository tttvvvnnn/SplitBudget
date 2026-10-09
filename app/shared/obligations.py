"""Обязательные платежи: аренда, кредиты, кредитные карты, подписки.

Шаблон — RecurringExpense (день месяца, сумма, кто платит и как делится). На каждый месяц
заводится RecurringPayment: в день платежа бот спрашивает «Оплачено?», и только после
подтверждения платёж записывается обычной тратой (Expense). Платёж по кредитной карте
тратой не считается: покупки по карте записываются отдельно, он только уменьшает долг.

Пока платёж не оплачен, он «зарезервирован» в лимитах этого месяца (см. reserved), чтобы
было видно, сколько денег на самом деле свободно.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.budgets import month_range
from app.shared.crud import CENT, build_custom_shares, build_equal_shares, load_recurring_participants
from app.shared.models import (
    Chat,
    Expense,
    ExpenseShare,
    Member,
    RecurringExpense,
    RecurringParticipant,
    RecurringPayment,
    _utcnow,
)

KINDS = {
    "rent": ("🏠", "Аренда"),
    "loan": ("🏦", "Кредит"),
    "card": ("💳", "Кредитная карта"),
    "subscription": ("📺", "Подписка"),
    "other": ("🔁", "Платёж"),
}
REMINDER_DAYS = (7, 3, 1)
ZERO = Decimal("0")


def month_of(date: dt.date) -> str:
    return date.strftime("%Y-%m")


def next_month(month: str) -> str:
    _, end = month_range(month)
    return month_of(end)


def counts_as_expense(recurring: RecurringExpense) -> bool:
    return recurring.kind != "card"


def due_date_for(recurring: RecurringExpense, month: str) -> dt.date:
    start, _ = month_range(month)
    return start.replace(day=recurring.day_of_month)


def applies_in_month(recurring: RecurringExpense, month: str) -> bool:
    """Есть ли платёж в этом месяце: шаблон активен, кредит ещё не закончился, и платёж не
    раньше, чем шаблон завели (заведённая 20-го аренда на 5-е начнётся со следующего месяца)."""
    if not recurring.is_active:
        return False
    if recurring.end_month and month > recurring.end_month:
        return False
    created = recurring.created_at.date() if recurring.created_at else dt.date.min
    return due_date_for(recurring, month) >= created


async def ensure_payment(
    session: AsyncSession, recurring: RecurringExpense, month: str
) -> RecurringPayment | None:
    """Платёж шаблона за месяц; создаётся при первом обращении."""
    result = await session.execute(
        select(RecurringPayment).where(
            RecurringPayment.recurring_id == recurring.id, RecurringPayment.month == month
        )
    )
    payment = result.scalar_one_or_none()
    if payment is not None:
        return payment
    if not applies_in_month(recurring, month):
        return None
    payment = RecurringPayment(
        recurring_id=recurring.id,
        month=month,
        due_date=due_date_for(recurring, month),
        status="pending",
        reminders_sent="",
        asked=False,
    )
    # Повторы до появления обязательных платежей создавали трату сами — такой месяц уже оплачен
    if recurring.last_generated_month and recurring.last_generated_month >= month:
        start, end = month_range(month)
        expense = (
            await session.execute(
                select(Expense)
                .where(
                    Expense.recurring_id == recurring.id,
                    Expense.expense_date >= start,
                    Expense.expense_date < end,
                )
                .order_by(Expense.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        payment.status = "paid"
        payment.amount = expense.amount if expense else recurring.amount
        payment.expense_id = expense.id if expense else None
        payment.asked = True
        payment.reminders_sent = ",".join(str(d) for d in REMINDER_DAYS)
    session.add(payment)
    await session.flush()
    return payment


def _scale_custom(
    template_amount: Decimal, amount: Decimal, pairs: list[tuple[int, Decimal]]
) -> list[tuple[int, Decimal]]:
    """Доли «вручную» пересчитываются пропорционально, если оплачена другая сумма."""
    if amount == template_amount:
        return pairs
    scaled = [(mid, (share * amount / template_amount).quantize(CENT)) for mid, share in pairs]
    diff = amount - sum((s for _, s in scaled), ZERO)
    if scaled and diff:
        mid, share = scaled[0]
        scaled[0] = (mid, share + diff)
    return scaled


async def template_shares(
    session: AsyncSession, recurring: RecurringExpense, amount: Decimal
) -> list[tuple[int, Decimal]]:
    """Доли участников для суммы amount. ValueError, если участников нет."""
    participants = await load_recurring_participants(session, recurring.id)
    if recurring.split_type == "custom":
        pairs = [(p.member_id, p.custom_amount) for p in participants if p.custom_amount]
        return build_custom_shares(amount, _scale_custom(recurring.amount, amount, pairs))
    return build_equal_shares(amount, [p.member_id for p in participants])


def _expense_date(payment: RecurringPayment, today: dt.date) -> dt.date:
    # Трата должна попасть в месяц платежа: оплатили заранее (в конце прошлого месяца)
    # или с опозданием — записываем на дату платежа.
    return today if month_of(today) == payment.month else payment.due_date


async def pay(
    session: AsyncSession,
    payment: RecurringPayment,
    amount: Decimal | None = None,
    actor_member_id: int | None = None,
) -> int | None:
    """Отметить платёж оплаченным. Возвращает id созданной траты (у кредитной карты — None).
    ValueError, если уже оплачен или не получается поделить сумму."""
    if payment.status == "paid":
        raise ValueError("Платёж уже отмечен оплаченным")
    recurring = await session.get(RecurringExpense, payment.recurring_id)
    amount = amount if amount is not None else recurring.amount
    expense_id = None
    if counts_as_expense(recurring):
        shares = await template_shares(session, recurring, amount)
        expense = Expense(
            chat_id=recurring.chat_id,
            title=recurring.title,
            amount=amount,
            category=recurring.category,
            subcategory=recurring.subcategory,
            expense_date=_expense_date(payment, dt.date.today()),
            payer_member_id=recurring.payer_member_id,
            split_type=recurring.split_type,
            created_by_member_id=actor_member_id or recurring.payer_member_id,
            recurring_id=recurring.id,
        )
        session.add(expense)
        await session.flush()
        for member_id, share in shares:
            session.add(ExpenseShare(expense_id=expense.id, member_id=member_id, amount=share))
        expense_id = expense.id
    elif recurring.debt is not None:
        recurring.debt = max(recurring.debt - amount, ZERO)

    payment.status = "paid"
    payment.amount = amount
    payment.expense_id = expense_id
    payment.paid_at = _utcnow()
    payment.asked = True
    if not recurring.last_generated_month or recurring.last_generated_month < payment.month:
        recurring.last_generated_month = payment.month
    await session.flush()
    return expense_id


async def skip(session: AsyncSession, payment: RecurringPayment) -> None:
    if payment.status == "paid":
        raise ValueError("Платёж уже оплачен — сначала отмените оплату")
    payment.status = "skipped"
    payment.asked = True
    await session.flush()


async def reset(session: AsyncSession, payment: RecurringPayment) -> None:
    """Вернуть платёж в «ждёт оплаты»: отменить оплату (удалить трату, вернуть долг по карте)
    или пропуск."""
    recurring = await session.get(RecurringExpense, payment.recurring_id)
    if payment.status == "paid":
        if payment.expense_id:
            expense = await session.get(Expense, payment.expense_id)
            if expense is not None:
                await session.delete(expense)
        elif recurring.kind == "card" and recurring.debt is not None and payment.amount:
            recurring.debt += payment.amount
    payment.status = "pending"
    payment.amount = None
    payment.expense_id = None
    payment.paid_at = None
    await session.flush()


@dataclass
class Obligation:
    payment: RecurringPayment
    recurring: RecurringExpense
    chat: Chat
    amount: Decimal  # оплаченная или плановая сумма целиком
    share: Decimal  # что из неё приходится на это пространство

    @property
    def counts(self) -> bool:
        return counts_as_expense(self.recurring) and self.payment.status != "skipped"


async def _member_share(
    session: AsyncSession, obligation_amount: Decimal, recurring: RecurringExpense,
    payment: RecurringPayment, member_id: int,
) -> Decimal:
    if payment.status == "paid" and payment.expense_id:
        result = await session.execute(
            select(ExpenseShare.amount).where(
                ExpenseShare.expense_id == payment.expense_id, ExpenseShare.member_id == member_id
            )
        )
        return result.scalar_one_or_none() or ZERO
    try:
        shares = dict(await template_shares(session, recurring, obligation_amount))
    except ValueError:
        return ZERO
    return shares.get(member_id, ZERO)


async def obligations(session: AsyncSession, chat: Chat, month: str) -> list[Obligation]:
    """Обязательные платежи пространства за месяц. В семейном чате — платежи чата целиком,
    в «Моих финансах» — личные платежи и доля владельца в семейных."""
    rows: list[tuple[RecurringExpense, Chat, int | None]] = []
    result = await session.execute(select(RecurringExpense).where(RecurringExpense.chat_id == chat.id))
    rows.extend((r, chat, None) for r in result.scalars().all())
    if chat.is_personal:
        result = await session.execute(
            select(RecurringExpense, Chat, Member.id)
            .join(RecurringParticipant, RecurringParticipant.recurring_id == RecurringExpense.id)
            .join(Member, Member.id == RecurringParticipant.member_id)
            .join(Chat, Chat.id == RecurringExpense.chat_id)
            .where(Member.tg_user_id == chat.id, RecurringExpense.chat_id != chat.id)
        )
        rows.extend(result.tuples().all())

    out = []
    for recurring, owner_chat, member_id in rows:
        payment = await ensure_payment(session, recurring, month)
        if payment is None:
            continue
        amount = payment.amount if payment.status == "paid" and payment.amount is not None else recurring.amount
        share = amount
        if member_id is not None:
            share = await _member_share(session, amount, recurring, payment, member_id)
        out.append(Obligation(payment, recurring, owner_chat, amount, share))
    out.sort(key=lambda o: (o.payment.due_date, o.recurring.title))
    return out


def reserved(items: list[Obligation]) -> dict[tuple[str, str | None], Decimal]:
    """Ещё не оплаченные платежи, которые пойдут в траты, — по тем же ключам, что и
    budgets.spending."""
    totals: dict[tuple[str, str | None], Decimal] = {}
    for o in items:
        if not o.counts or o.payment.status != "pending":
            continue
        keys = [("", None), (o.recurring.category, None)]
        if o.recurring.subcategory:
            keys.append((o.recurring.category, o.recurring.subcategory))
        for key in keys:
            totals[key] = totals.get(key, ZERO) + o.share
    return totals


def fmt_amount(amount: Decimal, currency: str) -> str:
    return f"{amount:,.0f} {currency}".replace(",", " ")


MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def _date_label(date: dt.date) -> str:
    return f"{date.day} {MONTHS_GENITIVE[date.month - 1]}"


def _days_label(days: int) -> str:
    if days == 1:
        return "завтра"
    word = "дня" if days in (2, 3, 4) else "дней"
    return f"через {days} {word}"


def payment_text(recurring: RecurringExpense, chat: Chat, payment: RecurringPayment, today: dt.date) -> str:
    icon, _ = KINDS.get(recurring.kind, KINDS["other"])
    prefix = "Мои финансы · " if chat.is_personal else ""
    days = (payment.due_date - today).days
    when = "Сегодня" if days == 0 else ("Просрочен" if days < 0 else _days_label(days).capitalize())
    text = (
        f"{icon} {prefix}{when} ({_date_label(payment.due_date)}) платёж «{recurring.title}» — "
        f"<b>{fmt_amount(recurring.amount, chat.currency)}</b>"
    )
    if recurring.kind == "card" and recurring.debt is not None:
        text += f"\nДолг по карте: {fmt_amount(recurring.debt, chat.currency)}"
    return text


@dataclass
class Notice:
    chat_id: int  # куда писать: семейный чат или личка владельца «Моих финансов»
    text: str
    ask_payment_id: int | None = None  # прислать кнопки «Оплачено?» для этого платежа
    chat_link_id: int | None = None  # пространство, которое открыть кнопкой «Другая сумма»
    # Произвольные кнопки: ряды (текст, callback_data) и ссылка «Другая сумма» на мини-апп
    # с этим startapp-параметром (например, доходы — app/shared/income.py)
    buttons: list[list[tuple[str, str]]] | None = None
    app_param: str | None = None


async def due_notices(session: AsyncSession, today: dt.date) -> list[Notice]:
    """Напоминания за 7, 3 и 1 день и вопрос «Оплачено?» в день платежа. Отправленное
    помечается в RecurringPayment (вызывающий коммитит), поэтому повторный запуск ничего не
    дублирует, а пропущенный день (сервер лежал) догоняется одним напоминанием."""
    months = [month_of(today), next_month(month_of(today))]
    result = await session.execute(select(RecurringExpense).where(RecurringExpense.is_active.is_(True)))
    notices = []
    for recurring in result.scalars().all():
        chat = await session.get(Chat, recurring.chat_id)
        if chat is None:
            continue
        for month in months:
            if (due_date_for(recurring, month) - today).days > max(REMINDER_DAYS):
                continue
            payment = await ensure_payment(session, recurring, month)
            if payment is None or payment.status != "pending":
                continue
            days = (payment.due_date - today).days
            if days <= 0:
                if not payment.asked:
                    payment.asked = True
                    notices.append(
                        Notice(chat.id, payment_text(recurring, chat, payment, today) + "\nОплачено?",
                               ask_payment_id=payment.id, chat_link_id=chat.id)
                    )
                continue
            sent = {int(d) for d in payment.reminders_sent.split(",") if d}
            due = [d for d in REMINDER_DAYS if d >= days and d not in sent]
            if due:
                payment.reminders_sent = ",".join(str(d) for d in sorted(sent | set(due), reverse=True))
                notices.append(Notice(chat.id, payment_text(recurring, chat, payment, today)))
    await session.flush()
    return notices
