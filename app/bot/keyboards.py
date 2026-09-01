"""Клавиатуры для открытия mini app."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from app.shared.config import settings


def open_app_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Кнопка «Открыть учёт трат» с web_app — Telegram разрешает такие кнопки ТОЛЬКО в личных
    чатах с ботом (см. официальную документацию InlineKeyboardButton.web_app: "Available only
    in private chats between a user and the bot"). В группе Telegram отклонит всё сообщение
    целиком — для групп используйте open_app_keyboard_group ниже."""
    url = f"{settings.WEBAPP_URL}/?chat_id={chat_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💸 Открыть учёт трат", web_app=WebAppInfo(url=url))]]
    )


def open_app_keyboard_group(bot_username: str, chat_id: int) -> InlineKeyboardMarkup:
    """Кнопка «Открыть учёт трат» для групп. web_app-кнопки в группах Telegram запрещает,
    поэтому используется Direct Link Mini App (t.me/<bot>?startapp=...) — обычная url-кнопка,
    которую Telegram при нажатии сам разворачивает в то же mini app поверх текущего чата.
    id чата передаётся через startapp и читается на фронтенде как initDataUnsafe.start_param."""
    url = f"https://t.me/{bot_username}?startapp={chat_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💸 Открыть учёт трат", url=url)]]
    )