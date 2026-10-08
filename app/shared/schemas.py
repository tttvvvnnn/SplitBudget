"""Pydantic-схемы для запросов/ответов FastAPI."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class MemberOut(BaseModel):
    id: int
    tg_user_id: int | None
    username: str | None
    full_name: str
    is_active: bool
    avatar_url: str | None
    is_manual: bool

    model_config = {"from_attributes": True}


class MemberCreate(BaseModel):
    """Участник без Telegram-аккаунта — добавляется вручную кем-то из чата (например,
    ребёнок или родственник без своего Telegram)."""

    full_name: str = Field(min_length=1, max_length=255)


class MemberUpdate(BaseModel):
    """Переименование ручного участника (см. MemberCreate) — только для него: у
    Telegram-участников full_name синхронизируется из их профиля."""

    full_name: str = Field(min_length=1, max_length=255)


class ChatOut(BaseModel):
    id: int
    title: str
    currency: str
    is_personal: bool

    model_config = {"from_attributes": True}


class CategoryNode(BaseModel):
    name: str
    icon: str
    subcategories: list[str]


class BudgetIn(BaseModel):
    category: str = Field(default="", max_length=64)  # "" — общий лимит на месяц
    subcategory: str | None = Field(default=None, max_length=64)
    amount: Decimal = Field(gt=0)


class BudgetOut(BaseModel):
    id: int
    category: str
    subcategory: str | None
    amount: Decimal
    spent: Decimal
    label: str


class CategorySuggestionOut(BaseModel):
    category: str
    subcategory: str | None
    source: str  # 'learned' — выучено по тратам этого чата, 'dictionary' — словарь магазинов


class MeOut(BaseModel):
    chat: ChatOut
    member: MemberOut
    members: list[MemberOut]
    categories: list[str]


class ShareIn(BaseModel):
    member_id: int
    amount: Decimal = Field(gt=0)


class ExpenseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    amount: Decimal = Field(gt=0)
    category: str = "Другое"
    expense_date: dt.date
    payer_member_id: int
    split_type: str = "equal"  # 'equal' | 'custom'
    participant_ids: list[int] = Field(default_factory=list)  # используется при split_type='equal'
    custom_shares: list[ShareIn] = Field(default_factory=list)  # используется при split_type='custom'

    @field_validator("split_type")
    @classmethod
    def _check_split_type(cls, v: str) -> str:
        if v not in ("equal", "custom"):
            raise ValueError("split_type должен быть 'equal' или 'custom'")
        return v


class ExpenseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    amount: Decimal | None = Field(default=None, gt=0)
    category: str | None = None
    expense_date: dt.date | None = None
    payer_member_id: int | None = None
    split_type: str | None = None
    participant_ids: list[int] | None = None
    custom_shares: list[ShareIn] | None = None


class ShareOut(BaseModel):
    member_id: int
    amount: Decimal

    model_config = {"from_attributes": True}


class ExpenseOut(BaseModel):
    id: int
    title: str
    amount: Decimal
    category: str
    subcategory: str | None
    photo_url: str | None
    expense_date: dt.date
    payer_member_id: int
    split_type: str
    created_by_member_id: int
    shares: list[ShareOut]
    created_at: dt.datetime
    is_recurring: bool


class AllExpenseOut(BaseModel):
    """Трата в сводке «Все траты»: из какого пространства и какая в ней доля пользователя."""

    id: int
    chat_id: int
    chat_title: str
    is_personal: bool
    title: str
    amount: Decimal
    my_share: Decimal
    category: str
    subcategory: str | None
    photo_url: str | None
    expense_date: dt.date
    payer_name: str
    i_paid: bool
    is_recurring: bool


class BalanceEntry(BaseModel):
    member_id: int
    net: Decimal  # положительное — ему должны, отрицательное — он должен


class DebtEntry(BaseModel):
    from_member_id: int
    to_member_id: int
    amount: Decimal


class BalancesOut(BaseModel):
    balances: list[BalanceEntry]
    simplified_debts: list[DebtEntry]


class SettlementCreate(BaseModel):
    from_member_id: int
    to_member_id: int
    amount: Decimal = Field(gt=0)
    note: str | None = None


class SettlementOut(BaseModel):
    id: int
    from_member_id: int
    to_member_id: int
    amount: Decimal
    note: str | None
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class RecurringParticipantIn(BaseModel):
    member_id: int
    custom_amount: Decimal | None = None


class RecurringCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    amount: Decimal = Field(gt=0)
    category: str = "Другое"
    subcategory: str | None = None
    payer_member_id: int
    split_type: str = "equal"
    day_of_month: int = Field(default=1, ge=1, le=28)
    participants: list[RecurringParticipantIn] = Field(default_factory=list)


class RecurringUpdate(BaseModel):
    title: str | None = None
    amount: Decimal | None = Field(default=None, gt=0)
    category: str | None = None
    subcategory: str | None = None  # "" — убрать подкатегорию
    payer_member_id: int | None = None
    split_type: str | None = None
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    participants: list[RecurringParticipantIn] | None = None
    is_active: bool | None = None


class RecurringOut(BaseModel):
    id: int
    title: str
    amount: Decimal
    category: str
    subcategory: str | None
    payer_member_id: int
    split_type: str
    day_of_month: int
    is_active: bool
    participants: list[RecurringParticipantIn]


class SubcategoryStat(BaseModel):
    subcategory: str  # "" — трата без подкатегории
    total: Decimal
    count: int


class CategoryStat(BaseModel):
    category: str
    total: Decimal
    count: int
    subcategories: list[SubcategoryStat] = Field(default_factory=list)


class StatsOut(BaseModel):
    period: str
    total: Decimal
    by_category: list[CategoryStat]
