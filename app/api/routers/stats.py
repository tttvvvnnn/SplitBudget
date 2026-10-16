"""Статистика трат за месяц (см. app/shared/stats.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.dependencies import ChatContext, get_chat_context
from app.shared.models import Expense
from app.shared.schemas import StatsOut
from app.shared.stats import build_stats, stats_range

router = APIRouter(tags=["stats"])


@router.get("/chats/{chat_id}/stats", response_model=StatsOut)
async def get_stats(month: str, ctx: ChatContext = Depends(get_chat_context)) -> StatsOut:
    try:
        start, end = stats_range(month)
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail="month должен быть в формате YYYY-MM") from exc

    result = await ctx.session.execute(
        select(Expense.expense_date, Expense.category, Expense.subcategory, Expense.amount).where(
            Expense.chat_id == ctx.chat.id, Expense.expense_date >= start, Expense.expense_date < end
        )
    )
    return build_stats(month, [tuple(r) for r in result.all()])
