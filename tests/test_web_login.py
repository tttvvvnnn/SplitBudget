"""Вход в веб-версию через бота: запрос → подтверждение в боте → cookie-сессия."""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from sqlalchemy import update

from app.api.routers import web
from app.shared import web_auth
from app.shared.database import async_session_maker
from app.shared.models import WebSession

USER = {"id": 777000111, "first_name": "Тихон", "username": "tikhon"}


@pytest.fixture(autouse=True)
def fake_bot_username(monkeypatch):
    async def _username() -> str:
        return "splitbudget_test_bot"

    monkeypatch.setattr(web, "bot_username", _username)


def _confirm(token: str, user: dict = USER) -> bool:
    async def _run():
        async with async_session_maker() as session:
            return await web_auth.confirm_login(session, token, user)

    return asyncio.run(_run())


def _login(client) -> dict:
    r = client.post("/api/web-login", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/120.0"})
    assert r.status_code == 200
    return r.json()


def test_full_login_flow(client):
    login = _login(client)
    assert login["bot_url"] == f"https://t.me/splitbudget_test_bot?start=login_{login['token']}"

    poll = {"token": login["token"], "secret": login["secret"]}
    assert client.post("/api/web-login/poll", json=poll).json() == {"status": "pending"}
    assert client.get("/api/personal").status_code == 401

    assert _confirm(login["token"])
    r = client.post("/api/web-login/poll", json=poll)
    assert r.json() == {"status": "confirmed"}
    assert "sb_session" in r.cookies

    r = client.get("/api/personal")
    assert r.status_code == 200
    assert r.json()["id"] == USER["id"]

    # сессия выдаётся один раз — повторный опрос ничего не даёт
    other = client.post("/api/web-login/poll", json=poll)
    assert other.json() == {"status": "expired"}

    assert client.post("/api/web-logout").status_code == 204
    client.cookies.clear()
    assert client.get("/api/personal").status_code == 401


def test_link_without_secret_does_not_log_in(client):
    login = _login(client)
    assert _confirm(login["token"])
    r = client.post("/api/web-login/poll", json={"token": login["token"], "secret": "guess"})
    assert r.json() == {"status": "expired"}
    assert "sb_session" not in r.cookies


def test_cancelled_login(client):
    login = _login(client)

    async def _cancel():
        async with async_session_maker() as session:
            await web_auth.cancel_login(session, login["token"])

    asyncio.run(_cancel())
    r = client.post("/api/web-login/poll", json={"token": login["token"], "secret": login["secret"]})
    assert r.json() == {"status": "cancelled"}
    assert not _confirm(login["token"])


def test_session_works_for_family_chat(client, seeded_chat):
    login = _login(client)
    assert _confirm(login["token"], {"id": seeded_chat.alice_tg_id, "first_name": "Алиса", "username": "alice"})
    client.post("/api/web-login/poll", json={"token": login["token"], "secret": login["secret"]})
    r = client.get(f"/api/chats/{seeded_chat.chat_id}/me")
    assert r.status_code == 200
    assert r.json()["member"]["username"] == "alice"


def test_expired_session_rejected(client):
    login = _login(client)
    assert _confirm(login["token"])
    client.post("/api/web-login/poll", json={"token": login["token"], "secret": login["secret"]})

    async def _expire():
        async with async_session_maker() as session:
            await session.execute(
                update(WebSession)
                .where(WebSession.tg_user_id == USER["id"])
                .values(expires_at=dt.datetime(2000, 1, 1))
            )
            await session.commit()

    asyncio.run(_expire())
    assert client.get("/api/personal").status_code == 401


def test_describe_user_agent():
    assert web_auth.describe_user_agent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    ) == "Chrome, Windows"
    assert web_auth.describe_user_agent(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15"
    ) == "Safari, macOS"
    assert web_auth.describe_user_agent("curl/8.0") == "браузер"
