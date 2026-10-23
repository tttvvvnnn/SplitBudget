"""Кредиты и кредитные карты «Моих финансов».

Кредитная карта (CreditCard) — счёт с долгом. Покупки по карте — траты с Expense.card_id —
тратами «Моих финансов» не считаются, они живут в карте (app/shared/my_spending.py). Тратой
становится погашение: трата «Кредиты» с Expense.repays_card_id (так же записывается «Внести»).
Погашения закрывают покупки по очереди, начиная с самых старых (ledger), — так видно, что ещё
не погашено.

Долг = собственный учёт карты (card.debt: стартовый долг, сверки и старые операции) плюс
покупки минус погашения: трату поправили или удалили, и долг пересчитался сам. «Продлить
срок» переносит даты, не трогая долг, «Начислили %» записывает покупку «Проценты» в карту,
«Сверить с банком» правит долг руками. Даты хранятся датами, а не днём месяца: льготный период
у банков не ровно месяц, и после каждой выписки срок свой.

Кредиты остаются обязательными платежами (RecurringExpense, kind = loan): у них фиксированный
день и сумма, платёж — трата целиком, а остаток долга (debt) уменьшается при оплате
(app/shared/obligations.py). Здесь они только собираются в общий список.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared import obligations as ob
from app.shared.loans import remaining
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


async def purchases_total(session: AsyncSession, card_ids: list[int]) -> dict[int, Decimal]:
    """На сколько траты изменили долг каждой карты: покупки минус траты-платежи."""
    if not card_ids:
        return {}
    totals: dict[int, Decimal] = {}
    for column, sign in ((Expense.card_id, 1), (Expense.repays_card_id, -1)):
        result = await session.execute(
            select(column, func.sum(Expense.amount)).where(column.in_(card_ids)).group_by(column)
        )
        for card_id, total in result.all():
            totals[card_id] = totals.get(card_id, ZERO) + sign * Decimal(total)
    return totals


async def debt_of(session: AsyncSession, card: CreditCard) -> Decimal:
    return card.debt + (await purchases_total(session, [card.id])).get(card.id, ZERO)


@dataclass
class Ledger:
    """Что по карте ещё не погашено: погашения закрывают долг по очереди, от самого старого."""

    opening: Decimal  # долг, с которым карту завели (и прочий долг не из покупок)
    opening_left: Decimal
    left: dict[int, Decimal]  # expense_id покупки → сколько осталось погасить


async def ledger(session: AsyncSession, card: CreditCard) -> Ledger:
    """Погашения (траты-погашения, старые «Внести», сверки в минус) закрывают сначала стартовый
    долг, потом покупки и проценты по дате. Сверка в плюс — как покупка в свой день."""
    ops = (
        await session.execute(select(CreditOperation).where(CreditOperation.card_id == card.id))
    ).scalars().all()
    paid_ops = sum((o.amount for o in ops if o.kind == "payment"), ZERO)
    # card.debt уже включает старые «Внести», сверки и старые проценты без покупки
    changes = [o for o in ops if o.amount is not None and (o.kind == "adjust" or (o.kind == "interest" and not o.expense_id))]
    opening = card.debt + paid_ops - sum((o.amount for o in changes), ZERO)
    rows = (
        await session.execute(
            select(Expense.id, Expense.expense_date, Expense.amount, Expense.card_id)
            .where(or_(Expense.card_id == card.id, Expense.repays_card_id == card.id))
            .order_by(Expense.expense_date, Expense.id)
        )
    ).all()
    repaid = paid_ops + sum((a for _, _, a, c in rows if c != card.id), ZERO)
    repaid += sum((-o.amount for o in changes if o.amount < 0), ZERO)
    if opening < 0:
        repaid, opening = repaid - opening, ZERO
    # Очередь долга: стартовый долг, затем покупки и сверки в плюс по дате
    queue: list[tuple[dt.date, int | None, Decimal]] = [(dt.date.min, None, opening)]
    queue += [(d, i, a) for i, d, a, c in rows if c == card.id]
    queue += [(o.op_date, None, o.amount) for o in changes if o.amount > 0]
    queue.sort(key=lambda x: (x[0], x[1] or 0))
    left: dict[int, Decimal] = {}
    opening_left = ZERO
    for date, expense_id, amount in queue:
        part = min(amount, max(repaid, ZERO))
        repaid -= part
        if expense_id is not None:
            left[expense_id] = amount - part
        elif date == dt.date.min:
            opening_left = amount - part
    return Ledger(opening=opening, opening_left=opening_left, left=left)


async def recent_purchases(session: AsyncSession, card_id: int, limit: int) -> list[Expense]:
    """Последние покупки картой и траты-платежи по ней."""
    result = await session.execute(
        select(Expense)
        .where(or_(Expense.card_id == card_id, Expense.repays_card_id == card_id))
        .order_by(Expense.expense_date.desc(), Expense.id.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def own_card(session: AsyncSession, card_id: int, tg_user_id: int | None) -> CreditCard:
    """Карта, которой можно оплатить трату: своя (из «Моих финансов» пользователя) и не в
    архиве. ValueError, если нет."""
    card = await session.get(CreditCard, card_id)
    if card is None or tg_user_id is None or card.chat_id != tg_user_id:
        raise ValueError("Карта не найдена")
    if card.is_archived:
        raise ValueError("Карта в архиве")
    return card


def deadlines(card: CreditCard) -> list[tuple[dt.date, str]]:
    """Сроки карты по возрастанию: минимальный платёж и конец беспроцентного периода."""
    out = []
    if card.payment_date:
        out.append((card.payment_date, "min"))
    if card.grace_until:
        out.append((card.grace_until, "grace"))
    return sorted(out)


def next_deadline(card: CreditCard, debt: Decimal, today: dt.date) -> dt.date | None:
    """Ближайший срок, который ещё не прошёл. Без долга сроков нет."""
    if debt <= 0:
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


async def _owner(session: AsyncSession, card: CreditCard) -> Member:
    """Владелец карты — единственный участник «Моих финансов», где она заведена."""
    member = (await session.execute(select(Member).where(Member.chat_id == card.chat_id))).scalars().first()
    if member is None:
        raise ValueError("Нет владельца карты")
    return member


async def _card_expense(
    session: AsyncSession, card: CreditCard, title: str, amount: Decimal, date: dt.date, **link
) -> Expense:
    member = await _owner(session, card)
    expense = Expense(
        chat_id=card.chat_id,
        title=title[:255],
        amount=amount,
        category=card.category,
        subcategory=card.subcategory,
        expense_date=date,
        payer_member_id=member.id,
        split_type="equal",
        created_by_member_id=member.id,
        **link,
    )
    session.add(expense)
    await session.flush()
    session.add(ExpenseShare(expense_id=expense.id, member_id=member.id, amount=amount))
    await session.flush()
    return expense


async def pay(session: AsyncSession, card: CreditCard, amount: Decimal, date: dt.date | None = None) -> Expense:
    """Внесли деньги на карту: это трата-погашение в категории карты, долг уменьшается. Внести
    больше долга нельзя — переплата на карте здесь не учитывается."""
    _check_amount(amount)
    debt = await debt_of(session, card)
    if amount > debt:
        raise ValueError(f"Это больше долга ({ob.fmt_amount(debt, '').strip()})")
    return await _card_expense(
        session, card, f"Погашение · {card.title}", amount, date or dt.date.today(), repays_card_id=card.id
    )


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
    # Напоминания по новым датам — заново; что про выписку уже спросили, помним
    card.reminders_sent = ",".join(k for k in card.reminders_sent.split(",") if k.startswith("stmt:"))
    operation = CreditOperation(
        card_id=card.id, kind="extend", op_date=dt.date.today(),
        note=json.dumps({"old": old, "new": _dates(card)}),
    )
    session.add(operation)
    await session.flush()
    return operation


async def interest(session: AsyncSession, card: CreditCard, member: Member, amount: Decimal) -> CreditOperation:
    """Банк начислил проценты или комиссию: покупка «Проценты» в карте, долг растёт. Тратой
    станет, когда карту погасят."""
    _check_amount(amount)
    today = dt.date.today()
    expense = await _card_expense(session, card, f"Проценты · {card.title}", amount, today, card_id=card.id)
    operation = CreditOperation(card_id=card.id, kind="interest", amount=amount, op_date=today, expense_id=expense.id)
    session.add(operation)
    await session.flush()
    return operation


async def adjust(session: AsyncSession, card: CreditCard, debt: Decimal) -> CreditOperation:
    """Сверка с банком: долг такой, как в приложении банка."""
    if debt is None or debt < 0:
        raise ValueError("Долг не может быть меньше нуля")
    delta = debt - await debt_of(session, card)
    card.debt += delta
    operation = CreditOperation(card_id=card.id, kind="adjust", amount=delta, op_date=dt.date.today())
    session.add(operation)
    await session.flush()
    return operation


async def undo(session: AsyncSession, card: CreditCard, operation: CreditOperation) -> None:
    """Отменить операцию: вернуть долг (или даты) как было и удалить покупку «Проценты»."""
    expense = await session.get(Expense, operation.expense_id) if operation.expense_id else None
    if operation.kind == "payment":
        card.debt += operation.amount
    elif operation.kind == "interest" and expense is not None:
        # Проценты — покупка в карте: удалили её, и долг пересчитался сам
        await session.execute(delete(ExpenseShare).where(ExpenseShare.expense_id == expense.id))
        await session.delete(expense)
    elif operation.kind in ("interest", "adjust"):
        card.debt -= operation.amount
    elif operation.kind == "extend" and operation.note:
        old = json.loads(operation.note)["old"]
        card.grace_until = _parse(old.get("grace_until"))
        card.payment_date = _parse(old.get("payment_date"))
        card.min_payment = Decimal(old["min_payment"]) if old.get("min_payment") else None
    await session.delete(operation)
    await session.flush()


async def delete_card(session: AsyncSession, card: CreditCard) -> None:
    # Каскад в SQLite срабатывает не на каждом соединении — операции удаляем сами.
    # Покупки и проценты остаются обычными тратами: деньги на них потрачены.
    await session.execute(delete(CreditOperation).where(CreditOperation.card_id == card.id))
    await session.execute(update(Expense).where(Expense.card_id == card.id).values(card_id=None))
    await session.execute(update(Expense).where(Expense.repays_card_id == card.id).values(repays_card_id=None))
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


def card_notice_text(
    card: CreditCard, debt: Decimal, chat: Chat, date: dt.date, what: str, today: dt.date
) -> str:
    days = (date - today).days
    when = f"{_days_text(days)} ({ob._date_label(date)})"
    if what == "grace":
        text = f"💳 {when} заканчивается срок без процентов по «{card.title}»"
        text += f"\nДолг: <b>{ob.fmt_amount(debt, chat.currency)}</b>"
    else:
        text = f"💳 {when} минимальный платёж по «{card.title}»"
        if card.min_payment:
            text += f": <b>{ob.fmt_amount(card.min_payment, chat.currency)}</b>"
        text += f"\nДолг: {ob.fmt_amount(debt, chat.currency)}"
    return text


def plus_month(date: dt.date) -> dt.date:
    """То же число через месяц (или последний день месяца, если такого числа нет)."""
    year, month = (date.year + 1, 1) if date.month == 12 else (date.year, date.month + 1)
    return date.replace(year=year, month=month, day=min(date.day, calendar.monthrange(year, month)[1]))


async def extend_month(session: AsyncSession, card: CreditCard) -> CreditOperation:
    """«Продлить срок на месяц» из бота: обе даты на месяц вперёд, минимальный платёж тот же."""
    return await extend(
        session, card,
        plus_month(card.grace_until) if card.grace_until else None,
        plus_month(card.payment_date) if card.payment_date else None,
        card.min_payment,
    )


def _is_statement_day(card: CreditCard, today: dt.date) -> bool:
    if not card.statement_day:
        return False
    last = calendar.monthrange(today.year, today.month)[1]
    return today.day == min(card.statement_day, last)


def card_buttons(card: CreditCard, debt: Decimal, what: str | None) -> list[list[tuple[str, str]]]:
    """Кнопки под сообщением о карте (app/bot/handlers/credits.py)."""
    rows = []
    if what == "min" and card.min_payment and card.min_payment < debt:
        rows.append([(f"✅ Внёс {ob.fmt_amount(card.min_payment, '').strip()}", f"cc:min:{card.id}")])
    if what in ("min", "grace"):
        rows.append([("✅ Погасил весь долг", f"cc:full:{card.id}")])
    if card.grace_until or card.payment_date:
        rows.append([("📅 Продлить срок на месяц", f"cc:ext:{card.id}")])
    return rows


async def due_card_notices(session: AsyncSession, today: dt.date) -> list[ob.Notice]:
    """Напоминания по картам за 7, 3, 1 день и в день срока — в личку владельцу, а в день
    выписки — вопрос про новые даты. Отправленное помечается в карте (вызывающий коммитит).
    Пропущенные дни (сервер лежал) догоняются одним напоминанием, как у обязательных платежей."""
    result = await session.execute(select(CreditCard).where(CreditCard.is_archived.is_(False)))
    cards = list(result.scalars().all())
    purchases = await purchases_total(session, [c.id for c in cards])
    notices = []
    for card in cards:
        debt = card.debt + purchases.get(card.id, ZERO)
        chat = await session.get(Chat, card.chat_id)
        if chat is None or debt <= 0:
            continue
        sent = {k for k in card.reminders_sent.split(",") if k}
        link = {"app_param": f"{chat.id}_credits", "app_text": "💳 Открыть «Кредиты»"}
        statement_key = f"stmt:{today:%Y-%m}"
        if _is_statement_day(card, today) and statement_key not in sent:
            sent.add(statement_key)
            text = (
                f"🧾 Сегодня выписка по «{card.title}». Долг по моим данным: "
                f"<b>{ob.fmt_amount(debt, chat.currency)}</b>\n"
                "Проверьте в банке срок без процентов и минимальный платёж: если они просто сдвинулись "
                "на месяц — нажмите кнопку, если нет — поправьте в «Кредитах»."
            )
            notices.append(ob.Notice(chat.id, text, buttons=card_buttons(card, debt, None), **link))
        for date, what in deadlines(card):
            days = (date - today).days
            if days < 0 or days > max(REMINDER_DAYS):
                continue
            keys = {f"{date.isoformat()}:{d}" for d in REMINDER_DAYS if d >= days}
            if keys <= sent:
                continue
            sent |= keys
            notices.append(ob.Notice(
                chat.id, card_notice_text(card, debt, chat, date, what, today),
                buttons=card_buttons(card, debt, what), **link,
            ))
        # Ключи прошедших сроков и прошлых выписок больше не нужны
        actual = {d.isoformat() for d, _ in deadlines(card)} | {statement_key.split(":")[1]}
        card.reminders_sent = ",".join(sorted(k for k in sent if _key_date(k) in actual))
    await session.flush()
    return notices


def _key_date(key: str) -> str:
    """"2026-11-05:7" → "2026-11-05", "stmt:2026-11" → "2026-11"."""
    head, _, tail = key.partition(":")
    return tail if head == "stmt" else head


@dataclass
class MonthDebts:
    cards_start: Decimal  # долг по картам в начале месяца
    cards_end: Decimal  # и в конце
    purchases: Decimal  # покупки картами за месяц (с процентами)
    paid: Decimal  # внесено на карты за месяц
    loans: Decimal  # остаток по кредитам сейчас


async def month_debts(session: AsyncSession, chat: Chat, start: dt.date, end: dt.date) -> MonthDebts | None:
    """Долги для итогов месяца. Долг по картам на дату восстанавливается от текущего: из него
    вычитается всё, что изменило долг позже (покупки, проценты, сверки, платежи)."""
    cards = [c for c in await cards_of(session, chat.id) if not c.is_archived]
    loans = [
        r for r in (
            await session.execute(
                select(RecurringExpense).where(
                    RecurringExpense.chat_id == chat.id,
                    RecurringExpense.kind == "loan",
                    RecurringExpense.is_active.is_(True),
                    RecurringExpense.debt.is_not(None),
                )
            )
        ).scalars().all()
    ]
    if not cards and not loans:
        return None
    ids = [c.id for c in cards]
    now = sum((c.debt for c in cards), ZERO) + sum((await purchases_total(session, ids)).values(), ZERO)
    after = in_month = purchases = paid = ZERO
    if ids:
        for date, amount in (
            await session.execute(select(Expense.expense_date, Expense.amount).where(Expense.card_id.in_(ids)))
        ).all():
            if date >= end:
                after += amount
            elif date >= start:
                in_month += amount
                purchases += amount
        for date, amount in (
            await session.execute(
                select(Expense.expense_date, Expense.amount).where(Expense.repays_card_id.in_(ids))
            )
        ).all():
            if date >= end:
                after -= amount
            elif date >= start:
                in_month -= amount
                paid += amount
        for kind, date, amount in (
            await session.execute(
                select(CreditOperation.kind, CreditOperation.op_date, CreditOperation.amount).where(
                    CreditOperation.card_id.in_(ids), CreditOperation.amount.is_not(None)
                )
            )
        ).all():
            change = -amount if kind == "payment" else amount
            if date >= end:
                after += change
            elif date >= start:
                in_month += change
                if kind == "payment":
                    paid += amount
                elif kind == "interest":
                    purchases += amount
    cards_end = now - after
    return MonthDebts(
        cards_start=cards_end - in_month,
        cards_end=cards_end,
        purchases=purchases,
        paid=paid,
        loans=sum((remaining(r, dt.date.today()) for r in loans), ZERO),
    )
