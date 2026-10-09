"""Месячные лимиты трат пространства (семейного чата или «Моих финансов»)."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select

from app.api.dependencies import ChatContext, get_chat_context
from app.shared.budgets import budget_label, budgets_status, month_range, spending
from app.shared import obligations as ob
from app.shared.forecast import forecast
from app.shared.models import Budget, BudgetAlert
from app.shared.schemas import BudgetIn, BudgetOut, ForecastOut

router = APIRouter(tags=["budgets"])


@router.get("/chats/{chat_id}/budgets", response_model=list[BudgetOut])
async def list_budgets(month: str, ctx: ChatContext = Depends(get_chat_context)) -> list[BudgetOut]:
    try:
        month_range(month)
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail="month должен быть в формате YYYY-MM") from exc
    statuses = await budgets_status(ctx.session, ctx.chat, month)
    # Неоплаченные обязательные платежи резервируют деньги в лимитах текущего и будущих месяцев
    reserved: dict = {}
    if statuses and month >= dt.date.today().strftime("%Y-%m"):
        reserved = ob.reserved(await ob.obligations(ctx.session, ctx.chat, month))
        await ctx.session.commit()
    fc = await forecast(ctx.session, ctx.chat, dt.date.today()) if statuses else None
    if fc is not None and fc.month != month:
        fc = None
    # Сначала общий лимит месяца, дальше категории по алфавиту, внутри — сама категория раньше подкатегорий
    statuses.sort(key=lambda s: (s.budget.category != "", s.budget.category, s.budget.subcategory or ""))
    return [
        BudgetOut(
            id=s.budget.id,
            category=s.budget.category,
            subcategory=s.budget.subcategory,
            amount=s.budget.amount,
            spent=s.spent,
            reserved=reserved.get((s.budget.category, s.budget.subcategory), 0),
            forecast=fc.get((s.budget.category, s.budget.subcategory)) if fc else None,
            label=budget_label(s.budget),
        )
        for s in statuses
    ]


@router.get("/chats/{chat_id}/forecast", response_model=ForecastOut | None)
async def get_forecast(ctx: ChatContext = Depends(get_chat_context)) -> ForecastOut | None:
    """Прогноз всех трат пространства на конец текущего месяца; null — рано (до 5-го числа)."""
    fc = await forecast(ctx.session, ctx.chat, dt.date.today())
    await ctx.session.commit()  # obligations() мог завести записи платежей месяца
    if fc is None:
        return None
    spent = (await spending(ctx.session, ctx.chat, fc.month)).get(("", None), 0)
    return ForecastOut(month=fc.month, spent=spent, forecast=fc.get(("", None)), days_left=fc.days_left)


@router.post("/chats/{chat_id}/budgets", response_model=BudgetOut, status_code=201)
async def upsert_budget(payload: BudgetIn, ctx: ChatContext = Depends(get_chat_context)) -> BudgetOut:
    """Задать лимит (или изменить существующий на ту же категорию/подкатегорию)."""
    category = payload.category.strip()
    subcategory = (payload.subcategory or "").strip() or None
    if not category and subcategory:
        raise HTTPException(status_code=400, detail="Подкатегория без категории")

    # NULL в UNIQUE не сравнивается, поэтому ищем существующий лимит явно
    query = select(Budget).where(Budget.chat_id == ctx.chat.id, Budget.category == category)
    query = query.where(Budget.subcategory.is_(None) if subcategory is None else Budget.subcategory == subcategory)
    budget = (await ctx.session.execute(query)).scalar_one_or_none()
    if budget is None:
        budget = Budget(chat_id=ctx.chat.id, category=category, subcategory=subcategory, amount=payload.amount)
        ctx.session.add(budget)
    elif budget.amount != payload.amount:
        budget.amount = payload.amount
        # Лимит изменили — уведомления этого месяца должны сработать заново по новой сумме
        await ctx.session.execute(delete(BudgetAlert).where(BudgetAlert.budget_id == budget.id))
    await ctx.session.commit()
    return BudgetOut(
        id=budget.id,
        category=budget.category,
        subcategory=budget.subcategory,
        amount=budget.amount,
        spent=0,
        label=budget_label(budget),
    )


@router.delete("/chats/{chat_id}/budgets/{budget_id}", status_code=204)
async def delete_budget(budget_id: int, ctx: ChatContext = Depends(get_chat_context)):
    budget = await ctx.session.get(Budget, budget_id)
    if budget is None or budget.chat_id != ctx.chat.id:
        raise HTTPException(status_code=404, detail="Лимит не найден")
    await ctx.session.delete(budget)
    await ctx.session.commit()
