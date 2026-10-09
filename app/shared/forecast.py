"""Прогноз трат на конец месяца: «в этом темпе выйдешь на 52 000 при лимите 45 000».

    прогноз = уже потрачено
            + обычные траты (без обязательных платежей) / прошедшие дни × оставшиеся дни
            + ещё не оплаченные обязательные платежи месяца

Обязательные платежи не экстраполируются: аренда в начале месяца не значит, что её будет
«ещё 30 раз». Так же и крупные разовые траты (см. one_off): аренда, записанная руками,
или покупка телевизора входят в «уже потрачено», но в темп не идут. Прогноз считается только для текущего месяца и с MIN_DAY-го числа — раньше
данных слишком мало и цифра пугает зря.

Раз в день (планировщик в 9:00) бот предупреждает о лимите, который по прогнозу будет
превышен, — один раз за месяц на лимит и только пока он ещё не исчерпан (дальше работают
обычные уведомления 80% / 100%, app/shared/budgets.py).
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared import obligations as ob
from app.shared.budgets import budget_label, budgets_status, spending_rows, sum_by_keys
from app.shared.models import Budget, BudgetAlert, Chat

ZERO = Decimal("0")
MIN_DAY = 5
ALERT_FROM_DAY = 7
# Разовая трата: в ONE_OFF_MEDIANS раз больше медианной и не меньше ONE_OFF_SHARE всех
# обычных трат месяца
ONE_OFF_MEDIANS = 5
ONE_OFF_SHARE = Decimal("0.25")
# Отметка в BudgetAlert.level для предупреждения по прогнозу (80 и 100 — обычные уведомления)
FORECAST_LEVEL = 1

Key = tuple[str, str | None]


@dataclass
class Forecast:
    month: str
    days_left: int
    totals: dict[Key, Decimal]  # прогноз на конец месяца по тем же ключам, что budgets.spending
    reserved: dict[Key, Decimal]  # ещё не оплаченные обязательные платежи

    def get(self, key: Key) -> Decimal:
        return self.totals.get(key, ZERO)


async def forecast(session: AsyncSession, chat: Chat, today: dt.date) -> Forecast | None:
    """Прогноз на текущий месяц или None, если ещё рано (раньше MIN_DAY-го числа)."""
    if today.day < MIN_DAY:
        return None
    month = ob.month_of(today)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_left = days_in_month - today.day
    spent = sum_by_keys(await spending_rows(session, chat, month))
    variable = sum_by_keys(one_off(await spending_rows(session, chat, month, exclude_recurring=True))[0])
    reserved = ob.reserved(await ob.obligations(session, chat, month))
    keys = set(spent) | set(reserved)
    totals = {
        key: (
            spent.get(key, ZERO)
            + variable.get(key, ZERO) / today.day * days_left
            + reserved.get(key, ZERO)
        ).quantize(Decimal("0.01"))
        for key in keys
    }
    return Forecast(month=month, days_left=days_left, totals=totals, reserved=reserved)


def one_off(rows: list[tuple[str, str | None, Decimal]]) -> tuple[list, list]:
    """Делит траты на обычные (идут в темп) и крупные разовые."""
    if len(rows) < 3:
        return rows, []
    amounts = sorted(r[2] for r in rows)
    median = amounts[len(amounts) // 2]
    total = sum(amounts, ZERO)
    regular, large = [], []
    for row in rows:
        is_large = row[2] >= median * ONE_OFF_MEDIANS and row[2] >= total * ONE_OFF_SHARE
        (large if is_large else regular).append(row)
    return regular, large


async def due_forecast_alerts(session: AsyncSession, today: dt.date) -> list[tuple[int, str]]:
    """Предупреждения «лимит будет превышен» — [(telegram chat_id, текст)]. Отправленные
    помечаются в BudgetAlert (коммитит вызывающий)."""
    if today.day < ALERT_FROM_DAY:
        return []
    month = ob.month_of(today)
    chat_ids = (await session.execute(select(Budget.chat_id).distinct())).scalars().all()
    out = []
    for chat_id in chat_ids:
        chat = await session.get(Chat, chat_id)
        if chat is None:
            continue
        fc = await forecast(session, chat, today)
        if fc is None or fc.days_left <= 0:
            continue
        for status in await budgets_status(session, chat, month):
            budget = status.budget
            key = (budget.category, budget.subcategory)
            predicted = fc.get(key)
            if budget.amount <= 0 or predicted <= budget.amount or status.spent >= budget.amount * Decimal("0.8"):
                continue
            sent = await session.execute(
                select(BudgetAlert.id).where(BudgetAlert.budget_id == budget.id, BudgetAlert.month == month)
            )
            if sent.first() is not None:  # уже было это предупреждение или 80% / 100%
                continue
            session.add(BudgetAlert(budget_id=budget.id, month=month, level=FORECAST_LEVEL))
            money = lambda a: ob.fmt_amount(a, chat.currency)  # noqa: E731
            # Сколько можно тратить в день, чтобы уложиться (с учётом платежей впереди)
            per_day = max(budget.amount - status.spent - fc.reserved.get(key, ZERO), ZERO) / fc.days_left
            prefix = "Мои финансы · " if chat.is_personal else ""
            out.append((
                chat.id,
                f"📈 {prefix}В таком темпе лимит «{budget_label(budget)}» будет превышен: "
                f"прогноз <b>{money(predicted)}</b> из {money(budget.amount)}. "
                f"Чтобы уложиться, тратьте не больше ~{money(per_day)} в день.",
            ))
    await session.flush()
    return out
