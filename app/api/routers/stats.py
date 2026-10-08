"""Статистика трат по категориям за период."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.dependencies import ChatContext, get_chat_context
from app.shared.categories import summarize_by_category
from app.shared.models import Expense
from app.shared.schemas import StatsOut

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
        select(Expense.category, Expense.subcategory, Expense.amount).where(
            Expense.chat_id == ctx.chat.id, Expense.expense_date >= start, Expense.expense_date < end
        )
    )
    total, by_category = summarize_by_category(result.all())
    return StatsOut(period=month, total=total, by_category=by_category)
