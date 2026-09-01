"""Статистика трат по категориям за период."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from app.api.dependencies import ChatContext, get_chat_context
from app.shared.models import Expense
from app.shared.schemas import CategoryStat, StatsOut

router = APIRouter(tags=["stats"])


@router.get("/chats/{chat_id}/stats", response_model=StatsOut)
async def get_stats(month: str, ctx: ChatContext = Depends(get_chat_context)) -> StatsOut:
    try:
        year, mon = (int(p) for p in month.split("-"))
        start = dt.date(year, mon, 1)
        end = dt.date(year + (mon // 12), (mon % 12) + 1, 1)
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail="month должен быть в формате YYYY-MM") from exc

    result = await ctx.session.execute(
        select(Expense.category, func.sum(Expense.amount), func.count(Expense.id))
        .where(Expense.chat_id == ctx.chat.id, Expense.expense_date >= start, Expense.expense_date < end)
        .group_by(Expense.category)
        .order_by(func.sum(Expense.amount).desc())
    )
    rows = result.all()
    total = sum((row[1] for row in rows), Decimal("0"))
    return StatsOut(
        period=month,
        total=total,
        by_category=[CategoryStat(category=r[0], total=r[1], count=r[2]) for r in rows],
    )
