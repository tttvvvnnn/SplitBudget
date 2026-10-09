"""Информация о чате, список участников, список чатов текущего пользователя."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.dependencies import ChatContext, get_chat_context
from app.shared.categories import CATEGORY_NAMES, category_tree_out
from app.shared.crud import family_chats_of, get_or_create_personal_space
from app.shared.database import get_session
from app.shared.models import (
    Chat,
    Expense,
    ExpenseShare,
    Member,
    RecurringExpense,
    RecurringParticipant,
    Settlement,
)
from app.shared.schemas import CategoryNode, ChatOut, MeOut, MemberCreate, MemberOut, MemberUpdate

router = APIRouter(tags=["chats"])


@router.get("/my-chats", response_model=list[ChatOut])
async def my_chats(
    hidden: bool = False, user: dict = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> list[Chat]:
    """Семейные чаты пользователя для переключателя в шапке (hidden=true — те, что он
    убрал из списка, для экрана «Скрытые чаты»)."""
    return await family_chats_of(session, int(user["id"]), hidden=hidden)


@router.post("/chats/{chat_id}/hide", status_code=204)
async def hide_chat(ctx: ChatContext = Depends(get_chat_context)):
    """Убрать чат из своего списка. Только для себя: чат, траты и долги остаются как есть."""
    if ctx.chat.is_personal:
        raise HTTPException(status_code=400, detail="«Мои финансы» убрать нельзя")
    ctx.member.is_hidden = True
    await ctx.session.commit()


@router.post("/chats/{chat_id}/unhide", status_code=204)
async def unhide_chat(ctx: ChatContext = Depends(get_chat_context)):
    ctx.member.is_hidden = False
    await ctx.session.commit()


@router.get("/personal", response_model=ChatOut)
async def personal_space(
    user: dict = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> Chat:
    """Личное пространство текущего пользователя («Мои финансы») — создаётся при первом
    обращении. Дальше с ним работают обычные /chats/{chat_id}/... эндпоинты."""
    full_name = " ".join(
        part for part in (user.get("first_name"), user.get("last_name")) if part
    ).strip()
    chat, _ = await get_or_create_personal_space(
        session, int(user["id"]), username=user.get("username"), full_name=full_name
    )
    await session.commit()
    return chat


@router.get("/categories", response_model=list[CategoryNode])
async def categories(user: dict = Depends(get_current_user)) -> list[dict]:  # noqa: ARG001
    """Дерево категорий и подкатегорий (app/shared/categories.py) — одно на все пространства."""
    return category_tree_out()


@router.get("/chats/{chat_id}/me", response_model=MeOut)
async def get_me(ctx: ChatContext = Depends(get_chat_context)) -> MeOut:
    result = await ctx.session.execute(
        select(Member).where(Member.chat_id == ctx.chat.id).order_by(Member.full_name)
    )
    members = list(result.scalars().all())
    return MeOut(
        chat=ChatOut.model_validate(ctx.chat),
        member=MemberOut.model_validate(ctx.member),
        members=[MemberOut.model_validate(m) for m in members],
        categories=CATEGORY_NAMES,
    )


@router.get("/chats/{chat_id}/members", response_model=list[MemberOut])
async def list_members(ctx: ChatContext = Depends(get_chat_context)) -> list[Member]:
    result = await ctx.session.execute(
        select(Member).where(ctx.chat.id == Member.chat_id).order_by(Member.full_name)
    )
    return list(result.scalars().all())


@router.post("/chats/{chat_id}/members", response_model=MemberOut, status_code=201)
async def add_manual_member(
    payload: MemberCreate, ctx: ChatContext = Depends(get_chat_context)
) -> Member:
    """Добавляет в чат участника без Telegram-аккаунта (например, ребёнка или родственника
    без своего профиля) — его может добавить любой уже известный боту участник этого чата.
    У такого участника нет tg_user_id: сам он мини-апп открыть не сможет, но может быть
    выбран как «кто оплатил» или как участник трат — кто-то другой отмечает это за него."""
    if ctx.chat.is_personal:
        raise HTTPException(status_code=400, detail="В личное пространство нельзя добавлять участников")
    member = Member(
        chat_id=ctx.chat.id,
        tg_user_id=None,
        username=None,
        full_name=payload.full_name.strip(),
        is_active=True,
    )
    ctx.session.add(member)
    await ctx.session.commit()
    return member


async def _get_manual_member_or_404(ctx: ChatContext, member_id: int) -> Member:
    member = await ctx.session.get(Member, member_id)
    if member is None or member.chat_id != ctx.chat.id:
        raise HTTPException(status_code=404, detail="Участник не найден")
    if not member.is_manual:
        raise HTTPException(
            status_code=400,
            detail="Это участник с Telegram-аккаунтом — его имя и статус синхронизируются "
            "из профиля автоматически, вручную менять нельзя",
        )
    return member


@router.patch("/chats/{chat_id}/members/{member_id}", response_model=MemberOut)
async def rename_manual_member(
    member_id: int, payload: MemberUpdate, ctx: ChatContext = Depends(get_chat_context)
) -> Member:
    """Переименовать участника без Telegram-аккаунта (см. add_manual_member) — например,
    поправить опечатку в имени. Для настоящих Telegram-участников запрещено: их имя всегда
    берётся из профиля."""
    member = await _get_manual_member_or_404(ctx, member_id)
    member.full_name = payload.full_name.strip()
    await ctx.session.commit()
    return member


@router.delete("/chats/{chat_id}/members/{member_id}", status_code=204)
async def delete_manual_member(member_id: int, ctx: ChatContext = Depends(get_chat_context)):
    """Удалить участника без Telegram-аккаунта — только если за ним ещё не числится ни
    одной траты, доли, погашения или шаблона повтора (иначе каскад по FK снёс бы их вместе
    с участником). Если такое обнаружено — 400 с понятным сообщением; проще создать нового
    участника или оставить этого, чем терять историю."""
    member = await _get_manual_member_or_404(ctx, member_id)

    checks = (
        select(Expense.id).where(
            (Expense.payer_member_id == member_id) | (Expense.created_by_member_id == member_id)
        ),
        select(ExpenseShare.id).where(ExpenseShare.member_id == member_id),
        select(Settlement.id).where(
            (Settlement.from_member_id == member_id)
            | (Settlement.to_member_id == member_id)
            | (Settlement.created_by_member_id == member_id)
        ),
        select(RecurringExpense.id).where(
            (RecurringExpense.payer_member_id == member_id)
            | (RecurringExpense.created_by_member_id == member_id)
        ),
        select(RecurringParticipant.id).where(RecurringParticipant.member_id == member_id),
    )
    for query in checks:
        result = await ctx.session.execute(query.limit(1))
        if result.first() is not None:
            raise HTTPException(
                status_code=400,
                detail="Нельзя удалить: за этим участником уже числятся траты или платежи",
            )

    await ctx.session.delete(member)
    await ctx.session.commit()
