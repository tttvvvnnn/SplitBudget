"""Тесты синхронизации аватарки участника (app/bot/avatars.py)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot.avatars import sync_member_avatar
from app.shared.database import async_session_maker
from app.shared.models import Member


def _fake_bot(photos):
    bot = SimpleNamespace()
    bot.get_user_profile_photos = AsyncMock(return_value=SimpleNamespace(photos=photos))
    bot.download = AsyncMock()
    return bot


def test_sync_member_avatar_downloads_largest_photo(seeded_chat):
    async def _run():
        async with async_session_maker() as session:
            member = await session.get(Member, seeded_chat.alice_member_id)
            small = SimpleNamespace(file_id="small")
            large = SimpleNamespace(file_id="large")
            bot = _fake_bot(photos=[[small, large]])  # один "альбом" фото, разные размеры

            await sync_member_avatar(bot, session, member)
            await session.commit()

            bot.download.assert_awaited_once()
            assert bot.download.call_args.args[0] is large  # взят последний — самый крупный
            expected_filename = f"{seeded_chat.chat_id}_avatar_{seeded_chat.alice_tg_id}.jpg"
            assert member.avatar_path == expected_filename
            assert member.avatar_url == f"photos/{expected_filename}"

    asyncio.run(_run())


def test_sync_member_avatar_skips_if_already_set(seeded_chat):
    async def _run():
        async with async_session_maker() as session:
            member = await session.get(Member, seeded_chat.alice_member_id)
            member.avatar_path = "already-there.jpg"
            await session.commit()

            bot = _fake_bot(photos=[[SimpleNamespace(file_id="x")]])
            await sync_member_avatar(bot, session, member)

            bot.get_user_profile_photos.assert_not_awaited()
            assert member.avatar_path == "already-there.jpg"

    asyncio.run(_run())


def test_sync_member_avatar_no_photos_leaves_null(seeded_chat):
    async def _run():
        async with async_session_maker() as session:
            member = await session.get(Member, seeded_chat.bob_member_id)
            bot = _fake_bot(photos=[])

            await sync_member_avatar(bot, session, member)

            bot.download.assert_not_awaited()
            assert member.avatar_path is None

    asyncio.run(_run())


def test_sync_member_avatar_never_raises_on_telegram_error(seeded_chat):
    """Сбой похода в Telegram (нет сети, rate limit и т.п.) не должен ломать обработку
    сообщения/команды, из-за которой участник был создан — ошибка только логируется."""
    async def _run():
        async with async_session_maker() as session:
            member = await session.get(Member, seeded_chat.bob_member_id)
            bot = SimpleNamespace()
            bot.get_user_profile_photos = AsyncMock(side_effect=RuntimeError("network down"))

            await sync_member_avatar(bot, session, member)  # не должно бросить исключение

            assert member.avatar_path is None

    asyncio.run(_run())
