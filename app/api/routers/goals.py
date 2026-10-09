"""Цели накоплений «Моих финансов» и пополнения."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import ChatContext, get_chat_context
from app.shared.goals import GoalStatus, goals_status
from app.shared.models import Goal, GoalDeposit
from app.shared.schemas import GoalDepositIn, GoalDepositOut, GoalIn, GoalOut

router = APIRouter(tags=["goals"])

RECENT_DEPOSITS = 20


def _personal(ctx: ChatContext) -> None:
    if not ctx.chat.is_personal:
        raise HTTPException(status_code=400, detail="Цели ведутся только в «Моих финансах»")


def _out(st: GoalStatus) -> GoalOut:
    return GoalOut(
        id=st.goal.id,
        title=st.goal.title,
        target=st.goal.target,
        deadline=st.goal.deadline,
        is_archived=st.goal.is_archived,
        saved=st.saved,
        saved_this_month=st.saved_this_month,
        months_left=st.months_left,
        monthly_needed=st.monthly_needed,
        deposits=[GoalDepositOut.model_validate(d) for d in st.deposits[:RECENT_DEPOSITS]],
    )


async def _goal(ctx: ChatContext, goal_id: int) -> Goal:
    goal = await ctx.session.get(Goal, goal_id)
    if goal is None or goal.chat_id != ctx.chat.id:
        raise HTTPException(status_code=404, detail="Цель не найдена")
    return goal


async def _one(ctx: ChatContext, goal_id: int) -> GoalOut:
    statuses = await goals_status(ctx.session, ctx.chat.id, dt.date.today())
    return _out(next(st for st in statuses if st.goal.id == goal_id))


@router.get("/chats/{chat_id}/goals", response_model=list[GoalOut])
async def list_goals(ctx: ChatContext = Depends(get_chat_context)) -> list[GoalOut]:
    _personal(ctx)
    return [_out(st) for st in await goals_status(ctx.session, ctx.chat.id, dt.date.today())]


@router.post("/chats/{chat_id}/goals", response_model=GoalOut, status_code=201)
async def create_goal(payload: GoalIn, ctx: ChatContext = Depends(get_chat_context)) -> GoalOut:
    _personal(ctx)
    goal = Goal(chat_id=ctx.chat.id, title=payload.title.strip()[:255], target=payload.target, deadline=payload.deadline)
    ctx.session.add(goal)
    await ctx.session.commit()
    return await _one(ctx, goal.id)


@router.patch("/chats/{chat_id}/goals/{goal_id}", response_model=GoalOut)
async def update_goal(goal_id: int, payload: GoalIn, ctx: ChatContext = Depends(get_chat_context)) -> GoalOut:
    goal = await _goal(ctx, goal_id)
    goal.title = payload.title.strip()[:255]
    goal.target = payload.target
    goal.deadline = payload.deadline
    goal.is_archived = payload.is_archived
    await ctx.session.commit()
    return await _one(ctx, goal.id)


@router.delete("/chats/{chat_id}/goals/{goal_id}", status_code=204)
async def delete_goal(goal_id: int, ctx: ChatContext = Depends(get_chat_context)):
    goal = await _goal(ctx, goal_id)
    await ctx.session.delete(goal)
    await ctx.session.commit()


@router.post("/chats/{chat_id}/goals/{goal_id}/deposits", response_model=GoalOut, status_code=201)
async def add_deposit(goal_id: int, payload: GoalDepositIn, ctx: ChatContext = Depends(get_chat_context)) -> GoalOut:
    goal = await _goal(ctx, goal_id)
    ctx.session.add(
        GoalDeposit(goal_id=goal.id, amount=payload.amount, deposit_date=payload.deposit_date or dt.date.today())
    )
    await ctx.session.commit()
    return await _one(ctx, goal.id)


@router.delete("/chats/{chat_id}/goals/{goal_id}/deposits/{deposit_id}", response_model=GoalOut)
async def delete_deposit(goal_id: int, deposit_id: int, ctx: ChatContext = Depends(get_chat_context)) -> GoalOut:
    goal = await _goal(ctx, goal_id)
    deposit = await ctx.session.get(GoalDeposit, deposit_id)
    if deposit is None or deposit.goal_id != goal.id:
        raise HTTPException(status_code=404, detail="Пополнение не найдено")
    await ctx.session.delete(deposit)
    await ctx.session.commit()
    return await _one(ctx, goal.id)
