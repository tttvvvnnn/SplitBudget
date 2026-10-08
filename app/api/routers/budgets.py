"""Месячные лимиты трат пространства (семейного чата или «Моих финансов»)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select

from app.api.dependencies import ChatContext, get_chat_context
from app.shared.budgets import budget_label, budgets_status, month_range
from app.shared.models import Budget, BudgetAlert
from app.shared.schemas import BudgetIn, BudgetOut

router = APIRouter(tags=["budgets"])


@router.get("/chats/{chat_id}/budgets", response_model=list[BudgetOut])
async def list_budgets(month: str, ctx: ChatContext = Depends(get_chat_context)) -> list[BudgetOut]:
    try:
        month_range(month)
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail="month должен быть в формате YYYY-MM") from exc
    statuses = await budgets_status(ctx.session, ctx.chat, month)
    # Сначала общий лимит месяца, дальше категории по алфавиту, внутри — сама категория раньше подкатегорий
    statuses.sort(key=lambda s: (s.budget.category != "", s.budget.category, s.budget.subcategory or ""))
    return [
        BudgetOut(
            id=s.budget.id,
            category=s.budget.category,
            subcategory=s.budget.subcategory,
            amount=s.budget.amount,
            spent=s.spent,
            label=budget_label(s.budget),
        )
        for s in statuses
    ]


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
