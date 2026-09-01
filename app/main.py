"""Точка входа: один процесс, в котором одновременно живут FastAPI (API + отдача mini app)
и long-polling Telegram-бота, плюс планировщик повторяющихся трат."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routers import balances, chats, expenses, export, recurring, stats
from app.bot.bot_instance import bot
from app.bot.runner import start_polling
from app.bot.scheduler import setup_scheduler
from app.shared.database import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"

_background_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

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

for router in (chats.router, expenses.router, balances.router, recurring.router, stats.router, export.router):
    app.include_router(router, prefix="/api")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


if WEBAPP_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEBAPP_DIR), html=True), name="webapp")
else:
    logger.warning("Папка webapp не найдена по пути %s — mini app отдаваться не будет", WEBAPP_DIR)
