"""Сводка «Моих финансов» по всем пространствам пользователя сразу: личному («Мои финансы») и
всем семейным чатам. Трата считается по реальным деньгам (app/shared/my_spending.py): если в
семейном чате ты заплатил 3 000, а твоя доля 1 000, то твой расход — 3 000, пока остальные
не вернут свои 2 000."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.routers.stats import period_or_400
from app.shared.database import get_session
from app.shared.models import Chat, Income, Member
from app.shared.my_spending import my_expenses
from app.shared.schemas import AllExpenseOut, MonthTotal, StatsOut
from app.shared.stats import ZERO, Row, build_stats, fetch_range, trend_months

router = APIRouter(tags=["overview"])


def _month_range(month: str) -> tuple[dt.date, dt.date]:
    try:
        year, mon = (int(p) for p in month.split("-"))
        return dt.date(year, mon, 1), dt.date(year + (mon // 12), (mon % 12) + 1, 1)
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail="month должен быть в формате YYYY-MM") from exc


@router.get("/all/expenses", response_model=list[AllExpenseOut])
async def all_expenses(
    month: str | None = None,
    date_from: dt.date | None = None,  # или с … по … включительно (статистика за период)
    date_to: dt.date | None = None,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AllExpenseOut]:
    period = _month_range(month) if month else period_or_400(None, date_from, date_to)
    rows = await my_expenses(session, int(user["id"]), *period)
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
            )
        )
    return out


@router.get("/all/stats", response_model=StatsOut)
async def all_stats(
    month: str | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    full: bool = False,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StatsOut:
    """full=true — полные суммы трат, в которых пользователь участвует (переключатель
    «всего» в «Моих финансах»), иначе — сколько из них на самом деле потратил он.
    Плюс доходы из личного пространства — по месяцам тренда и за период."""
    start, end = period_or_400(month, date_from, date_to)
    fetch_start, fetch_end = fetch_range(start, end)
    rows = await my_expenses(session, int(user["id"]), fetch_start, fetch_end)
    stats = build_stats(start, end, [
        Row(m.expense.expense_date, m.expense.category, m.expense.subcategory,
            m.expense.amount if full else m.amount, m.expense.title, m.expense.recurring_id is not None)
        for m in rows
        if full or m.amount
    ])

    incomes = (
        await session.execute(
            select(Income.income_date, Income.amount)
            .join(Chat, Chat.id == Income.chat_id)
            .join(Member, Member.chat_id == Chat.id)
            .where(
                Chat.is_personal.is_(True), Member.tg_user_id == int(user["id"]),
                Income.income_date >= fetch_start, Income.income_date < fetch_end,
            )
        )
    ).all()
    by_month: dict[str, Decimal] = {}
    for date, amount in incomes:
        key = date.strftime("%Y-%m")
        by_month[key] = by_month.get(key, ZERO) + amount
    stats.income_trend = [MonthTotal(month=m, total=by_month.get(m, ZERO)) for m in trend_months(end)]
    stats.income_total = sum((a for d, a in incomes if start <= d < end), ZERO)
    return stats
