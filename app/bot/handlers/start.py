"""Команды /start и /app, а также обработка входа/выхода участников из группы."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from sqlalchemy import select

from app.bot.avatars import sync_member_avatar
from app.bot.keyboards import open_app_keyboard_group
from app.shared.config import settings
from app.shared.crud import deactivate_member, get_or_create_chat, get_or_create_member
from app.shared.database import async_session_maker
from app.shared.models import Chat, Member

router = Router(name="start")

WELCOME_GROUP = (
    "👋 Привет! Я буду помогать вести учёт семейных трат в этом чате.\n\n"
    "Нажмите кнопку ниже, чтобы открыть приложение: добавляйте траты, "
    "смотрите баланс и кто кому должен.\n\n"
    "Чтобы я видел всех участников чата, попросите каждого написать в этот чат "
    "хотя бы одно любое сообщение (например, просто «привет») — так устроен Telegram, "
    "полный список участников группы боту недоступен."
)

WELCOME_PRIVATE = (
    "👋 «Мои финансы» — ваш личный учёт трат, его видите только вы. "
    "Ниже — семейные чаты с общими тратами."
)

WELCOME_PRIVATE_NO_CHATS = (
    "👋 Привет! В «Моих финансах» можно вести личный учёт трат — его видите только вы.\n\n"
    "Для общих трат добавьте меня в семейный групповой чат и напишите там /start — "
    "тогда здесь появится и кнопка семейного чата."
)


@router.message(CommandStart(), F.chat.type.in_({"group", "supergroup"}))
async def start_in_group(message: Message, bot: Bot) -> None:
    async with async_session_maker() as session:
        await get_or_create_chat(session, message.chat.id, message.chat.title or "")
        if message.from_user:
            member = await get_or_create_member(
                session,
                chat_id=message.chat.id,
                tg_user_id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name,
            )
            await sync_member_avatar(bot, session, member)
        await session.commit()
    me = await bot.get_me()
    await message.answer(
        WELCOME_GROUP, reply_markup=open_app_keyboard_group(me.username, message.chat.id)
    )


@router.message(Command("app", "expenses"), F.chat.type.in_({"group", "supergroup"}))
async def open_app_in_group(message: Message, bot: Bot) -> None:
    me = await bot.get_me()
    await message.answer(
        "💸 Открыть учёт трат:", reply_markup=open_app_keyboard_group(me.username, message.chat.id)
    )


@router.message(CommandStart(), F.chat.type == "private")
async def start_in_private(message: Message) -> None:
    """В личке — кнопка «Мои финансы» (личное пространство) и по кнопке на каждый семейный
    чат, где пользователь уже известен боту."""
    if not message.from_user:
        return
    async with async_session_maker() as session:
        result = await session.execute(
            select(Chat)
            .join(Member, Member.chat_id == Chat.id)
            .where(
                Member.tg_user_id == message.from_user.id,
                Member.is_active.is_(True),
                Chat.is_personal.is_(False),
            )
        )
        family_chats = result.scalars().all()

    buttons = [
        [
            InlineKeyboardButton(
                text="👤 Мои финансы",
                web_app=WebAppInfo(url=f"{settings.WEBAPP_URL}/?space=personal"),
            )
        ]
    ]
    for chat in family_chats:
        url = f"{settings.WEBAPP_URL}/?chat_id={chat.id}"
        buttons.append(
            [InlineKeyboardButton(text=f"🏠 {chat.title or chat.id}", web_app=WebAppInfo(url=url))]
        )

    text = WELCOME_PRIVATE if family_chats else WELCOME_PRIVATE_NO_CHATS
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.message(F.new_chat_members)
async def on_new_members(message: Message, bot: Bot) -> None:
    me = await bot.get_me()
    async with async_session_maker() as session:
        await get_or_create_chat(session, message.chat.id, message.chat.title or "")
        for user in message.new_chat_members or []:
            if user.id == me.id:
                await session.commit()
                await message.answer(
                    WELCOME_GROUP,
                    reply_markup=open_app_keyboard_group(me.username, message.chat.id),
                )
                continue
            if not user.is_bot:
                new_member = await get_or_create_member(
                    session,
                    chat_id=message.chat.id,
                    tg_user_id=user.id,
                    username=user.username,
                    full_name=user.full_name,
                )
                await sync_member_avatar(bot, session, new_member)
        await session.commit()


@router.message(F.left_chat_member)
async def on_member_left(message: Message) -> None:
    user = message.left_chat_member
    if not user or user.is_bot:
        return
    async with async_session_maker() as session:
        await deactivate_member(session, message.chat.id, user.id)
        await session.commit()
