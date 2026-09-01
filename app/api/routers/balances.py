"""Баланс участников, упрощённый список долгов и ручное погашение (settle up)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.dependencies import ChatContext, get_chat_context
from app.bot.notify import notify_settlement
from app.shared.balance import compute_net_balances, simplify_debts
from app.shared.models import Member, Settlement
from app.shared.schemas import (
    BalanceEntry,
    BalancesOut,
    DebtEntry,
    SettlementCreate,
    SettlementOut,
)

router = APIRouter(tags=["balances"])


@router.get("/chats/{chat_id}/balances", response_model=BalancesOut)
async def get_balances(ctx: ChatContext = Depends(get_chat_context)) -> BalancesOut:
    balances = await compute_net_balances(ctx.session, ctx.chat.id)
    debts = simplify_debts(balances)
    return BalancesOut(
        balances=[BalanceEntry(member_id=mid, net=net) for mid, net in balances.items()],
        simplified_debts=[
            DebtEntry(from_member_id=f, to_member_id=t, amount=a) for f, t, a in debts
        ],
    )


@router.get("/chats/{chat_id}/settlements", response_model=list[SettlementOut])
async def list_settlements(ctx: ChatContext = Depends(get_chat_context)) -> list[Settlement]:
    result = await ctx.session.execute(
        select(Settlement)
        .where(Settlement.chat_id == ctx.chat.id)
        .order_by(Settlement.created_at.desc())
        .limit(200)
    )
    return list(result.scalars().all())


@router.post("/chats/{chat_id}/settlements", response_model=SettlementOut, status_code=201)
async def create_settlement(
    payload: SettlementCreate, ctx: ChatContext = Depends(get_chat_context)
) -> Settlement:
    if payload.from_member_id == payload.to_member_id:
        raise HTTPException(status_code=400, detail="Нельзя погасить долг самому себе")

    result = await ctx.session.execute(
        select(Member).where(
            Member.chat_id == ctx.chat.id,
            Member.id.in_([payload.from_member_id, payload.to_member_id]),
        )
    )
    members = {m.id: m for m in result.scalars().all()}
    if payload.from_member_id not in members or payload.to_member_id not in members:
        raise HTTPException(status_code=400, detail="Неизвестный участник")

    settlement = Settlement(
        chat_id=ctx.chat.id,
        from_member_id=payload.from_member_id,
        to_member_id=payload.to_member_id,
        amount=payload.amount,
        note=payload.note,
        created_by_member_id=ctx.member.id,
    )
    ctx.session.add(settlement)
    await ctx.session.commit()

    await notify_settlement(
        ctx.chat, members[payload.from_member_id], members[payload.to_member_id], payload.amount
    )
    return settlement
