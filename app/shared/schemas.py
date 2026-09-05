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

    model_config = {"from_attributes": True}


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
    photo_url: str | None
    expense_date: dt.date
    payer_member_id: int
    split_type: str
    created_by_member_id: int
    shares: list[ShareOut]
    created_at: dt.datetime
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
    payer_member_id: int
    split_type: str = "equal"
    day_of_month: int = Field(default=1, ge=1, le=28)
    participants: list[RecurringParticipantIn] = Field(default_factory=list)


class RecurringUpdate(BaseModel):
    title: str | None = None
    amount: Decimal | None = Field(default=None, gt=0)
    category: str | None = None
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
    payer_member_id: int
    split_type: str
    day_of_month: int
    is_active: bool
    participants: list[RecurringParticipantIn]


class CategoryStat(BaseModel):
    category: str
    total: Decimal
    count: int


class StatsOut(BaseModel):
    period: str
    total: Decimal
    by_category: list[CategoryStat]
