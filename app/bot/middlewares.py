"""Middleware, который на лету регистрирует в базе чат и автора каждого сообщения,
пришедшего из группы/супергруппы. Это единственный способ узнать участников группы,
так как Telegram Bot API не отдаёт полный список участников чата."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject, Update

from app.bot.avatars import sync_member_avatar
from app.shared.crud import get_or_create_chat, get_or_create_member
from app.shared.database import async_session_maker


class MemberTrackingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        message = None
        if isinstance(event, Update) and event.message is not None:
            message = event.message

        if message is not None and message.chat.type in ("group", "supergroup") and message.from_user:
            if not message.from_user.is_bot:
                bot: Bot | None = data.get("bot")
                async with async_session_maker() as session:
                    await get_or_create_chat(session, message.chat.id, message.chat.title or "")
                    member = await get_or_create_member(
                        session,
                        chat_id=message.chat.id,
                        tg_user_id=message.from_user.id,
                        username=message.from_user.username,
                        full_name=message.from_user.full_name,
                    )
                    if bot is not None:
                        await sync_member_avatar(bot, session, member)
                    await session.commit()

        return await handler(event, data)
