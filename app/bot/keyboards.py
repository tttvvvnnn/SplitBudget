"""Клавиатуры для открытия mini app."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from app.shared.config import settings


def open_app_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Кнопка «Открыть учёт трат», передающая id семейного чата в mini app через query-параметр.
    Работает и в группах, и в личных сообщениях с ботом."""
    url = f"{settings.WEBAPP_URL}/?chat_id={chat_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💸 Открыть учёт трат", web_app=WebAppInfo(url=url))]]
    )
