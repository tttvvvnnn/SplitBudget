"""Вход в веб-версию через бота (app/shared/web_auth.py) и команда /web в личке.

Кнопка «Войти через Telegram» на сайте открывает t.me/<бот>?start=login_<token> — сюда
приходит /start login_<token>, бот спрашивает подтверждение. /web — адрес сайта и выход
из веб-версии на всех устройствах."""
from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.shared import web_auth
from app.shared.config import settings
from app.shared.database import async_session_maker

router = Router(name="web_login")
router.message.filter(F.chat.type == "private")

EXPIRED = "⌛ Ссылка для входа устарела. Нажмите «Войти через Telegram» на сайте ещё раз."


@router.message(CommandStart(deep_link=True, magic=F.args.startswith(web_auth.START_PREFIX)))
async def ask_web_login(message: Message, command: CommandObject) -> None:
    token = command.args.removeprefix(web_auth.START_PREFIX)
    async with async_session_maker() as session:
        login = await web_auth.pending_login(session, token)
    if login is None:
        await message.answer(EXPIRED)
        return
    browser = escape(web_auth.describe_user_agent(login.user_agent))
    await message.answer(
        f"🖥 <b>Вход в веб-версию SplitBudget</b>\n\nБраузер: {browser}\n\n"
        "Подтвердите, только если сами только что нажали «Войти» на сайте. "
        "Если ссылку прислал кто-то другой — нажмите «Отмена»: иначе он получит доступ "
        "к вашим тратам.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="✅ Войти", callback_data=f"wl:ok:{token}"),
                InlineKeyboardButton(text="✖️ Отмена", callback_data=f"wl:no:{token}"),
            ]]
        ),
    )


@router.callback_query(F.data.startswith("wl:ok:"))
async def confirm_web_login(callback: CallbackQuery) -> None:
    token = callback.data.removeprefix("wl:ok:")
    user = callback.from_user
    async with async_session_maker() as session:
        ok = await web_auth.confirm_login(
            session,
            token,
            {k: v for k, v in (
                ("id", user.id), ("first_name", user.first_name),
                ("last_name", user.last_name), ("username", user.username),
            ) if v is not None},
        )
    text = "✅ Готово — вернитесь в браузер, вход выполнится сам.\n\nВыйти на всех устройствах — /web" if ok else EXPIRED
    if callback.message:
        await callback.message.edit_text(text)
    await callback.answer()


@router.callback_query(F.data.startswith("wl:no:"))
async def cancel_web_login(callback: CallbackQuery) -> None:
    async with async_session_maker() as session:
        await web_auth.cancel_login(session, callback.data.removeprefix("wl:no:"))
    if callback.message:
        await callback.message.edit_text("✖️ Вход отменён.")
    await callback.answer()


@router.message(Command("web"))
async def web_info(message: Message) -> None:
    if not message.from_user:
        return
    async with async_session_maker() as session:
        count = await web_auth.active_sessions_count(session, message.from_user.id)
    text = (
        f"🖥 Веб-версия для компьютера: {settings.WEBAPP_URL}\n"
        "Откройте адрес в браузере и нажмите «Войти через Telegram»."
    )
    markup = None
    if count:
        text += f"\n\nСейчас выполнен вход в браузерах: {count}."
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🚪 Выйти везде", callback_data="wl:logout_all")]]
        )
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data == "wl:logout_all")
async def logout_all(callback: CallbackQuery) -> None:
    async with async_session_maker() as session:
        await web_auth.revoke_all_sessions(session, callback.from_user.id)
    if callback.message:
        await callback.message.edit_text("🚪 Вы вышли из веб-версии во всех браузерах.")
    await callback.answer()
