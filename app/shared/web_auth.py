"""Вход в веб-версию (браузер на ПК) через бота.

1. Браузер: POST /api/web-login → token (уходит в ссылку t.me/<бот>?start=login_<token>)
   и secret (остаётся только в браузере).
2. Пользователь открывает ссылку, бот спрашивает «Войти в веб-версию?» → confirm_login().
3. Браузер опрашивает POST /api/web-login/poll с token и secret; после подтверждения
   получает сессию — cookie, по которой API узнаёт пользователя вместо initData.

Ссылку могут подсмотреть (демонстрация экрана) — без secret по ней не войти. Остаётся
фишинг «нажми ссылку и подтверди», поэтому бот показывает, с какого браузера просят вход."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.models import WebLogin, WebSession, _utcnow

LOGIN_TTL = dt.timedelta(minutes=10)
SESSION_TTL = dt.timedelta(days=30)
# Продлеваем сессию не на каждый запрос, а не чаще раза в час — меньше записей в базу
SESSION_TOUCH_INTERVAL = dt.timedelta(hours=1)
START_PREFIX = "login_"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def create_login(session: AsyncSession, user_agent: str = "") -> tuple[str, str]:
    """Новый запрос на вход: (token, secret). Заодно чистит протухшие запросы."""
    await session.execute(delete(WebLogin).where(WebLogin.created_at < _utcnow() - LOGIN_TTL))
    token = secrets.token_urlsafe(24)  # 32 символа из [A-Za-z0-9_-] — как раз для start-параметра
    secret = secrets.token_urlsafe(32)
    session.add(WebLogin(token=token, secret_hash=_hash(secret), user_agent=user_agent[:255]))
    await session.commit()
    return token, secret


async def _active_login(session: AsyncSession, token: str) -> WebLogin | None:
    login = (await session.execute(select(WebLogin).where(WebLogin.token == token))).scalar_one_or_none()
    if login is None or login.created_at < _utcnow() - LOGIN_TTL:
        return None
    return login


async def pending_login(session: AsyncSession, token: str) -> WebLogin | None:
    """Запрос, который ещё ждёт подтверждения в боте."""
    login = await _active_login(session, token)
    return login if login is not None and login.status == "pending" else None


async def confirm_login(session: AsyncSession, token: str, user: dict) -> bool:
    """Пользователь подтвердил вход в боте. user — {id, first_name, last_name, username}."""
    login = await pending_login(session, token)
    if login is None:
        return False
    login.status = "confirmed"
    login.tg_user_id = int(user["id"])
    login.user_json = json.dumps(user, ensure_ascii=False)
    await session.commit()
    return True


async def cancel_login(session: AsyncSession, token: str) -> None:
    login = await pending_login(session, token)
    if login is not None:
        login.status = "cancelled"
        await session.commit()


async def poll_login(session: AsyncSession, token: str, secret: str) -> tuple[str, str | None]:
    """Статус запроса для браузера: ("pending" | "cancelled" | "expired", None) или
    ("confirmed", cookie-токен новой сессии). Сессия выдаётся один раз."""
    login = await _active_login(session, token)
    if login is None or not secrets.compare_digest(login.secret_hash, _hash(secret)):
        return "expired", None
    if login.status != "confirmed":
        return ("expired" if login.status == "used" else login.status), None
    login.status = "used"
    raw = secrets.token_urlsafe(32)
    now = _utcnow()
    session.add(
        WebSession(
            token_hash=_hash(raw),
            tg_user_id=login.tg_user_id,
            user_json=login.user_json,
            user_agent=login.user_agent,
            created_at=now,
            last_used_at=now,
            expires_at=now + SESSION_TTL,
        )
    )
    await session.commit()
    return "confirmed", raw


async def resolve_session(session: AsyncSession, raw: str) -> dict | None:
    """Пользователь по cookie сессии (тот же dict, что даёт initData) или None."""
    if not raw:
        return None
    web = (
        await session.execute(select(WebSession).where(WebSession.token_hash == _hash(raw)))
    ).scalar_one_or_none()
    now = _utcnow()
    if web is None or web.expires_at < now:
        return None
    if now - web.last_used_at > SESSION_TOUCH_INTERVAL:
        web.last_used_at = now
        web.expires_at = now + SESSION_TTL
        await session.commit()
    return json.loads(web.user_json)


async def revoke_session(session: AsyncSession, raw: str) -> None:
    await session.execute(delete(WebSession).where(WebSession.token_hash == _hash(raw)))
    await session.commit()


async def active_sessions_count(session: AsyncSession, tg_user_id: int) -> int:
    rows = await session.execute(
        select(WebSession.id).where(WebSession.tg_user_id == tg_user_id, WebSession.expires_at >= _utcnow())
    )
    return len(rows.all())


async def revoke_all_sessions(session: AsyncSession, tg_user_id: int) -> None:
    await session.execute(delete(WebSession).where(WebSession.tg_user_id == tg_user_id))
    await session.commit()


def describe_user_agent(ua: str) -> str:
    """«Chrome, Windows» — чтобы в боте было видно, какой браузер просит вход."""
    browser = next(
        (name for name, pattern in (
            ("Яндекс Браузер", r"YaBrowser"), ("Edge", r"Edg/"), ("Opera", r"OPR/"),
            ("Firefox", r"Firefox/"), ("Chrome", r"Chrome/"), ("Safari", r"Safari/"),
        ) if re.search(pattern, ua)),
        "браузер",
    )
    system = next(
        (name for name, pattern in (
            ("Android", r"Android"), ("iOS", r"iPhone|iPad"), ("Windows", r"Windows"),
            ("macOS", r"Mac OS X|Macintosh"), ("Linux", r"Linux"),
        ) if re.search(pattern, ua)),
        "",
    )
    return f"{browser}, {system}" if system else browser
