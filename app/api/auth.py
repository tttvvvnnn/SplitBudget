"""Проверка подлинности Telegram.WebApp.initData, которую фронтенд шлёт в заголовке
X-Telegram-Init-Data при каждом запросе к API.

Алгоритм ровно такой, как описан в официальной документации Telegram:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from app.shared.config import settings

MAX_INIT_DATA_AGE_SECONDS = 24 * 60 * 60  # сутки


def validate_init_data(init_data: str) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="Отсутствует initData")

    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Некорректный initData") from exc

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Отсутствует hash в initData")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise HTTPException(status_code=401, detail="Не удалось подтвердить подлинность initData")

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        auth_date = 0
    if auth_date <= 0 or (time.time() - auth_date) > MAX_INIT_DATA_AGE_SECONDS:
        raise HTTPException(status_code=401, detail="initData устарела — откройте приложение заново")

    user_json = pairs.get("user")
    if not user_json:
        raise HTTPException(status_code=401, detail="Не удалось определить пользователя Telegram")

    try:
        user = json.loads(user_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=401, detail="Некорректные данные пользователя") from exc

    return user


async def get_current_user(x_telegram_init_data: str = Header(alias="X-Telegram-Init-Data")) -> dict:
    return validate_init_data(x_telegram_init_data)
