"""Цели накоплений «Моих финансов».

Накоплено — сумма пополнений (снятие — отрицательное пополнение). Если у цели есть срок,
считаем, сколько нужно откладывать в месяц, включая текущий: (цель − накоплено) / месяцев.
Отложенное в месяце вычитается из «свободно» (app/shared/income.py).

Прогноз: темп — сколько в среднем откладывали в месяц за последние 3 месяца (или с начала
цели, если она моложе), и когда при таком темпе цель будет накоплена.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.budgets import month_range
from app.shared.models import Goal, GoalDeposit

ZERO = Decimal("0")
PACE_MONTHS = 3
DAYS_IN_MONTH = Decimal("30.4")


def months_left(today: dt.date, deadline: dt.date) -> int:
    """Сколько месяцев осталось откладывать, считая текущий (минимум 1)."""
    return max((deadline.year - today.year) * 12 + deadline.month - today.month + 1, 1)


@dataclass
class GoalStatus:
    goal: Goal
    saved: Decimal
    saved_this_month: Decimal
    months_left: int | None
    monthly_needed: Decimal | None
    deposits: list[GoalDeposit]
    pace: Decimal | None = None  # сколько в среднем откладывали в месяц
    eta_month: str | None = None  # когда накопится при таком темпе, 'YYYY-MM'


def _shift(month: str, delta: int) -> str:
    year, mon = (int(p) for p in month.split("-"))
    index = year * 12 + mon - 1 + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def forecast(goal: Goal, deposits: list[GoalDeposit], saved: Decimal, today: dt.date) -> tuple[Decimal | None, str | None]:
    """Темп за последние 3 месяца (не раньше создания цели, но не меньше месяца — иначе одно
    пополнение в первый день дало бы «темп» в 30 раз больше) и месяц, когда цель накопится."""
    month = today.strftime("%Y-%m")
    window = month_range(_shift(month, -PACE_MONTHS))[0]
    window = max(window, goal.created_at.date().replace(day=1))
    in_window = sum((d.amount for d in deposits if window <= d.deposit_date <= today), ZERO)
    days = max(Decimal((today - window).days + 1), DAYS_IN_MONTH)
    pace = (in_window * DAYS_IN_MONTH / days).quantize(Decimal("1"))
    if pace <= 0:
        return None, None
    left = goal.target - saved
    if left <= 0:
        return pace, None
    return pace, _shift(month, math.ceil(left / pace))


async def goals_status(session: AsyncSession, chat_id: int, today: dt.date) -> list[GoalStatus]:
    goals = (
        await session.execute(
            select(Goal).where(Goal.chat_id == chat_id).order_by(Goal.is_archived, Goal.created_at)
        )
    ).scalars().all()
    start, end = month_range(today.strftime("%Y-%m"))
    out = []
    for goal in goals:
        deposits = (
            await session.execute(
                select(GoalDeposit)
                .where(GoalDeposit.goal_id == goal.id)
                .order_by(GoalDeposit.deposit_date.desc(), GoalDeposit.id.desc())
            )
        ).scalars().all()
        saved = sum((d.amount for d in deposits), ZERO)
        this_month = sum((d.amount for d in deposits if start <= d.deposit_date < end), ZERO)
        left = needed = None
        if goal.deadline and not goal.is_archived:
            left = months_left(today, goal.deadline)
            # Отложенное в этом месяце уже идёт в зачёт текущего месяца
            remaining = max(goal.target - saved + this_month, ZERO)
            needed = (remaining / left).quantize(Decimal("1"))
        pace, eta = forecast(goal, list(deposits), saved, today) if not goal.is_archived else (None, None)
        out.append(GoalStatus(goal, saved, this_month, left, needed, list(deposits), pace, eta))
    return out


async def saved_in_month(session: AsyncSession, chat_id: int, month: str) -> Decimal:
    start, end = month_range(month)
    result = await session.execute(
        select(func.coalesce(func.sum(GoalDeposit.amount), 0))
        .join(Goal, Goal.id == GoalDeposit.goal_id)
        .where(Goal.chat_id == chat_id, GoalDeposit.deposit_date >= start, GoalDeposit.deposit_date < end)
    )
    return Decimal(result.scalar_one())
