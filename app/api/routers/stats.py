"""Статистика трат за период (см. app/shared/stats.py)."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.dependencies import ChatContext, get_chat_context
from app.shared.models import Expense
from app.shared.schemas import StatsOut
from app.shared.stats import Row, build_stats, fetch_range, stats_period

router = APIRouter(tags=["stats"])

PERIOD_ERROR = "Нужен month=YYYY-MM или date_from и date_to (YYYY-MM-DD), не длиннее 3 лет"


def period_or_400(month: str | None, date_from: dt.date | None, date_to: dt.date | None) -> tuple[dt.date, dt.date]:
    try:
        return stats_period(month, date_from, date_to)
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail=PERIOD_ERROR) from exc


@router.get("/chats/{chat_id}/stats", response_model=StatsOut)
async def get_stats(
    month: str | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    ctx: ChatContext = Depends(get_chat_context),
) -> StatsOut:
    start, end = period_or_400(month, date_from, date_to)
    fetch_start, fetch_end = fetch_range(start, end)
    result = await ctx.session.execute(
        select(
            Expense.expense_date, Expense.category, Expense.subcategory, Expense.amount,
            Expense.title, Expense.recurring_id.is_not(None),
        ).where(Expense.chat_id == ctx.chat.id, Expense.expense_date >= fetch_start, Expense.expense_date < fetch_end)
    )
    return build_stats(start, end, [Row(*r) for r in result.all()])
