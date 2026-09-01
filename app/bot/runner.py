"""Сборка диспетчера и запуск polling-цикла бота как фоновой задачи внутри FastAPI-процесса."""
from __future__ import annotations

import logging

from app.bot.bot_instance import bot, dp
from app.bot.handlers import start
from app.bot.middlewares import MemberTrackingMiddleware

logger = logging.getLogger(__name__)

_configured = False


def configure_dispatcher() -> None:
    global _configured
    if _configured:
        return
    dp.update.outer_middleware(MemberTrackingMiddleware())
    dp.include_router(start.router)
    _configured = True


async def start_polling() -> None:
    configure_dispatcher()
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Запуск polling бота...")
    await dp.start_polling(bot)
