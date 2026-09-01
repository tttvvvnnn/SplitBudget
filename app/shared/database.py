"""Асинхронное подключение к SQLite и фабрика сессий, общая для бота и API."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.shared.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, echo=False)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def configure_sqlite() -> None:
    """Включает WAL-режим SQLite для параллельного доступа из процесса бота и процесса API.

    Схему таблиц создают/обновляют миграции Alembic (app.shared.migrate.upgrade_to_head),
    не эта функция — см. lifespan в app/main.py."""
    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON;")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI-зависимость: одна сессия на запрос."""
    async with async_session_maker() as session:
        yield session
