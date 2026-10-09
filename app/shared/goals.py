"""Цели накоплений «Моих финансов».

Накоплено — сумма пополнений (снятие — отрицательное пополнение). Если у цели есть срок,
считаем, сколько нужно откладывать в месяц, включая текущий: (цель − накоплено) / месяцев.
Отложенное в месяце вычитается из «свободно» (app/shared/income.py).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.budgets import month_range
from app.shared.models import Goal, GoalDeposit

ZERO = Decimal("0")


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
        out.append(GoalStatus(goal, saved, this_month, left, needed, list(deposits)))
    return out


async def saved_in_month(session: AsyncSession, chat_id: int, month: str) -> Decimal:
    start, end = month_range(month)
    result = await session.execute(
        select(func.coalesce(func.sum(GoalDeposit.amount), 0))
        .join(Goal, Goal.id == GoalDeposit.goal_id)
        .where(Goal.chat_id == chat_id, GoalDeposit.deposit_date >= start, GoalDeposit.deposit_date < end)
    )
    return Decimal(result.scalar_one())
