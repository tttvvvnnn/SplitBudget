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
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.database import Base

MoneyType = Numeric(12, 2)


def _utcnow() -> dt.datetime:
    """Текущее время в UTC без tzinfo — колонки DateTime хранят naive-время.
    Замена устаревшему dt.datetime.utcnow()."""
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


class Chat(Base):
    """Семейный чат в Telegram, в который добавлен бот."""

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # telegram chat_id
    title: Mapped[str] = mapped_column(String(255), default="")
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    # True — личное пространство пользователя («Мои финансы»), а не семейная группа. Его id
    # совпадает с tg_user_id владельца (как id личного чата с ботом в Telegram), единственный
    # участник — сам владелец. Id групп в Telegram всегда отрицательные, так что не пересекаются.
    is_personal: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    members: Mapped[list["Member"]] = relationship(back_populates="chat", cascade="all, delete-orphan")
    expenses: Mapped[list["Expense"]] = relationship(back_populates="chat", cascade="all, delete-orphan")


class Member(Base):
    """Участник семейного чата. Появляется в базе, как только впервые написал в группу
    (или взаимодействовал с ботом) — Telegram Bot API не даёт получить список участников напрямую."""

    __tablename__ = "members"
    __table_args__ = (UniqueConstraint("chat_id", "tg_user_id", name="uq_member_chat_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    # NULL — участник добавлен вручную, без Telegram-аккаунта (см. is_manual). Уникальность
    # (chat_id, tg_user_id) не страдает: несколько NULL в одном чате не считаются дубликатами
    # ни в SQLite, ни в Postgres.
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # False, если покинул группу
    # Имя файла аватарки участника (фото профиля Telegram на момент последней синхронизации),
    # лежит в том же PHOTOS_DIR, что и фото чеков — см. app/bot/avatars.py. NULL, пока не
    # синхронизировано, или если у пользователя нет фото профиля (в т.ч. всегда NULL у
    # ручных участников — синхронизировать нечего).
    avatar_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    added_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    chat: Mapped["Chat"] = relationship(back_populates="members")

    @property
    def avatar_url(self) -> str | None:
        """Относительный путь для GET /chats/{chat_id}/photos/{filename} — тот же эндпоинт,
        что уже отдаёт фото чеков (файл начинается с chat_id, проверка доступа та же)."""
        return f"photos/{self.avatar_path}" if self.avatar_path else None

    @property
    def is_manual(self) -> bool:
        """Участник без Telegram-аккаунта: добавлен вручную кем-то из чата (не может открыть
        мини-апп сам — заходить в неё будет кто-то другой, отмечая его как участника трат)."""
        return self.tg_user_id is None


class Expense(Base):
    """Одна трата."""

    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(MoneyType)
    category: Mapped[str] = mapped_column(String(64), default="Другое")
    # Подкатегория внутри category (например, «Продукты» → «Алкоголь»), см. app/shared/categories.py.
    subcategory: Mapped[str | None] = mapped_column(String(64), nullable=True)
    photo_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expense_date: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    payer_member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    split_type: Mapped[str] = mapped_column(String(16), default="equal")  # 'equal' | 'custom'
    created_by_member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    recurring_id: Mapped[int | None] = mapped_column(
        ForeignKey("recurring_expenses.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
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
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

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
    subcategory: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payer_member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    split_type: Mapped[str] = mapped_column(String(16), default="equal")
    day_of_month: Mapped[int] = mapped_column(default=1)  # 1..28
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_generated_month: Mapped[str | None] = mapped_column(String(7), nullable=True)  # 'YYYY-MM'
    created_by_member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

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


class CategoryRule(Base):
    """Выученное соответствие «название траты → категория» в конкретном чате (см.
    app/shared/autocategory.py). Обновляется при каждом сохранении траты."""

    __tablename__ = "category_rules"
    __table_args__ = (UniqueConstraint("chat_id", "keyword", name="uq_category_rule_chat_keyword"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    keyword: Mapped[str] = mapped_column(String(255))  # нормализованное название траты
    category: Mapped[str] = mapped_column(String(64))
    subcategory: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class Budget(Base):
    """Месячный лимит трат в пространстве (семейном чате или «Моих финансах»).

    category="" — общий лимит на месяц; subcategory=None — лимит на всю категорию. В семейном
    чате считаются полные суммы трат чата, в личном пространстве — доля владельца во всех его
    тратах (личных и семейных), как в «Все траты». См. app/shared/budgets.py."""

    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("chat_id", "category", "subcategory", name="uq_budget_chat_category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(64), default="")
    subcategory: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount: Mapped[Decimal] = mapped_column(MoneyType)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class BudgetAlert(Base):
    """Отправленное уведомление о лимите (80% или 100%) — чтобы не слать его повторно в том же месяце."""

    __tablename__ = "budget_alerts"
    __table_args__ = (UniqueConstraint("budget_id", "month", "level", name="uq_budget_alert"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    budget_id: Mapped[int] = mapped_column(ForeignKey("budgets.id", ondelete="CASCADE"))
    month: Mapped[str] = mapped_column(String(7))  # 'YYYY-MM'
    level: Mapped[int] = mapped_column()  # 80 | 100
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
