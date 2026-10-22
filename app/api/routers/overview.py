"""Сводка «Моих финансов» по всем пространствам пользователя сразу: личному («Мои финансы») и
всем семейным чатам. Трата считается по реальным деньгам (app/shared/my_spending.py): если в
семейном чате ты заплатил 3 000, а твоя доля 1 000, то твой расход — 3 000, пока остальные
не вернут свои 2 000."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.shared.database import get_session
from app.shared.models import Member
from app.shared.my_spending import my_expenses
from app.shared.schemas import AllExpenseOut, StatsOut
from app.shared.stats import build_stats, stats_range

router = APIRouter(tags=["overview"])


def _month_range(month: str) -> tuple[dt.date, dt.date]:
    try:
        year, mon = (int(p) for p in month.split("-"))
        return dt.date(year, mon, 1), dt.date(year + (mon // 12), (mon % 12) + 1, 1)
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail="month должен быть в формате YYYY-MM") from exc


@router.get("/all/expenses", response_model=list[AllExpenseOut])
async def all_expenses(
    month: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AllExpenseOut]:
    rows = await my_expenses(session, int(user["id"]), *_month_range(month))
    payer_ids = {m.expense.payer_member_id for m in rows}
    payers: dict[int, Member] = {}
    if payer_ids:
        payers_result = await session.execute(select(Member).where(Member.id.in_(payer_ids)))
        payers = {m.id: m for m in payers_result.scalars().all()}

    out = []
    for row in rows:
        expense, chat = row.expense, row.chat
        payer = payers.get(expense.payer_member_id)
        out.append(
            AllExpenseOut(
                id=expense.id,
                chat_id=chat.id,
                chat_title=chat.title,
                is_personal=chat.is_personal,
                title=expense.title,
                amount=expense.amount,
                my_share=row.amount,
                category=expense.category,
                subcategory=expense.subcategory,
                photo_url=(f"photos/{expense.photo_path}" if expense.photo_path else None),
                expense_date=expense.expense_date,
                payer_name=payer.full_name if payer else "",
                i_paid=row.i_paid,
                is_recurring=expense.recurring_id is not None,
                card_id=expense.card_id if row.i_paid else None,
            )
        )
    return out


@router.get("/all/stats", response_model=StatsOut)
async def all_stats(
    month: str,
    full: bool = False,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StatsOut:
    """full=true — полные суммы трат, в которых пользователь участвует (переключатель
    «всего» в «Моих финансах»), иначе — сколько из них на самом деле потратил он."""
    try:
        start, end = stats_range(month)
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail="month должен быть в формате YYYY-MM") from exc
    rows = await my_expenses(session, int(user["id"]), start, end)
    return build_stats(month, [
        (m.expense.expense_date, m.expense.category, m.expense.subcategory, m.expense.amount if full else m.amount)
        for m in rows
        if full or m.amount
    ])
