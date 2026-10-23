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
    true,
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
    # С какого числа начинается месяц плана распределения дохода (app/shared/plan.py)
    plan_start_day: Mapped[int] = mapped_column(default=1, server_default="1")
    # Подсказки категорий от ИИ (app/shared/ai_category.py); можно выключить в настройках
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
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
    # Пользователь убрал этот чат из своего списка в приложении (одноразовый чат с друзьями).
    # Касается только его: у остальных участников чат на месте, траты и долги не трогаются.
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
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
    # Оплачено кредитной картой плательщика (её долг растёт на сумму траты). Карта лежит в
    # «Моих финансах» плательщика, поэтому в семейном чате её видит только он сам.
    card_id: Mapped[int | None] = mapped_column(
        ForeignKey("credit_cards.id", ondelete="SET NULL"), nullable=True
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
    day_of_month: Mapped[int] = mapped_column(default=1)  # 1..31, больше дней в месяце — последний день
    # Тип обязательного платежа: rent | loan | subscription | other. Кредитные карты живут
    # отдельно (CreditCard): у них плавающие даты, а не день месяца. Бывший вид card остался
    # только в старых записях до миграции.
    kind: Mapped[str] = mapped_column(String(16), default="other")
    end_month: Mapped[str | None] = mapped_column(String(7), nullable=True)  # последний платёж, 'YYYY-MM'
    debt: Mapped[Decimal | None] = mapped_column(MoneyType, nullable=True)  # остаток долга по кредиту
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


class SubscriptionHint(Base):
    """Найденная по истории трат подписка (app/shared/subscriptions.py): бот уже спросил
    «Сделать обязательным платежом?» (asked) или человек ответил «Не подписка» (dismissed)."""

    __tablename__ = "subscription_hints"
    __table_args__ = (UniqueConstraint("chat_id", "keyword", name="uq_subscription_hint_chat_keyword"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    keyword: Mapped[str] = mapped_column(String(255))  # нормализованное название траты
    status: Mapped[str] = mapped_column(String(16), default="asked")  # asked | dismissed | accepted
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


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


class AiGuess(Base):
    """Ответ ИИ про категорию траты с таким названием в этом чате (app/shared/ai_category.py).
    Кэш: пока дерево категорий не поменялось (tree_hash), повторно провайдера не спрашиваем.
    По created_at считаем дневной лимит обращений."""

    __tablename__ = "ai_guesses"
    __table_args__ = (UniqueConstraint("chat_id", "keyword", name="uq_ai_guess_chat_keyword"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    keyword: Mapped[str] = mapped_column(String(255))  # нормализованное название траты
    tree_hash: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(16))  # existing | new_subcategory | new_category | unknown
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subcategory: Mapped[str | None] = mapped_column(String(64), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class Budget(Base):
    """Месячный лимит трат в пространстве (семейном чате или «Моих финансах»).

    category="" — общий лимит на месяц; subcategory=None — лимит на всю категорию. В семейном
    чате считаются полные суммы трат чата, в личном пространстве — доля владельца во всех его
    тратах (личных и семейных), как в «Моих финансах». См. app/shared/budgets.py."""

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


class RecurringPayment(Base):
    """Платёж по шаблону RecurringExpense за конкретный месяц: ждёт оплаты (pending),
    оплачен (paid — для всего, кроме кредитной карты, создана Expense) или пропущен (skipped).
    Здесь же отмечаются отправленные напоминания, чтобы не слать их повторно."""

    __tablename__ = "recurring_payments"
    __table_args__ = (UniqueConstraint("recurring_id", "month", name="uq_recurring_payment_month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    recurring_id: Mapped[int] = mapped_column(ForeignKey("recurring_expenses.id", ondelete="CASCADE"))
    month: Mapped[str] = mapped_column(String(7))  # 'YYYY-MM'
    due_date: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | paid | skipped
    amount: Mapped[Decimal | None] = mapped_column(MoneyType, nullable=True)  # фактически оплачено
    expense_id: Mapped[int | None] = mapped_column(
        ForeignKey("expenses.id", ondelete="SET NULL"), nullable=True
    )
    reminders_sent: Mapped[str] = mapped_column(String(32), default="")  # например "7,3"
    asked: Mapped[bool] = mapped_column(Boolean, default=False)  # спросили «Оплачено?» в день платежа
    paid_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class IncomeSource(Base):
    """Регулярный доход в «Моих финансах»: зарплата, аванс и т.п. В его день бот спрашивает
    «Пришла?», и после подтверждения записывается Income (см. app/shared/income.py)."""

    __tablename__ = "income_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(MoneyType)
    day_of_month: Mapped[int] = mapped_column(default=1)  # 1..31, больше дней в месяце — последний день
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class Income(Base):
    """Поступление денег в «Моих финансах»."""

    __tablename__ = "incomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(MoneyType)
    income_date: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    # Какой регулярный доход это закрывает в месяце income_date (для «ожидается»)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("income_sources.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class IncomeAsk(Base):
    """Бот уже спросил «Пришла?» про регулярный доход в этом месяце — не спрашиваем повторно."""

    __tablename__ = "income_asks"
    __table_args__ = (UniqueConstraint("source_id", "month", name="uq_income_ask"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("income_sources.id", ondelete="CASCADE"))
    month: Mapped[str] = mapped_column(String(7))


class MonthlyReport(Base):
    """Итоги месяца уже отправлены в этот чат — второй раз не шлём."""

    __tablename__ = "monthly_reports"
    __table_args__ = (UniqueConstraint("chat_id", "month", name="uq_monthly_report"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    month: Mapped[str] = mapped_column(String(7))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class Goal(Base):
    """Цель накоплений в «Моих финансах»: «Отпуск 200 000 к июлю»."""

    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    target: Mapped[Decimal] = mapped_column(MoneyType)
    deadline: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class GoalDeposit(Base):
    """Сколько отложили на цель (отрицательная сумма — сняли). Отложенное в месяце уменьшает
    «свободно» в доходах (app/shared/income.py)."""

    __tablename__ = "goal_deposits"

    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.id", ondelete="CASCADE"))
    amount: Mapped[Decimal] = mapped_column(MoneyType)
    # Отложено по плану с этого поступления (кнопка «Отложил» в боте) — чтобы не отложить дважды
    income_id: Mapped[int | None] = mapped_column(
        ForeignKey("incomes.id", ondelete="SET NULL"), nullable=True
    )
    deposit_date: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class PlanItem(Base):
    """Статья плана распределения дохода в «Моих финансах» (app/shared/plan.py).

    kind: spend — расходная, к ней привязаны категории трат (categories);
          goal — накопительная, деньги откладываются на цель goal_id.
    Кредиты — обычные расходные статьи по категории «Кредиты». recurring_ids остался от
    бывших долговых статей и не используется.
    Статья «Свободно» не хранится: ей достаётся остаток процентов и остатки статей за
    прошлые месяцы."""

    __tablename__ = "plan_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16), default="spend")
    percent: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    categories: Mapped[str] = mapped_column(String(1024), default="[]")  # JSON-список категорий
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id", ondelete="SET NULL"), nullable=True)
    recurring_ids: Mapped[str] = mapped_column(String(255), default="[]")  # не используется
    position: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class PlanAlert(Base):
    """Какие уведомления «статья почти исчерпана / превышена» уже отправлены в периоде."""

    __tablename__ = "plan_alerts"
    __table_args__ = (UniqueConstraint("item_id", "period", "level", name="uq_plan_alert"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("plan_items.id", ondelete="CASCADE"))
    period: Mapped[str] = mapped_column(String(10))  # дата начала периода, 'YYYY-MM-DD'
    level: Mapped[int] = mapped_column()  # 80 | 100
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class CustomCategory(Base):
    """Свои категории и подкатегории пространства (app/shared/custom_categories.py).

    parent=None — категория, иначе — подкатегория категории parent (своей или базовой).
    is_builtin=True — строка только скрывает базовую категорию/подкатегорию из дерева."""

    __tablename__ = "custom_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    parent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(64))
    icon: Mapped[str] = mapped_column(String(16), default="🏷️")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class WebLogin(Base):
    """Запрос на вход в веб-версию через бота (app/shared/web_auth.py).

    Браузер создаёт запрос и открывает t.me/<бот>?start=login_<token>, пользователь
    подтверждает вход в боте, браузер по secret забирает сессию. secret знает только
    браузер, поэтому увидеть ссылку недостаточно, чтобы войти."""

    __tablename__ = "web_logins"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True)
    secret_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|confirmed|cancelled|used
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_json: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class WebSession(Base):
    """Сессия веб-версии: cookie в браузере, в базе — только sha256 от неё."""

    __tablename__ = "web_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_json: Mapped[str] = mapped_column(String(1024))
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    last_used_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)


class CreditCard(Base):
    """Кредитная карта в «Моих финансах» (app/shared/credits.py). Долг = debt (стартовый долг
    и операции CreditOperation) плюс покупки — траты с card_id этой карты. Даты не «N-е число каждого месяца»: льготный период у банков не ровно
    месяц, поэтому срок без процентов и дата минимального платежа хранятся датами и
    обновляются после каждой выписки или кнопкой «Продлить срок»."""

    __tablename__ = "credit_cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    credit_limit: Mapped[Decimal | None] = mapped_column(MoneyType, nullable=True)
    # Долг без покупок: стартовый ± операции. Может уйти в минус, когда покупки погашены.
    debt: Mapped[Decimal] = mapped_column(MoneyType, default=Decimal("0"))
    statement_day: Mapped[int | None] = mapped_column(nullable=True)  # день выписки, 1..31
    grace_until: Mapped[dt.date | None] = mapped_column(Date, nullable=True)  # весь долг без процентов до
    min_payment: Mapped[Decimal | None] = mapped_column(MoneyType, nullable=True)
    payment_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)  # минимальный платёж до
    # Куда в «Плане» списываются платежи по карте и проценты
    category: Mapped[str] = mapped_column(String(64), default="Кредиты")
    subcategory: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    # Какие напоминания уже отправлены: "2026-11-05:7,2026-11-05:3"
    reminders_sent: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class CreditOperation(Base):
    """Операция по кредитной карте.

    kind: payment  — внесли деньги, долг уменьшился (не трата);
          extend   — продлили срок: долг тот же, новые даты (amount пустой, старые даты в note);
          interest — начислили проценты: трата в категории карты (expense_id), долг вырос;
          adjust   — сверка с банком: amount — на сколько изменился долг."""

    __tablename__ = "credit_operations"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("credit_cards.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal | None] = mapped_column(MoneyType, nullable=True)
    op_date: Mapped[dt.date] = mapped_column(Date, default=dt.date.today)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expense_id: Mapped[int | None] = mapped_column(
        ForeignKey("expenses.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
