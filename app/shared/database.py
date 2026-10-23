"""Асинхронное подключение к SQLite и фабрика сессий, общая для бота и API."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.shared.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, echo=False)


@event.listens_for(engine.sync_engine, "connect")
def _foreign_keys_on(dbapi_connection, _record) -> None:
    # В SQLite внешние ключи (а с ними ON DELETE CASCADE / SET NULL) включаются на каждом
    # соединении отдельно. Без этого удалённый кредит оставлял свои платежи, и новый кредит
    # с тем же (освободившимся) id получал чужой платёж.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
async_session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def configure_sqlite() -> None:
    """Включает WAL-режим SQLite для параллельного доступа из процесса бота и процесса API.

    Схему таблиц создают/обновляют миграции Alembic (app.shared.migrate.upgrade_to_head),
    не эта функция — см. lifespan в app/main.py."""
    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL;")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI-зависимость: одна сессия на запрос."""
    async with async_session_maker() as session:
        yield session
