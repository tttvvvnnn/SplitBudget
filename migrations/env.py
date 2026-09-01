"""Alembic env.py.

Важно: миграции гоняются через обычный синхронный sqlite3-драйвер (не aiosqlite),
которым в остальном приложении пользуется асинхронный движок — для SQLite отдельный async-режим
Alembic не нужен, это лишняя сложность. Путь к базе всегда берётся из тех же настроек
(app.shared.config.settings), что использует само приложение, поэтому `alembic upgrade head`
всегда работает с той же базой, что настроена в .env — вручную ничего указывать не нужно.
"""
from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.shared.config import settings
from app.shared.database import Base
from app.shared import models  # noqa: F401  регистрирует все модели в Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_sync_database_url() -> str:
    # settings.database_url — это "sqlite+aiosqlite:///...", Alembic-у нужен обычный "sqlite:///..."
    Path(settings.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{settings.DATABASE_PATH}"


def run_migrations_offline() -> None:
    context.configure(
        url=get_sync_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite не умеет ALTER TABLE напрямую — нужен batch-режим
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_sync_database_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
