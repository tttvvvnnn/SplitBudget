"""Точка входа: один процесс, в котором одновременно живут FastAPI (API + отдача mini app)
и long-polling Telegram-бота, плюс планировщик повторяющихся трат."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routers import balances, chats, expenses, export, recurring, stats
from app.bot.bot_instance import bot
from app.bot.runner import start_polling
from app.bot.scheduler import setup_scheduler
from app.shared.database import configure_sqlite
from app.shared.migrate import upgrade_to_head

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"

# Прокидывается из Dockerfile (ARG/ENV APP_VERSION), который release.yml заполняет тегом релиза
# (например "1.2.0"). Вне релизного билда (локальный запуск, dev) остаётся "dev".
APP_VERSION = os.environ.get("APP_VERSION", "dev")

# Метка времени сборки образа (см. Dockerfile) — на стенде APP_VERSION всегда "dev", поэтому
# само по себе не показывает, свежая ли сборка. Файл появляется в образе на этапе `docker build`
# и меняется только тогда, когда реально поменялся код (см. комментарий в Dockerfile рядом с
# RUN date ...). Используется в /healthz и показывается в мини-аппе.
_BUILD_INFO_PATH = Path("/srv/build_info.txt")


def _read_build_info() -> str:
    try:
        return _BUILD_INFO_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


APP_BUILD_INFO = _read_build_info()

_background_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # alembic upgrade head — синхронный/блокирующий вызов, уводим в отдельный поток,
    # чтобы не блокировать event loop на старте.
    await asyncio.to_thread(upgrade_to_head)
    await configure_sqlite()

    bot_task = asyncio.create_task(start_polling())
    _background_tasks.add(bot_task)
    bot_task.add_done_callback(_background_tasks.discard)

    scheduler = setup_scheduler()
    scheduler.start()

    logger.info("Приложение запущено: API + бот + планировщик")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        bot_task.cancel()
        try:
            await bot_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        await bot.session.close()


app = FastAPI(title="Family Expenses Bot", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_webapp_static(request, call_next):
    """Mini app (HTML/JS/CSS) отдаём с обязательной ревалидацией на каждый запрос.

    По умолчанию StaticFiles не ставит Cache-Control вообще, и тогда браузеры/WebView сами
    решают, сколько кешировать (эвристика). Мобильный WebView внутри Telegram особенно
    агрессивен: после передеплоя интерфейс мог не обновляться даже после очистки кеша
    Telegram и перезапуска приложения. ETag/Last-Modified у StaticFiles уже есть, так что
    ревалидация дешёвая — почти всегда 304 Not Modified, а не полная перекачка файла.
    """
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        # /healthz тоже не кешируем: мини-апп дёргает его, чтобы показать версию/сборку в
        # шапке, и если ответ закешируется в том же агрессивном мобильном WebView — бейдж
        # будет врать после передеплоя точно так же, как раньше врал весь интерфейс.
        response.headers["Cache-Control"] = "no-cache"
    return response


for router in (chats.router, expenses.router, balances.router, recurring.router, stats.router, export.router):
    app.include_router(router, prefix="/api")


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "version": APP_VERSION, "build": APP_BUILD_INFO}


if WEBAPP_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEBAPP_DIR), html=True), name="webapp")
else:
    logger.warning("Папка webapp не найдена по пути %s — mini app отдаваться не будет", WEBAPP_DIR)
