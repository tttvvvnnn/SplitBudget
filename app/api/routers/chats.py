"""Информация о чате, список участников, список чатов текущего пользователя."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.dependencies import ChatContext, get_chat_context
from app.shared.config import settings
from app.shared.database import get_session
from app.shared.models import Chat, Member
from app.shared.schemas import ChatOut, MeOut, MemberOut

router = APIRouter(tags=["chats"])


@router.get("/my-chats", response_model=list[ChatOut])
async def my_chats(
    user: dict = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> list[Chat]:
    """Список семейных чатов, где пользователь уже известен боту — используется, когда
    приложение открыто без явного chat_id (например, через кнопку меню бота в личке)."""
    tg_user_id = int(user["id"])
    result = await session.execute(
        select(Member.chat_id).where(Member.tg_user_id == tg_user_id, Member.is_active.is_(True))
    )
    chat_ids = [row[0] for row in result.all()]
    if not chat_ids:
        return []
    chats_result = await session.execute(select(Chat).where(Chat.id.in_(chat_ids)))
    return list(chats_result.scalars().all())


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
        categories=settings.DEFAULT_CATEGORIES,
    )


@router.get("/chats/{chat_id}/members", response_model=list[MemberOut])
async def list_members(ctx: ChatContext = Depends(get_chat_context)) -> list[Member]:
    result = await ctx.session.execute(
        select(Member).where(ctx.chat.id == Member.chat_id).order_by(Member.full_name)
    )
    return list(result.scalars().all())
