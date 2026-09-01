"""Синхронизация фото профиля участника из Telegram — используется как аватарка в mini app."""
from __future__ import annotations

import logging

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.config import settings
from app.shared.models import Member

logger = logging.getLogger(__name__)


async def sync_member_avatar(bot: Bot, session: AsyncSession, member: Member) -> None:
    """Один раз (пока avatar_path пуст) забирает у Telegram текущее фото профиля участника
    и сохраняет локально — так же, как фото чеков, чтобы отдавать его через тот же
    защищённый эндпоинт GET /chats/{chat_id}/photos/{filename}. Ошибки (нет сети, нет фото
    у пользователя, приватность профиля закрыта и т.п.) намеренно не всплывают наружу —
    это не должно ломать обработку сообщения/команды, из-за которой участник был создан."""
    if member.avatar_path:
        return
    try:
        photos = await bot.get_user_profile_photos(member.tg_user_id, limit=1)
        if not photos.photos:
            return
        largest = photos.photos[0][-1]  # последний размер в списке — самый крупный
        filename = f"{member.chat_id}_avatar_{member.tg_user_id}.jpg"
        path = settings.photos_dir / filename
        await bot.download(largest, destination=path)
        member.avatar_path = filename
        await session.flush()
    except Exception:  # noqa: BLE001 — см. пояснение выше
        logger.warning("Не удалось получить аватар участника %s", member.tg_user_id, exc_info=True)
