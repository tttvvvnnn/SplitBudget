"""Клавиатуры для открытия mini app."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.shared.config import settings


def open_app_keyboard_group(bot_username: str, chat_id: int) -> InlineKeyboardMarkup:
    """Кнопка «Открыть учёт трат» для групп. web_app-кнопки в группах Telegram запрещает,
    поэтому используется Direct Link Mini App (t.me/<bot>?startapp=...) — обычная url-кнопка,
    которую Telegram при нажатии сам разворачивает в то же mini app поверх текущего чата.
    id чата передаётся через startapp и читается на фронтенде как initDataUnsafe.start_param."""
    url = f"https://t.me/{bot_username}?startapp={chat_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💸 Открыть учёт трат", url=url)]]
    )

def payment_keyboard(bot_username: str, payment_id: int, chat_id: int) -> InlineKeyboardMarkup:
    """Кнопки под вопросом «Оплачено?» об обязательном платеже. «Другая сумма» открывает
    вкладку «Лимиты» нужного пространства (суффикс _pay в startapp, см. webapp/js/app.js)."""
    url = f"https://t.me/{bot_username}?startapp={chat_id}_pay"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Оплачено", callback_data=f"rp:pay:{payment_id}"),
                InlineKeyboardButton(text="⏭ Не в этом месяце", callback_data=f"rp:skip:{payment_id}"),
            ],
            [InlineKeyboardButton(text="✏️ Другая сумма", url=url)],
        ]
    )
