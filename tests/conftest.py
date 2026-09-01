"""Общие фикстуры для тестов.

Важно: переменные окружения выставляются на уровне модуля, ДО того как где-либо
импортируется app.shared.config (там Settings читает окружение один раз при импорте) —
поэтому эти строчки должны идти самыми первыми в файле, раньше остальных импортов.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="family_expenses_tests_"))
os.environ.setdefault("BOT_TOKEN", "123456789:AATestTokenForPytestOnly")
os.environ.setdefault("WEBAPP_URL", "https://expenses.example.com")
os.environ.setdefault("DATABASE_PATH", str(_TMP_DIR / "test.sqlite3"))
os.environ.setdefault("PHOTOS_DIR", str(_TMP_DIR / "photos"))
os.environ.setdefault("SCHEDULER_TIMEZONE", "Europe/Moscow")

import asyncio  # noqa: E402
import hashlib  # noqa: E402
import hmac  # noqa: E402
import itertools  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
import urllib.parse  # noqa: E402
from dataclasses import dataclass  # noqa: E402

import pytest  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.shared.config import settings  # noqa: E402
from app.shared.database import async_session_maker  # noqa: E402
from app.shared.models import Chat, Member  # noqa: E402

_chat_id_counter = itertools.count(1)


def make_init_data(user: dict) -> str:
    """Собирает валидную (корректно подписанную) Telegram.WebApp.initData строку для тестового
    BOT_TOKEN — тем же алгоритмом, что app.api.auth.validate_init_data проверяет на входе."""
    auth_date = str(int(time.time()))
    data = {"auth_date": auth_date, "query_id": "AAtest", "user": json.dumps(user, separators=(",", ":"))}
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(data)


@dataclass
class SeededChat:
    chat_id: int
    alice_member_id: int
    bob_member_id: int
    alice_tg_id: int
    bob_tg_id: int
    alice_init_data: str
    bob_init_data: str


@pytest.fixture()
def client():
    """TestClient, поднимающий полный lifespan приложения (миграции, бот-поллинг,
    планировщик) — так же, как в проде, только без реального Telegram на другом конце."""
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def seeded_chat(client) -> SeededChat:  # noqa: ARG001  (client нужен, чтобы миграции уже применились)
    """Создаёт изолированный чат с двумя участниками (Алиса, Боб) для одного теста —
    у каждого теста свой chat_id, поэтому тесты не мешают друг другу в общей тестовой БД."""
    n = next(_chat_id_counter)
    chat_id = -(100000000000 + n)
    alice_tg_id = 900000000 + n * 10
    bob_tg_id = 900000000 + n * 10 + 1

    async def _seed():
        async with async_session_maker() as session:
            chat = Chat(id=chat_id, title=f"Тестовая семья #{n}", currency="RUB")
            session.add(chat)
            alice = Member(chat_id=chat_id, tg_user_id=alice_tg_id, username="alice", full_name="Алиса")
            bob = Member(chat_id=chat_id, tg_user_id=bob_tg_id, username="bob", full_name="Боб")
            session.add_all([alice, bob])
            await session.commit()
            return alice.id, bob.id

    alice_id, bob_id = asyncio.run(_seed())

    return SeededChat(
        chat_id=chat_id,
        alice_member_id=alice_id,
        bob_member_id=bob_id,
        alice_tg_id=alice_tg_id,
        bob_tg_id=bob_tg_id,
        alice_init_data=make_init_data({"id": alice_tg_id, "username": "alice", "first_name": "Алиса"}),
        bob_init_data=make_init_data({"id": bob_tg_id, "username": "bob", "first_name": "Боб"}),
    )


@pytest.fixture()
def auth_header():
    """Фикстура-хелпер: auth_header(init_data) -> {"X-Telegram-Init-Data": init_data}."""

    def _make(init_data: str) -> dict:
        return {"X-Telegram-Init-Data": init_data}

    return _make
