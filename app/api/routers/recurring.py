"""CRUD шаблонов повторяющихся трат (аренда, подписки)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.dependencies import ChatContext, get_chat_context
from app.shared.crud import build_custom_shares, build_equal_shares
from app.shared.models import Member, RecurringExpense, RecurringParticipant
from app.shared.schemas import RecurringCreate, RecurringOut, RecurringParticipantIn, RecurringUpdate

router = APIRouter(tags=["recurring"])


async def _validate_members(session, chat_id: int, ids: set[int]) -> None:
    if not ids:
        return
    result = await session.execute(
        select(Member.id).where(Member.chat_id == chat_id, Member.id.in_(ids))
    )
    found = {row[0] for row in result.all()}
    missing = ids - found
    if missing:
        raise HTTPException(status_code=400, detail=f"Неизвестные участники: {sorted(missing)}")


def _validate_split(amount, split_type: str, participants: list[RecurringParticipantIn]) -> None:
    """Просто проверяем корректность на этапе создания, не сохраняя результат — реальный расчёт
    долей происходит при генерации конкретной траты планировщиком."""
    if split_type == "equal":
        build_equal_shares(amount, [p.member_id for p in participants])
    else:
        pairs = [(p.member_id, p.custom_amount) for p in participants if p.custom_amount is not None]
        build_custom_shares(amount, pairs)


def _to_out(r: RecurringExpense, participants: list[RecurringParticipant]) -> RecurringOut:
    # Не присваиваем в r.participants (relationship) — на AsyncSession это провоцирует
    # неявный синхронный lazy-load вне greenlet-контекста (MissingGreenlet).
    return RecurringOut(
        id=r.id,
        title=r.title,
        amount=r.amount,
        category=r.category,
        payer_member_id=r.payer_member_id,
        split_type=r.split_type,
        day_of_month=r.day_of_month,
        is_active=r.is_active,
        participants=[
            RecurringParticipantIn(member_id=p.member_id, custom_amount=p.custom_amount)
            for p in participants
        ],
    )


@router.get("/chats/{chat_id}/recurring", response_model=list[RecurringOut])
async def list_recurring(ctx: ChatContext = Depends(get_chat_context)):
    result = await ctx.session.execute(
        select(RecurringExpense).where(RecurringExpense.chat_id == ctx.chat.id)
    )
    items = result.scalars().unique().all()
    out = []
    for r in items:
        p_result = await ctx.session.execute(
            select(RecurringParticipant).where(RecurringParticipant.recurring_id == r.id)
        )
        out.append(_to_out(r, list(p_result.scalars().all())))
    return out


@router.post("/chats/{chat_id}/recurring", response_model=RecurringOut, status_code=201)
async def create_recurring(payload: RecurringCreate, ctx: ChatContext = Depends(get_chat_context)):
    ids = {p.member_id for p in payload.participants} | {payload.payer_member_id}
    await _validate_members(ctx.session, ctx.chat.id, ids)
    try:
        _validate_split(payload.amount, payload.split_type, payload.participants)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    recurring = RecurringExpense(
        chat_id=ctx.chat.id,
        title=payload.title.strip()[:255],
        amount=payload.amount,
        category=payload.category or "Другое",
        payer_member_id=payload.payer_member_id,
        split_type=payload.split_type,
        day_of_month=payload.day_of_month,
        created_by_member_id=ctx.member.id,
    )
    ctx.session.add(recurring)
    await ctx.session.flush()
    for p in payload.participants:
        ctx.session.add(
            RecurringParticipant(
                recurring_id=recurring.id, member_id=p.member_id, custom_amount=p.custom_amount
            )
        )
    await ctx.session.commit()

    p_result = await ctx.session.execute(
        select(RecurringParticipant).where(RecurringParticipant.recurring_id == recurring.id)
    )
    return _to_out(recurring, list(p_result.scalars().all()))


@router.patch("/chats/{chat_id}/recurring/{recurring_id}", response_model=RecurringOut)
async def update_recurring(
    recurring_id: int, payload: RecurringUpdate, ctx: ChatContext = Depends(get_chat_context)
):
    recurring = await ctx.session.get(RecurringExpense, recurring_id)
    if recurring is None or recurring.chat_id != ctx.chat.id:
        raise HTTPException(status_code=404, detail="Шаблон не найден")

    if payload.title is not None:
        recurring.title = payload.title.strip()[:255]
    if payload.amount is not None:
        recurring.amount = payload.amount
    if payload.category is not None:
        recurring.category = payload.category
    if payload.payer_member_id is not None:
        recurring.payer_member_id = payload.payer_member_id
    if payload.split_type is not None:
        recurring.split_type = payload.split_type
    if payload.day_of_month is not None:
        recurring.day_of_month = payload.day_of_month
    if payload.is_active is not None:
        recurring.is_active = payload.is_active

    if payload.participants is not None:
        ids = {p.member_id for p in payload.participants} | {recurring.payer_member_id}
        await _validate_members(ctx.session, ctx.chat.id, ids)
        await ctx.session.execute(
            RecurringParticipant.__table__.delete().where(
                RecurringParticipant.recurring_id == recurring.id
            )
        )
        for p in payload.participants:
            ctx.session.add(
                RecurringParticipant(
                    recurring_id=recurring.id, member_id=p.member_id, custom_amount=p.custom_amount
                )
            )

    await ctx.session.commit()

    p_result = await ctx.session.execute(
        select(RecurringParticipant).where(RecurringParticipant.recurring_id == recurring.id)
    )
    return _to_out(recurring, list(p_result.scalars().all()))


@router.delete("/chats/{chat_id}/recurring/{recurring_id}", status_code=204)
async def delete_recurring(recurring_id: int, ctx: ChatContext = Depends(get_chat_context)):
    recurring = await ctx.session.get(RecurringExpense, recurring_id)
    if recurring is None or recurring.chat_id != ctx.chat.id:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    await ctx.session.delete(recurring)
    await ctx.session.commit()
    return None
