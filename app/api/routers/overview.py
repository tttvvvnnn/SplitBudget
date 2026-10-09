"""Сводка «Моих финансов» по всем пространствам пользователя сразу: личному («Мои финансы») и
всем семейным чатам. Каждая трата считается по доле пользователя в ней (ExpenseShare), а не по
полной сумме: если в семейном чате ты заплатил 3 000, а твоя доля 1 000, то твой расход — 1 000,
остальное тебе должны (это долг, а не трата)."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.shared.categories import summarize_by_category
from app.shared.database import get_session
from app.shared.models import Chat, Expense, ExpenseShare, Member
from app.shared.schemas import AllExpenseOut, StatsOut

router = APIRouter(tags=["overview"])


def _month_range(month: str) -> tuple[dt.date, dt.date]:
    try:
        year, mon = (int(p) for p in month.split("-"))
        return dt.date(year, mon, 1), dt.date(year + (mon // 12), (mon % 12) + 1, 1)
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail="month должен быть в формате YYYY-MM") from exc


async def _my_shares(session: AsyncSession, tg_user_id: int, month: str):
    """Строки (Expense, ExpenseShare, Chat) — траты за месяц, в которых у пользователя есть доля,
    по всем чатам, где он участник (в т.ч. тем, из которых он уже вышел: история остаётся)."""
    start, end = _month_range(month)
    result = await session.execute(
        select(Expense, ExpenseShare, Chat)
        .join(ExpenseShare, ExpenseShare.expense_id == Expense.id)
        .join(Member, Member.id == ExpenseShare.member_id)
        .join(Chat, Chat.id == Expense.chat_id)
        .where(
            Member.tg_user_id == tg_user_id,
            Expense.expense_date >= start,
            Expense.expense_date < end,
        )
        .order_by(Expense.expense_date.desc(), Expense.id.desc())
    )
    return result.all()


@router.get("/all/expenses", response_model=list[AllExpenseOut])
async def all_expenses(
    month: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AllExpenseOut]:
    rows = await _my_shares(session, int(user["id"]), month)
    payer_ids = {e.payer_member_id for e, _, _ in rows}
    payers: dict[int, Member] = {}
    if payer_ids:
        payers_result = await session.execute(select(Member).where(Member.id.in_(payer_ids)))
        payers = {m.id: m for m in payers_result.scalars().all()}

    out = []
    for expense, share, chat in rows:
        payer = payers.get(expense.payer_member_id)
        out.append(
            AllExpenseOut(
                id=expense.id,
                chat_id=chat.id,
                chat_title=chat.title,
                is_personal=chat.is_personal,
                title=expense.title,
                amount=expense.amount,
                my_share=share.amount,
                category=expense.category,
                subcategory=expense.subcategory,
                photo_url=(f"photos/{expense.photo_path}" if expense.photo_path else None),
                expense_date=expense.expense_date,
                payer_name=payer.full_name if payer else "",
                i_paid=expense.payer_member_id == share.member_id,
                is_recurring=expense.recurring_id is not None,
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
    «всего» в «Моих финансах»), иначе — только его доля."""
    rows = await _my_shares(session, int(user["id"]), month)
    total, by_category = summarize_by_category(
        (expense.category, expense.subcategory, expense.amount if full else share.amount)
        for expense, share, _ in rows
    )
    return StatsOut(period=month, total=total, by_category=by_category)
