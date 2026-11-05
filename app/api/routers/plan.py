"""План распределения дохода «Моих финансов»: статьи в процентах и их состояние."""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import ChatContext, get_chat_context
from app.shared import plan, plan_analytics
from app.shared.custom_categories import plan_keys, tree_for
from app.shared.models import Goal, GoalDeposit, PlanItem
from app.shared.schemas import (
    PlanEntryOut,
    PlanFlowOut,
    PlanHistoryOut,
    PlanItemIn,
    PlanItemOut,
    PlanOrderIn,
    PlanOut,
    PlanSettingsIn,
)

router = APIRouter(tags=["plan"])


def _personal(ctx: ChatContext) -> None:
    if not ctx.chat.is_personal:
        raise HTTPException(status_code=400, detail="План ведётся только в «Моих финансах»")


def _out(s: plan.PlanSummary) -> PlanOut:
    return PlanOut(
        start=s.start,
        end=s.end - dt.timedelta(days=1),
        start_day=s.start_day,
        income=s.income,
        incomes_count=s.incomes_count,
        carried=s.carried,
        percent_total=s.percent_total,
        items=[
            PlanItemOut(
                id=i.id,
                title=i.title,
                kind=i.kind,
                percent=i.percent,
                allocated=i.allocated,
                spent=i.spent,
                remaining=i.remaining,
                categories=i.categories,
                goal_id=i.goal_id,
                goal_title=i.goal_title,
                goal_saved=i.goal_saved,
                goal_target=i.goal_target,
                carried=i.carried,
                card_debt=i.card_debt,
            )
            for i in s.items
        ],
    )


async def _summary(ctx: ChatContext, date: dt.date | None = None) -> PlanOut:
    return _out(await plan.summary(ctx.session, ctx.chat, date or dt.date.today()))


async def _item(ctx: ChatContext, item_id: int) -> PlanItem:
    item = await ctx.session.get(PlanItem, item_id)
    if item is None or item.chat_id != ctx.chat.id:
        raise HTTPException(status_code=404, detail="Статья не найдена")
    return item


async def _validate(ctx: ChatContext, payload: PlanItemIn, item_id: int | None, date: dt.date) -> None:
    # Проценты у каждого месяца свои: проверяем по статьям того месяца, который правим
    month = [(i, p) for i, p in await plan.month_items(ctx.session, ctx.chat, date) if i.id != item_id]
    others = [i for i, _ in month]
    total = sum((p for _, p in month), plan.ZERO) + payload.percent
    if total > plan.HUNDRED:
        free = plan.HUNDRED - (total - payload.percent)
        raise HTTPException(status_code=400, detail=f"Сумма процентов больше 100%: свободно ещё {free.normalize():f}%")
    if payload.kind == "spend":
        if not payload.categories:
            raise HTTPException(status_code=400, detail="Выберите хотя бы одну категорию трат")
        known = plan_keys(await tree_for(ctx.session, ctx.chat))
        for key in payload.categories:
            if key not in known:
                raise HTTPException(status_code=400, detail=f"Нет такой категории: {key}")
        for other in others:
            if other.kind == "spend":
                taken = set(payload.categories) & {str(c) for c in plan.loads(other.categories)}
                if taken:
                    raise HTTPException(
                        status_code=400,
                        detail=f"«{sorted(taken)[0]}» уже в статье «{other.title}»",
                    )
    elif payload.kind == "goal":
        goal = await ctx.session.get(Goal, payload.goal_id) if payload.goal_id else None
        if goal is None or goal.chat_id != ctx.chat.id:
            raise HTTPException(status_code=400, detail="Выберите цель")
        if any(o.kind == "goal" and o.goal_id == goal.id for o in others):
            raise HTTPException(status_code=400, detail=f"Цель «{goal.title}» уже в другой статье")


def _apply(item: PlanItem, payload: PlanItemIn) -> None:
    """Всё, кроме процента: название, вид, категории и цель у статьи общие для всех месяцев."""
    item.title = payload.title.strip()[:255]
    item.kind = payload.kind
    item.categories = json.dumps(list(dict.fromkeys(payload.categories)) if payload.kind == "spend" else [], ensure_ascii=False)
    item.goal_id = payload.goal_id if payload.kind == "goal" else None
    item.recurring_ids = "[]"


@router.get("/chats/{chat_id}/plan", response_model=PlanOut)
async def get_plan(date: dt.date | None = None, ctx: ChatContext = Depends(get_chat_context)) -> PlanOut:
    """date — любой день нужного месяца плана (по умолчанию сегодня)."""
    _personal(ctx)
    return await _summary(ctx, date)


@router.get("/chats/{chat_id}/plan/entries", response_model=list[PlanEntryOut])
async def get_entries(
    item_id: int | None = None, date: dt.date | None = None, ctx: ChatContext = Depends(get_chat_context)
) -> list[PlanEntryOut]:
    """На что ушли деньги статьи в месяце плана; без item_id — «Свободно»."""
    _personal(ctx)
    if item_id is not None:
        await _item(ctx, item_id)
    entries = await plan.item_entries(ctx.session, ctx.chat, date or dt.date.today(), item_id)
    return [
        PlanEntryOut(date=e.date, title=e.title, amount=e.amount, source=e.source,
                     category=e.category, subcategory=e.subcategory)
        for e in entries
    ]


@router.get("/chats/{chat_id}/plan/history", response_model=PlanHistoryOut)
async def get_plan_history(date: dt.date | None = None, ctx: ChatContext = Depends(get_chat_context)) -> PlanHistoryOut:
    """Статьи за 6 месяцев плана, заканчивая месяцем с date (app/shared/plan_analytics.py)."""
    _personal(ctx)
    return await plan_analytics.history(ctx.session, ctx.chat, date or dt.date.today())


@router.get("/chats/{chat_id}/plan/flow", response_model=PlanFlowOut)
async def get_plan_flow(date: dt.date | None = None, ctx: ChatContext = Depends(get_chat_context)) -> PlanFlowOut:
    """Куда ушли деньги месяца плана с date — для диаграммы Санки."""
    _personal(ctx)
    return await plan_analytics.flow(ctx.session, ctx.chat, date or dt.date.today())


@router.post("/chats/{chat_id}/plan/items", response_model=PlanOut, status_code=201)
async def create_item(
    payload: PlanItemIn, date: dt.date | None = None, ctx: ChatContext = Depends(get_chat_context)
) -> PlanOut:
    """date — любой день месяца плана, с которого статья появляется (по умолчанию сегодня)."""
    _personal(ctx)
    date = date or dt.date.today()
    await _validate(ctx, payload, None, date)
    items = await plan.items_of(ctx.session, ctx.chat.id)
    item = PlanItem(chat_id=ctx.chat.id, position=max((i.position for i in items), default=-1) + 1,
                    percent=payload.percent)
    _apply(item, payload)
    ctx.session.add(item)
    await plan.set_percent(ctx.session, ctx.chat, item, date, payload.percent)
    await ctx.session.commit()
    return await _summary(ctx, date)


@router.patch("/chats/{chat_id}/plan/items/{item_id}", response_model=PlanOut)
async def update_item(
    item_id: int, payload: PlanItemIn, date: dt.date | None = None, ctx: ChatContext = Depends(get_chat_context)
) -> PlanOut:
    """Процент меняется только в месяце date (и следующих); прошлые месяцы не трогаем."""
    date = date or dt.date.today()
    item = await _item(ctx, item_id)
    await _validate(ctx, payload, item.id, date)
    _apply(item, payload)
    await plan.set_percent(ctx.session, ctx.chat, item, date, payload.percent)
    await ctx.session.commit()
    return await _summary(ctx, date)


@router.delete("/chats/{chat_id}/plan/items/{item_id}", response_model=PlanOut)
async def delete_item(item_id: int, date: dt.date | None = None, ctx: ChatContext = Depends(get_chat_context)) -> PlanOut:
    """Статья пропадает с месяца date; в прошлых месяцах она остаётся. Если ни в одном
    месяце её не остаётся, удаляем совсем."""
    date = date or dt.date.today()
    item = await _item(ctx, item_id)
    await plan.set_percent(ctx.session, ctx.chat, item, date, None)
    if not await plan.is_used(ctx.session, item):
        await ctx.session.delete(item)
    await ctx.session.commit()
    return await _summary(ctx, date)


@router.put("/chats/{chat_id}/plan/order", response_model=PlanOut)
async def reorder(payload: PlanOrderIn, ctx: ChatContext = Depends(get_chat_context)) -> PlanOut:
    _personal(ctx)
    position = {item_id: n for n, item_id in enumerate(payload.ids)}
    for item in await plan.items_of(ctx.session, ctx.chat.id):
        item.position = position.get(item.id, len(position))
    await ctx.session.commit()
    return await _summary(ctx)


@router.put("/chats/{chat_id}/plan/settings", response_model=PlanOut)
async def update_settings(payload: PlanSettingsIn, ctx: ChatContext = Depends(get_chat_context)) -> PlanOut:
    _personal(ctx)
    ctx.chat.plan_start_day = payload.start_day
    await ctx.session.commit()
    return await _summary(ctx)


@router.post("/chats/{chat_id}/plan/items/{item_id}/save", response_model=PlanOut)
async def save_to_goal(item_id: int, ctx: ChatContext = Depends(get_chat_context)) -> PlanOut:
    """«Отложил»: на цель статьи уходит то, что по плану ещё не отложено в этом месяце."""
    item = await _item(ctx, item_id)
    if item.kind != "goal" or not item.goal_id:
        raise HTTPException(status_code=400, detail="Отложить можно только в накопительную статью")
    s = await plan.summary(ctx.session, ctx.chat, dt.date.today())
    st = next(i for i in s.items if i.id == item.id)
    if st.remaining <= 0:
        raise HTTPException(status_code=400, detail="По плану в этом месяце уже всё отложено")
    ctx.session.add(GoalDeposit(goal_id=item.goal_id, amount=st.remaining, deposit_date=dt.date.today()))
    await ctx.session.commit()
    return await _summary(ctx)
