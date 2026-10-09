"""Доходы «Моих финансов»: поступления, регулярные доходы и сводка «сколько свободно»."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.dependencies import ChatContext, get_chat_context
from app.shared import income as inc
from app.shared.budgets import month_range
from app.shared.models import Income, IncomeSource
from app.shared.schemas import IncomeIn, IncomeOut, IncomeSourceIn, IncomeSourceOut, IncomeSummaryOut

router = APIRouter(tags=["income"])


def _personal(ctx: ChatContext) -> None:
    if not ctx.chat.is_personal:
        raise HTTPException(status_code=400, detail="Доходы ведутся только в «Моих финансах»")


@router.get("/chats/{chat_id}/income", response_model=IncomeSummaryOut)
async def income_summary(month: str, ctx: ChatContext = Depends(get_chat_context)) -> IncomeSummaryOut:
    _personal(ctx)
    try:
        month_range(month)
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail="month должен быть в формате YYYY-MM") from exc
    s = await inc.summary(ctx.session, ctx.chat, month, dt.date.today())
    await ctx.session.commit()  # платежи месяца создаются при первом обращении
    return IncomeSummaryOut(
        incomes=[IncomeOut.model_validate(i) for i in s.incomes],
        sources=[
            IncomeSourceOut(
                id=st.source.id,
                title=st.source.title,
                amount=st.source.amount,
                day_of_month=st.source.day_of_month,
                is_active=st.source.is_active,
                date=st.date,
                received=st.income is not None,
            )
            for st in s.sources
        ],
        received=s.received,
        expected=s.expected,
        spent=s.spent,
        obligations_pending=s.obligations_pending,
        free=s.free,
        next_title=s.next_title,
        next_date=s.next_date,
        next_amount=s.next_amount,
        days_to_next=s.days_to_next,
        obligations_before_next=s.obligations_before_next,
        per_day=s.per_day,
    )


@router.post("/chats/{chat_id}/income", response_model=IncomeOut, status_code=201)
async def add_income(payload: IncomeIn, ctx: ChatContext = Depends(get_chat_context)) -> IncomeOut:
    _personal(ctx)
    if payload.source_id is not None:
        source = await ctx.session.get(IncomeSource, payload.source_id)
        if source is None or source.chat_id != ctx.chat.id:
            raise HTTPException(status_code=404, detail="Регулярный доход не найден")
    income = await inc.record_income(
        ctx.session, ctx.chat, payload.title, payload.amount, payload.income_date or dt.date.today(), payload.source_id
    )
    await ctx.session.commit()
    return IncomeOut.model_validate(income)


@router.patch("/chats/{chat_id}/income/{income_id}", response_model=IncomeOut)
async def update_income(income_id: int, payload: IncomeIn, ctx: ChatContext = Depends(get_chat_context)) -> IncomeOut:
    income = await ctx.session.get(Income, income_id)
    if income is None or income.chat_id != ctx.chat.id:
        raise HTTPException(status_code=404, detail="Доход не найден")
    income.title = payload.title.strip()[:255] or "Доход"
    income.amount = payload.amount
    if payload.income_date is not None:
        income.income_date = payload.income_date
    await ctx.session.commit()
    return IncomeOut.model_validate(income)


@router.delete("/chats/{chat_id}/income/{income_id}", status_code=204)
async def delete_income(income_id: int, ctx: ChatContext = Depends(get_chat_context)):
    income = await ctx.session.get(Income, income_id)
    if income is None or income.chat_id != ctx.chat.id:
        raise HTTPException(status_code=404, detail="Доход не найден")
    await ctx.session.delete(income)
    await ctx.session.commit()


@router.get("/chats/{chat_id}/income-sources", response_model=list[IncomeSourceOut])
async def list_sources(ctx: ChatContext = Depends(get_chat_context)) -> list[IncomeSourceOut]:
    _personal(ctx)
    result = await ctx.session.execute(
        select(IncomeSource).where(IncomeSource.chat_id == ctx.chat.id).order_by(IncomeSource.day_of_month)
    )
    return [IncomeSourceOut.model_validate(s) for s in result.scalars().all()]


@router.post("/chats/{chat_id}/income-sources", response_model=IncomeSourceOut, status_code=201)
async def add_source(payload: IncomeSourceIn, ctx: ChatContext = Depends(get_chat_context)) -> IncomeSourceOut:
    _personal(ctx)
    source = IncomeSource(
        chat_id=ctx.chat.id,
        title=payload.title.strip()[:255],
        amount=payload.amount,
        day_of_month=payload.day_of_month,
        is_active=payload.is_active,
    )
    ctx.session.add(source)
    await ctx.session.commit()
    return IncomeSourceOut.model_validate(source)


@router.patch("/chats/{chat_id}/income-sources/{source_id}", response_model=IncomeSourceOut)
async def update_source(
    source_id: int, payload: IncomeSourceIn, ctx: ChatContext = Depends(get_chat_context)
) -> IncomeSourceOut:
    source = await ctx.session.get(IncomeSource, source_id)
    if source is None or source.chat_id != ctx.chat.id:
        raise HTTPException(status_code=404, detail="Регулярный доход не найден")
    source.title = payload.title.strip()[:255]
    source.amount = payload.amount
    source.day_of_month = payload.day_of_month
    source.is_active = payload.is_active
    await ctx.session.commit()
    return IncomeSourceOut.model_validate(source)


@router.delete("/chats/{chat_id}/income-sources/{source_id}", status_code=204)
async def delete_source(source_id: int, ctx: ChatContext = Depends(get_chat_context)):
    source = await ctx.session.get(IncomeSource, source_id)
    if source is None or source.chat_id != ctx.chat.id:
        raise HTTPException(status_code=404, detail="Регулярный доход не найден")
    await ctx.session.delete(source)
    await ctx.session.commit()
