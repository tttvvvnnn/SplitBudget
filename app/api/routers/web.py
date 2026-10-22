"""Вход в веб-версию через бота и выход (app/shared/web_auth.py)."""
from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import SESSION_COOKIE
from app.bot.bot_instance import bot
from app.shared import web_auth
from app.shared.database import get_session

router = APIRouter(tags=["web"])

_bot_username: str | None = None


async def bot_username() -> str:
    global _bot_username
    if _bot_username is None:
        _bot_username = (await bot.get_me()).username
    return _bot_username


class WebLoginOut(BaseModel):
    token: str
    secret: str
    bot_url: str
    expires_in: int


class WebLoginPoll(BaseModel):
    token: str
    secret: str


class WebLoginStatus(BaseModel):
    status: str  # pending | confirmed | cancelled | expired


@router.post("/web-login", response_model=WebLoginOut)
async def start_web_login(request: Request, session: AsyncSession = Depends(get_session)) -> WebLoginOut:
    token, secret = await web_auth.create_login(session, request.headers.get("user-agent", ""))
    username = await bot_username()
    return WebLoginOut(
        token=token,
        secret=secret,
        bot_url=f"https://t.me/{username}?start={web_auth.START_PREFIX}{token}",
        expires_in=int(web_auth.LOGIN_TTL.total_seconds()),
    )


@router.post("/web-login/poll", response_model=WebLoginStatus)
async def poll_web_login(
    body: WebLoginPoll, request: Request, response: Response, session: AsyncSession = Depends(get_session)
) -> WebLoginStatus:
    status, raw = await web_auth.poll_login(session, body.token, body.secret)
    if raw:
        response.set_cookie(
            SESSION_COOKIE,
            raw,
            max_age=int(web_auth.SESSION_TTL.total_seconds()),
            httponly=True,
            secure=_is_https(request),
            # Strict: cookie не уходит ни с одним запросом с чужого сайта — защита от CSRF
            samesite="strict",
        )
    return WebLoginStatus(status=status)


@router.post("/web-logout", status_code=204)
async def web_logout(
    response: Response,
    sb_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    session: AsyncSession = Depends(get_session),
):
    if sb_session:
        await web_auth.revoke_session(session, sb_session)
    response.delete_cookie(SESSION_COOKIE)


def _is_https(request: Request) -> bool:
    # за nginx приложение видит http, настоящую схему передаёт X-Forwarded-Proto
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
