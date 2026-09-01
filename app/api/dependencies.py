"""Общие FastAPI-зависимости: аутентифицированный пользователь → участник конкретного
семейного чата, с проверкой членства напрямую через Telegram, если участника ещё нет в базе."""
from __future__ import annotations

from dataclasses import dataclass

from aiogram.exceptions import TelegramBadRequest
from fastapi import Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.bot.avatars import sync_member_avatar
from app.bot.bot_instance import bot
from app.shared.crud import get_or_create_member
from app.shared.database import get_session
from app.shared.models import Chat, Member


@dataclass
class ChatContext:
    chat: Chat
    member: Member
    session: AsyncSession


async def get_chat_context(
    chat_id: int = Path(...),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ChatContext:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(
            status_code=404,
            detail="Этот чат ещё не зарегистрирован. Напишите /start в семейном групповом чате.",
        )

    tg_user_id = int(user["id"])
    result = await session.execute(
        select(Member).where(Member.chat_id == chat_id, Member.tg_user_id == tg_user_id)
    )
    member = result.scalar_one_or_none()

    if member is None or not member.is_active:
        try:
            chat_member = await bot.get_chat_member(chat_id, tg_user_id)
        except TelegramBadRequest as exc:
            raise HTTPException(status_code=403, detail="Вы не участник этого чата") from exc
        if chat_member.status in ("left", "kicked"):
            raise HTTPException(status_code=403, detail="Вы не участник этого чата")

        full_name = " ".join(
            part for part in (user.get("first_name"), user.get("last_name")) if part
        ).strip()
        member = await get_or_create_member(
            session,
            chat_id=chat_id,
            tg_user_id=tg_user_id,
            username=user.get("username"),
            full_name=full_name,
        )
        await sync_member_avatar(bot, session, member)
        await session.commit()

    return ChatContext(chat=chat, member=member, session=session)
