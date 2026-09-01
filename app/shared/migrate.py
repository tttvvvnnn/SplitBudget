"""Программный запуск `alembic upgrade head` при старте приложения — чтобы схема БД всегда
подтягивалась автоматически из миграций (см. migrations/), без ручного вызова `alembic` на
сервере и без потери данных при последующих изменениях структуры таблиц."""
from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def upgrade_to_head() -> None:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    logger.info("Применяю миграции БД (alembic upgrade head)...")
    command.upgrade(cfg, "head")
    logger.info("Миграции применены")
