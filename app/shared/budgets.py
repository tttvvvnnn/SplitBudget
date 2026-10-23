"""Месячные лимиты (Budget): сколько потрачено относительно лимита и уведомления при 80% / 100%.

Что считается тратой:
- семейный чат — полные суммы трат этого чата;
- личное пространство («Мои финансы») — сколько владелец на самом деле потратил во всех
  своих тратах, личных и семейных (как во вкладке «Траты» «Моих финансов»,
  см. app/shared/my_spending.py).

Здесь траты считаются по покупке (как в статистике): покупка с кредитки — в день покупки, а
погашение карты (трата с repays_card_id) не считается, иначе покупка попала бы в траты
дважды. План и «Свободно» считают деньги по оплате — см. plan.money_entries.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.models import Budget, BudgetAlert, Chat, Expense, ExpenseShare, Member

ALERT_LEVELS = (100, 80)  # проверяем от большего: при скачке сразу за 100% не шлём ещё и «80%»
ZERO = Decimal("0")


def month_range(month: str) -> tuple[dt.date, dt.date]:
    """'YYYY-MM' → [первое число, первое число следующего месяца). ValueError при плохом формате."""
    year, mon = (int(p) for p in month.split("-"))
    return dt.date(year, mon, 1), dt.date(year + (mon // 12), (mon % 12) + 1, 1)


def in_budget(expense: Expense) -> bool:
    """Трата идёт в статистику и лимиты — всё, кроме погашения кредитки."""
    return expense.repays_card_id is None


async def spending_rows(
    session: AsyncSession, chat: Chat, month: str, exclude_recurring: bool = False
) -> list[tuple[str, str | None, Decimal]]:
    """Траты за месяц строками (категория, подкатегория, сумма). В «Моих финансах» — сколько
    потратил владелец. exclude_recurring — без записанных обязательных платежей."""
    from app.shared.my_spending import my_expenses  # my_spending импортирует модели, как и plan

    start, end = month_range(month)
    if chat.is_personal:
        return [
            (m.expense.category, m.expense.subcategory, m.amount)
            for m in await my_expenses(session, chat.id, start, end)
            if m.amount and in_budget(m.expense) and not (exclude_recurring and m.expense.recurring_id is not None)
        ]
    query = select(Expense.category, Expense.subcategory, Expense.amount).where(
        Expense.chat_id == chat.id, Expense.expense_date >= start, Expense.expense_date < end,
        Expense.repays_card_id.is_(None),
    )
    if exclude_recurring:
        query = query.where(Expense.recurring_id.is_(None))
    return [tuple(row) for row in (await session.execute(query)).all()]


def sum_by_keys(rows: list[tuple[str, str | None, Decimal]]) -> dict[tuple[str, str | None], Decimal]:
    """{(категория, подкатегория): сумма}, {(категория, None): сумма по категории} и
    {("", None): всего}."""
    totals: dict[tuple[str, str | None], Decimal] = {}
    for category, subcategory, amount in rows:
        keys = [("", None), (category, None)]
        if subcategory:
            keys.append((category, subcategory))
        for key in keys:
            totals[key] = totals.get(key, ZERO) + amount
    return totals


async def spending(session: AsyncSession, chat: Chat, month: str) -> dict[tuple[str, str | None], Decimal]:
    """Траты за месяц по ключам sum_by_keys."""
    return sum_by_keys(await spending_rows(session, chat, month))


@dataclass
class BudgetStatus:
    budget: Budget
    spent: Decimal


async def budgets_status(session: AsyncSession, chat: Chat, month: str) -> list[BudgetStatus]:
    result = await session.execute(select(Budget).where(Budget.chat_id == chat.id))
    budgets = result.scalars().all()
    if not budgets:
        return []
    spent = await spending(session, chat, month)
    return [BudgetStatus(b, spent.get((b.category, b.subcategory), ZERO)) for b in budgets]


def budget_label(budget: Budget) -> str:
    if not budget.category:
        return "Весь месяц"
    return f"{budget.category} › {budget.subcategory}" if budget.subcategory else budget.category


def _fmt(amount: Decimal, currency: str) -> str:
    return f"{amount:,.0f} {currency}".replace(",", " ")


async def collect_alerts(session: AsyncSession, chat: Chat, month: str) -> list[str]:
    """Тексты новых уведомлений о лимитах в чате за месяц; отправленные помечаются в
    BudgetAlert (их нужно закоммитить вызывающему), чтобы не повторяться. В «Моих финансах»
    с планом распределения дохода вместо лимитов следим за статьями плана."""
    from app.shared import plan  # plan импортирует модели, а budgets импортируют отовсюду

    if chat.is_personal:
        plan_messages = await plan.collect_alerts(session, chat, dt.date.today())
        if plan_messages is not None:
            return plan_messages
    messages = []
    for status in await budgets_status(session, chat, month):
        budget = status.budget
        if budget.amount <= 0:
            continue
        percent = status.spent * 100 / budget.amount
        level = next((lvl for lvl in ALERT_LEVELS if percent >= lvl), None)
        if level is None:
            continue
        sent = await session.execute(
            select(BudgetAlert.level).where(BudgetAlert.budget_id == budget.id, BudgetAlert.month == month)
        )
        if any(lvl >= level for lvl in sent.scalars().all()):
            continue
        session.add(BudgetAlert(budget_id=budget.id, month=month, level=level))
        icon, verb = ("🚨", "превышен") if level == 100 else ("⚠️", "почти исчерпан")
        prefix = "Мои финансы · " if chat.is_personal else ""
        messages.append(
            f"{icon} {prefix}Лимит «{budget_label(budget)}» {verb}: "
            f"{_fmt(status.spent, chat.currency)} из {_fmt(budget.amount, chat.currency)} ({percent:.0f}%)"
        )
    return messages


async def alerts_after_expense(session: AsyncSession, expense_id: int) -> list[tuple[int, str]]:
    """После создания/изменения траты: какие уведомления о лимитах отправить и куда —
    [(telegram chat_id, текст)]. Проверяются лимиты самого чата траты и «Мои финансы» каждого
    её участника. Только для трат текущего месяца — правка старых трат не должна будить."""
    expense = await session.get(Expense, expense_id)
    if expense is None:
        return []
    today = dt.date.today()
    if (expense.expense_date.year, expense.expense_date.month) != (today.year, today.month):
        return []
    month = today.strftime("%Y-%m")

    chats: list[Chat] = []
    chat = await session.get(Chat, expense.chat_id)
    if chat is not None:
        chats.append(chat)
    if chat is not None and not chat.is_personal:
        # Участники с долей и плативший: на нём вся сумма, даже если сам он в трате не участвует
        shared = select(ExpenseShare.member_id).where(ExpenseShare.expense_id == expense.id)
        result = await session.execute(
            select(Member.tg_user_id).where(
                or_(Member.id.in_(shared), Member.id == expense.payer_member_id), Member.tg_user_id.is_not(None)
            )
        )
        for tg_user_id in result.scalars().all():
            personal = await session.get(Chat, tg_user_id)
            if personal is not None and personal.is_personal:
                chats.append(personal)

    out = []
    for c in chats:
        # Личное пространство — это личка с ботом (id совпадает с tg_user_id владельца)
        out.extend((c.id, text) for text in await collect_alerts(session, c, month))
    return out
