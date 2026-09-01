"""SQLAlchemy-модели. Одна база — SQLite-файл, читают и пишут в неё и бот, и API,
работающие в одном процессе (см. app/main.py)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database import Base

MoneyType = Numeric(12, 2)


class Chat(Base):
    """Семейный чат в Telegram, в который добавлен бот."""

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # telegram chat_id
    title: Mapped[str] = mapped_column(String(255), default="")
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    members: Mapped[list["Member"]] = relationship(back_populates="chat", cascade="all, delete-orphan")
    expenses: Mapped[list["Expense"]] = relationship(back_populates="chat", cascade="all, delete-orphan")


class Member(Base):
    """Участник семейного чата. Появляется в базе, как только впервые написал в группу
    (или взаимодействовал с ботом) — Telegram Bot API не даёт получить список участников напрямую."""

    __tablename__ = "members"
    __table_args__ = (UniqueConstraint("chat_id", "tg_user_id", name="uq_member_chat_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    tg_user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # False, если покинул группу
    # Имя файла аватарки участника (фото профиля Telegram на момент последней синхронизации),
    # лежит в том же PHOTOS_DIR, что и фото чеков — см. app/bot/avatars.py. NULL, пока не
    # синхронизировано, или если у пользователя нет фото профиля.
    avatar_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    added_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    chat: Mapped["Chat"] = relationship(back_populates="members")

    @property
    def avatar_url(self) -> str | None:
        """Относительный путь для GET /chats/{chat_id}/photos/{filename} — тот же эндпоинт,
        что уже отдаёт фото чеков (файл начинается с chat_id, проверка доступа та же)."""
        return f"photos/{self.avatar_path}" if self.avatar_path else None


class Expense(Base):
    """Одна трата."""

    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(MoneyType)
    category: Mapped[str] = mapped_column(String(64), default="Другое")
    photo_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expense_date: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    payer_member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    split_type: Mapped[str] = mapped_column(String(16), default="equal")  # 'equal' | 'custom'
    created_by_member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    recurring_id: Mapped[int | None] = mapped_column(
        ForeignKey("recurring_expenses.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow
    )

    chat: Mapped["Chat"] = relationship(back_populates="expenses")
    payer: Mapped["Member"] = relationship(foreign_keys=[payer_member_id])
    created_by: Mapped["Member"] = relationship(foreign_keys=[created_by_member_id])
    shares: Mapped[list["ExpenseShare"]] = relationship(back_populates="expense", cascade="all, delete-orphan")


class ExpenseShare(Base):
    """Доля конкретного участника в конкретной трате (сколько он должен за неё заплатить)."""

    __tablename__ = "expense_shares"
    __table_args__ = (UniqueConstraint("expense_id", "member_id", name="uq_share_expense_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    expense_id: Mapped[int] = mapped_column(ForeignKey("expenses.id", ondelete="CASCADE"))
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    amount: Mapped[Decimal] = mapped_column(MoneyType)

    expense: Mapped["Expense"] = relationship(back_populates="shares")
    member: Mapped["Member"] = relationship()


class Settlement(Base):
    """Запись о погашении долга: from_member перевёл to_member указанную сумму."""

    __tablename__ = "settlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    from_member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    to_member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    amount: Mapped[Decimal] = mapped_column(MoneyType)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by_member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    from_member: Mapped["Member"] = relationship(foreign_keys=[from_member_id])
    to_member: Mapped["Member"] = relationship(foreign_keys=[to_member_id])


class RecurringExpense(Base):
    """Шаблон повторяющейся траты (аренда, подписки), из которого раз в месяц
    автоматически создаётся обычная Expense."""

    __tablename__ = "recurring_expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(MoneyType)
    category: Mapped[str] = mapped_column(String(64), default="Другое")
    payer_member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    split_type: Mapped[str] = mapped_column(String(16), default="equal")
    day_of_month: Mapped[int] = mapped_column(default=1)  # 1..28
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_generated_month: Mapped[str | None] = mapped_column(String(7), nullable=True)  # 'YYYY-MM'
    created_by_member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    payer: Mapped["Member"] = relationship(foreign_keys=[payer_member_id])
    participants: Mapped[list["RecurringParticipant"]] = relationship(
        back_populates="recurring", cascade="all, delete-orphan"
    )


class RecurringParticipant(Base):
    """Участник повторяющейся траты + (опционально) его фиксированная сумма для custom-режима."""

    __tablename__ = "recurring_participants"
    __table_args__ = (UniqueConstraint("recurring_id", "member_id", name="uq_recurring_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    recurring_id: Mapped[int] = mapped_column(ForeignKey("recurring_expenses.id", ondelete="CASCADE"))
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    custom_amount: Mapped[Decimal | None] = mapped_column(MoneyType, nullable=True)

    recurring: Mapped["RecurringExpense"] = relationship(back_populates="participants")
    member: Mapped["Member"] = relationship()
