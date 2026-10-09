"""Ответ на вопрос «Похоже на подписку — сделать обязательным платежом?» (кнопки sub:add /
sub:no под сообщением планировщика, см. app/shared/subscriptions.py)."""
from __future__ import annotations

import datetime as dt
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.shared.database import async_session_maker
from app.shared.models import Chat, Member, SubscriptionHint
from app.shared import subscriptions

logger = logging.getLogger(__name__)
router = Router(name="subscriptions")


@router.callback_query(F.data.startswith("sub:"))
async def on_subscription_button(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    try:
        action, hint_id = parts[1], int(parts[2])
    except (IndexError, ValueError):
        await callback.answer()
        return
    async with async_session_maker() as session:
        hint = await session.get(SubscriptionHint, hint_id)
        chat = await session.get(Chat, hint.chat_id) if hint else None
        member = None
        if chat is not None:
            member = (
                await session.execute(
                    select(Member).where(
                        Member.chat_id == chat.id,
                        Member.tg_user_id == callback.from_user.id,
                        Member.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
        if hint is None or chat is None or member is None:
            await callback.answer("Это предложение не для вас", show_alert=True)
            return
        if hint.status != "asked":
            await callback.answer("Уже ответили")
            return

        if action == "no":
            await subscriptions.dismiss(session, chat.id, hint.keyword)
            await session.commit()
            text = "✖️ Хорошо, это не подписка — больше не предлагаю."
        elif action == "add":
            candidate = next(
                (c for c in await subscriptions.find_candidates(session, chat, dt.date.today()) if c.keyword == hint.keyword),
                None,
            )
            if candidate is None:
                await callback.answer("Не нашёл эти траты — возможно, их удалили", show_alert=True)
                return
            recurring = await subscriptions.accept(session, chat, candidate, member.id)
            await session.commit()
            text = (
                f"📌 «{recurring.title}» теперь обязательный платёж, {recurring.day_of_month}-го числа. "
                "Изменить или удалить — во вкладке «📌 Платежи»."
            )
        else:
            await callback.answer()
            return
    await callback.answer()
    try:
        await callback.message.edit_text(text)
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось обновить сообщение о подписке", exc_info=True)
