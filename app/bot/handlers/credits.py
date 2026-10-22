"""Кнопки под напоминаниями о кредитной карте (app/shared/credits.py, due_card_notices):
«✅ Внёс <мин. платёж>», «✅ Погасил весь долг» и «📅 Продлить срок на месяц» — обе даты
на месяц вперёд, долг тот же."""
from __future__ import annotations

import logging
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.shared import credits
from app.shared import obligations as ob
from app.shared.database import async_session_maker
from app.shared.models import Chat, CreditCard

logger = logging.getLogger(__name__)
router = Router(name="credits")


def _date(value) -> str:
    return ob._date_label(value) if value else "—"


@router.callback_query(F.data.startswith("cc:"))
async def on_card_button(callback: CallbackQuery) -> None:
    try:
        _, action, raw_id = callback.data.split(":")
        card_id = int(raw_id)
    except ValueError:
        await callback.answer()
        return

    async with async_session_maker() as session:
        card = await session.get(CreditCard, card_id)
        # Карта лежит в «Моих финансах» владельца: id пространства = его tg id
        if card is None or card.chat_id != callback.from_user.id:
            await callback.answer("Карта не найдена", show_alert=True)
            return
        chat = await session.get(Chat, card.chat_id)
        try:
            if action == "min":
                await credits.pay(session, card, card.min_payment)
                result = f"✅ «{escape(card.title)}»: внесено {ob.fmt_amount(card.min_payment, chat.currency)}"
            elif action == "full":
                debt = await credits.debt_of(session, card)
                await credits.pay(session, card, debt)
                result = f"✅ «{escape(card.title)}»: долг {ob.fmt_amount(debt, chat.currency)} погашен"
            elif action == "ext":
                await credits.extend_month(session, card)
                result = (
                    f"📅 «{escape(card.title)}»: срок продлён, долг тот же\n"
                    f"Без процентов до {_date(card.grace_until)}"
                )
                if card.payment_date:
                    result += f"\nМин. платёж до {_date(card.payment_date)}"
            else:
                await callback.answer()
                return
        except (ValueError, TypeError) as exc:
            await callback.answer(str(exc) if isinstance(exc, ValueError) else "Не получилось", show_alert=True)
            return
        debt = await credits.debt_of(session, card)
        await session.commit()

    if action != "ext":
        result += f"\nОсталось: {ob.fmt_amount(debt, chat.currency)}"
    try:
        await callback.message.edit_text(result)
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось обновить сообщение о карте", exc_info=True)
    await callback.answer()
