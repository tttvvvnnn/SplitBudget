"""Централизованная конфигурация приложения, читается из переменных окружения (.env)."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    BOT_TOKEN: str
    WEBAPP_URL: str
    DEFAULT_CURRENCY: str = "RUB"
    DATABASE_PATH: str = "/data/db/expenses.sqlite3"
    PHOTOS_DIR: str = "/data/photos"
    SCHEDULER_TIMEZONE: str = "Europe/Moscow"
    API_PORT: int = 8000

    # Категории и подкатегории трат — в app/shared/categories.py.

    @property
    def database_url(self) -> str:
        Path(self.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{self.DATABASE_PATH}"

    @property
    def photos_dir(self) -> Path:
        p = Path(self.PHOTOS_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
