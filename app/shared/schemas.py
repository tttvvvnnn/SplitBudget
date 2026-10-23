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
    ai_enabled: bool = True  # подсказки категорий от ИИ в этом пространстве

    model_config = {"from_attributes": True}


class CategoryNode(BaseModel):
    name: str
    icon: str
    subcategories: list[str]


class BudgetIn(BaseModel):
    category: str = Field(default="", max_length=64)  # "" — общий лимит на месяц
    subcategory: str | None = Field(default=None, max_length=64)
    amount: Decimal = Field(gt=0)


class ObligationOut(BaseModel):
    payment_id: int
    recurring_id: int
    chat_id: int
    chat_title: str | None  # семейный чат, если платёж показан в «Моих финансах»
    title: str
    kind: str
    category: str
    subcategory: str | None
    due_date: dt.date
    status: str  # pending | paid | skipped
    amount: Decimal  # оплачено или запланировано целиком
    share: Decimal  # доля этого пространства
    counts: bool  # идёт ли в траты (платёж по кредитной карте — нет)
    debt: Decimal | None
    end_month: str | None


class ObligationsOut(BaseModel):
    items: list[ObligationOut]
    total: Decimal
    paid: Decimal
    pending: Decimal


class PayIn(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0)


class BudgetOut(BaseModel):
    id: int
    category: str
    subcategory: str | None
    amount: Decimal
    spent: Decimal
    reserved: Decimal = Decimal("0")  # ещё не оплаченные обязательные платежи этого месяца
    forecast: Decimal | None = None  # прогноз на конец месяца (только текущий месяц, с 5-го числа)
    label: str


class ForecastOut(BaseModel):
    """Прогноз всех трат пространства на конец текущего месяца (app/shared/forecast.py)."""

    month: str
    spent: Decimal
    forecast: Decimal
    days_left: int


class AiProposalOut(BaseModel):
    """ИИ предлагает создать категорию (new_category) или подкатегорию (new_subcategory)."""

    action: str
    category: str
    subcategory: str | None
    icon: str | None


class CategorySuggestionOut(BaseModel):
    category: str | None  # None — только предложение новой категории
    subcategory: str | None
    source: str  # 'learned' — выучено по тратам этого чата, 'dictionary' — словарь магазинов, 'ai' — ИИ
    proposal: AiProposalOut | None = None


class AiSettingsIn(BaseModel):
    ai_enabled: bool


class MeOut(BaseModel):
    chat: ChatOut
    member: MemberOut
    members: list[MemberOut]
    categories: list[str]
    ai_available: bool = False  # на сервере настроен ИИ (AI_API_KEY) — показываем выключатель


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
    card_id: int | None = None  # своя кредитная карта; чужая не показывается
    repays_card_id: int | None = None  # платёж по своей кредитной карте


class AllExpenseOut(BaseModel):
    """Трата в «Моих финансах» (личная или семейная, которую пользователь оплатил или в которой
    у него есть доля): из какого пространства и сколько из неё потратил сам пользователь
    (my_share, см. app/shared/my_spending.py)."""

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
    card_id: int | None = None  # оплачено своей кредитной картой
    repays_card_id: int | None = None  # платёж по своей кредитной карте


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



# Кредитные карты — не обязательные платежи, а отдельная вкладка «Кредиты» (CreditCardIn)
RECURRING_KINDS = ("rent", "loan", "subscription", "other")


def _check_kind(v: str | None) -> str | None:
    if v == "card":
        raise ValueError("Кредитные карты заводятся во вкладке «Кредиты»")
    if v is not None and v not in RECURRING_KINDS:
        raise ValueError(f"kind должен быть одним из {RECURRING_KINDS}")
    return v


def _check_month(v: str | None) -> str | None:
    if v:
        try:
            dt.datetime.strptime(v, "%Y-%m")
        except ValueError as exc:
            raise ValueError("Месяц должен быть в формате YYYY-MM") from exc
    return v or None


class RecurringParticipantIn(BaseModel):
    member_id: int
    custom_amount: Decimal | None = None


class RecurringCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    amount: Decimal = Field(ge=0)  # 0 — без суммы: только напоминание, сколько внёс — при оплате
    category: str = "Другое"
    subcategory: str | None = None
    payer_member_id: int
    split_type: str = "equal"
    day_of_month: int = Field(default=1, ge=1, le=31)  # 31 — последний день месяца
    participants: list[RecurringParticipantIn] = Field(default_factory=list)
    kind: str = "other"
    end_month: str | None = None  # последний платёж кредита, 'YYYY-MM'
    debt: Decimal | None = Field(default=None, ge=0)  # остаток долга по кредиту

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        return _check_kind(v)

    @field_validator("end_month")
    @classmethod
    def _end_month(cls, v: str | None) -> str | None:
        return _check_month(v)


class RecurringUpdate(BaseModel):
    title: str | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    category: str | None = None
    subcategory: str | None = None  # "" — убрать подкатегорию
    payer_member_id: int | None = None
    split_type: str | None = None
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    participants: list[RecurringParticipantIn] | None = None
    is_active: bool | None = None
    kind: str | None = None
    end_month: str | None = None  # "" — без срока
    debt: Decimal | None = Field(default=None, ge=0)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str | None) -> str | None:
        return _check_kind(v)

    @field_validator("end_month")
    @classmethod
    def _end_month(cls, v: str | None) -> str | None:
        return v if v == "" else _check_month(v)


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
    kind: str
    end_month: str | None
    debt: Decimal | None


class SubcategoryStat(BaseModel):
    subcategory: str  # "" — трата без подкатегории
    total: Decimal
    count: int


class CategoryStat(BaseModel):
    category: str
    total: Decimal
    count: int
    subcategories: list[SubcategoryStat] = Field(default_factory=list)
    prev_total: Decimal = Decimal("0")  # та же категория в прошлом периоде
    prev_to_date: Decimal | None = None  # прошлый период за тот же срок, если этот ещё идёт
    avg_total: Decimal | None = None  # в среднем за 3 прошлых месяца (только для месяца; если идёт — за тот же срок)


class MonthTotal(BaseModel):
    month: str
    total: Decimal


class CategoryTrend(BaseModel):
    category: str
    totals: list[Decimal]  # по месяцам, как StatsOut.trend


class TopExpense(BaseModel):
    title: str
    category: str
    expense_date: dt.date
    amount: Decimal


class PlaceStat(BaseModel):
    """Частое место: траты с одинаковым (нормализованным) названием."""

    title: str
    count: int
    total: Decimal


class StatsOut(BaseModel):
    period: str  # 'YYYY-MM' для месяца, иначе 'YYYY-MM-DD/YYYY-MM-DD'
    date_from: dt.date
    date_to: dt.date  # включительно
    is_month: bool = True
    total: Decimal
    count: int = 0
    by_category: list[CategoryStat]
    prev_total: Decimal = Decimal("0")  # весь прошлый период той же длины
    # Если период ещё идёт: прошлый период за тот же срок от начала (с ним и сравниваем)
    prev_to_date: Decimal | None = None
    # В среднем за 3 прошлых месяца с тратами (только для месяца; если он идёт — за тот же срок)
    avg_total: Decimal | None = None
    # Траты нарастающим итогом по дням (только для месяца): daily[i] — потрачено с 1-го по (i+1)-е
    daily: list[Decimal] = Field(default_factory=list)
    prev_daily: list[Decimal] = Field(default_factory=list)
    # Итоги за 12 месяцев, заканчивая последним месяцем периода, от старых к новым
    trend: list[MonthTotal] = Field(default_factory=list)
    category_trend: list[CategoryTrend] = Field(default_factory=list)  # по убыванию суммы за 12 мес
    weekdays: list[Decimal] = Field(default_factory=list)  # пн..вс за период
    top_expenses: list[TopExpense] = Field(default_factory=list)
    places: list[PlaceStat] = Field(default_factory=list)
    recurring_total: Decimal = Decimal("0")  # траты из регулярных платежей
    recurring_count: int = 0
    # Только «Мои финансы»: доходы по месяцам (как trend) и за период
    income_trend: list[MonthTotal] | None = None
    income_total: Decimal | None = None


class IncomeIn(BaseModel):
    title: str = Field(default="Доход", max_length=255)
    amount: Decimal = Field(gt=0)
    income_date: dt.date | None = None  # по умолчанию — сегодня
    source_id: int | None = None


class IncomeOut(BaseModel):
    id: int
    title: str
    amount: Decimal
    income_date: dt.date
    source_id: int | None

    model_config = {"from_attributes": True}


class IncomeSourceIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    amount: Decimal = Field(gt=0)
    day_of_month: int = Field(ge=1, le=31)  # 31 — последний день месяца
    is_active: bool = True


class IncomeSourceOut(BaseModel):
    id: int
    title: str
    amount: Decimal
    day_of_month: int
    is_active: bool
    date: dt.date | None = None  # дата в запрошенном месяце
    received: bool = False

    model_config = {"from_attributes": True}


class IncomeSummaryOut(BaseModel):
    incomes: list[IncomeOut]
    sources: list[IncomeSourceOut]
    received: Decimal
    expected: Decimal
    spent: Decimal
    obligations_pending: Decimal
    saved: Decimal
    free: Decimal
    has_cards: bool
    spent_card: Decimal
    card_repaid: Decimal
    next_title: str | None
    next_date: dt.date | None
    next_amount: Decimal | None
    days_to_next: int | None
    obligations_before_next: Decimal
    per_day: Decimal | None


class GoalIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    target: Decimal = Field(gt=0)
    deadline: dt.date | None = None
    is_archived: bool = False


class GoalDepositIn(BaseModel):
    amount: Decimal  # отрицательная — сняли с цели
    deposit_date: dt.date | None = None

    @field_validator("amount")
    @classmethod
    def _not_zero(cls, v: Decimal) -> Decimal:
        if v == 0:
            raise ValueError("Сумма не может быть нулевой")
        return v


class GoalDepositOut(BaseModel):
    id: int
    amount: Decimal
    deposit_date: dt.date

    model_config = {"from_attributes": True}


class GoalOut(BaseModel):
    id: int
    title: str
    target: Decimal
    deadline: dt.date | None
    is_archived: bool
    saved: Decimal
    saved_this_month: Decimal
    months_left: int | None
    monthly_needed: Decimal | None
    deposits: list[GoalDepositOut]


class SubscriptionHintOut(BaseModel):
    """Трата, похожая на подписку (app/shared/subscriptions.py)."""

    keyword: str
    title: str
    amount: Decimal
    category: str
    subcategory: str | None
    day_of_month: int
    months: int


class SubscriptionHintAction(BaseModel):
    keyword: str = Field(min_length=1, max_length=255)


class PlanItemIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    kind: str = Field(default="spend", pattern="^(spend|goal)$")
    percent: Decimal = Field(gt=0, le=100)
    categories: list[str] = Field(default_factory=list)  # для spend: «Категория» или «Категория › Подкатегория»
    goal_id: int | None = None  # для goal


class PlanItemOut(BaseModel):
    id: int | None  # None — «Свободно»
    title: str
    kind: str  # spend | goal | free
    percent: Decimal
    allocated: Decimal
    spent: Decimal
    remaining: Decimal
    categories: list[str] = []
    goal_id: int | None = None
    goal_title: str | None = None
    goal_saved: Decimal | None = None
    goal_target: Decimal | None = None
    carried: Decimal = Decimal("0")
    card_debt: Decimal | None = None  # долг по картам, платежи по которым идут в статью


class PlanOut(BaseModel):
    start: dt.date
    end: dt.date  # последний день месяца плана, включительно
    start_day: int
    income: Decimal
    incomes_count: int
    carried: Decimal
    percent_total: Decimal
    items: list[PlanItemOut]


class PlanEntryOut(BaseModel):
    """Списание статьи плана: трата (твоя доля), платёж по кредитке или пополнение цели."""

    date: dt.date
    title: str
    amount: Decimal
    source: str  # expense | card | deposit
    category: str | None = None
    subcategory: str | None = None


class PlanSettingsIn(BaseModel):
    start_day: int = Field(ge=1, le=28)


class PlanOrderIn(BaseModel):
    ids: list[int]


class SubcategoryNodeOut(BaseModel):
    name: str
    custom: bool
    hidden: bool
    id: int | None


class CategoryTreeNodeOut(BaseModel):
    """Категория пространства (app/shared/custom_categories.py)."""

    name: str
    icon: str
    subcategories: list[str]  # видимые подкатегории
    all_subcategories: list[SubcategoryNodeOut]
    custom: bool
    hidden: bool
    id: int | None
    family: str | None = None  # своя категория семейного чата (только в «Моих финансах»)
    family_subcategories: list[str] = []


class CustomCategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    icon: str | None = Field(default=None, max_length=16)
    parent: str | None = Field(default=None, max_length=64)


class CategoryHiddenIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    parent: str | None = Field(default=None, max_length=64)
    hidden: bool


class CreditCardIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    credit_limit: Decimal | None = Field(default=None, gt=0)
    debt: Decimal = Field(default=Decimal("0"), ge=0)  # при создании; дальше — операции
    statement_day: int | None = Field(default=None, ge=1, le=31)
    grace_until: dt.date | None = None
    min_payment: Decimal | None = Field(default=None, ge=0)
    payment_date: dt.date | None = None
    category: str = "Кредиты"
    subcategory: str | None = "Кредитная карта"
    is_archived: bool = False


class CreditOperationOut(BaseModel):
    id: int
    kind: str  # payment | extend | interest | adjust
    amount: Decimal | None
    op_date: dt.date
    note: str | None  # у extend — JSON со старыми и новыми датами

    model_config = {"from_attributes": True}


class CreditPurchaseOut(BaseModel):
    """Трата, оплаченная картой (личная или семейная)."""

    id: int
    chat_id: int
    title: str
    amount: Decimal
    expense_date: dt.date
    is_payment: bool = False  # трата-платёж по карте: долг уменьшился

    model_config = {"from_attributes": True}


class CreditCardOut(BaseModel):
    id: int
    title: str
    credit_limit: Decimal | None
    debt: Decimal  # вместе с покупками
    statement_day: int | None
    grace_until: dt.date | None
    min_payment: Decimal | None
    payment_date: dt.date | None
    next_deadline: dt.date | None
    category: str
    subcategory: str | None
    is_archived: bool
    operations: list[CreditOperationOut]
    purchases: list[CreditPurchaseOut]  # последние покупки


class LoanOut(BaseModel):
    recurring_id: int
    title: str
    amount: Decimal  # ежемесячный платёж
    debt: Decimal | None  # остаток
    day_of_month: int
    end_month: str | None
    is_active: bool
    payment_id: int | None  # платёж этого месяца
    payment_status: str | None  # pending | paid | skipped
    payment_amount: Decimal | None  # оплачено или к оплате
    payment_due: dt.date | None
    next_due: dt.date | None


class CreditsOut(BaseModel):
    cards: list[CreditCardOut]
    loans: list[LoanOut]
    total_debt: Decimal
    next_deadline: dt.date | None


class CreditAmountIn(BaseModel):
    amount: Decimal = Field(gt=0)
    op_date: dt.date | None = None


class CreditExtendIn(BaseModel):
    grace_until: dt.date | None = None
    payment_date: dt.date | None = None
    min_payment: Decimal | None = Field(default=None, ge=0)


class CreditAdjustIn(BaseModel):
    debt: Decimal = Field(ge=0)


class AiProposalAcceptedOut(BaseModel):
    """Создали предложенное ИИ: что выбрать в форме и обновлённое дерево категорий."""

    category: str
    subcategory: str | None
    categories: list[CategoryTreeNodeOut]


class TidyItemOut(BaseModel):
    """Группа трат в «Другом» с одинаковым названием и подсказка, куда их переложить."""

    title: str
    count: int
    total: Decimal
    category: str | None
    subcategory: str | None
    source: str
    proposal: AiProposalOut | None = None


class TidyOut(BaseModel):
    items: list[TidyItemOut]
    unknown: list[str] = []  # названия, для которых категорию не угадали
    more: bool  # не всё успели разобрать — можно запросить ещё


class TidyApplyItem(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    category: str | None = None
    subcategory: str | None = None
    create: bool = False  # создать предложенную ИИ категорию (proposal)


class TidyApplyIn(BaseModel):
    items: list[TidyApplyItem] = Field(max_length=200)


class TidyApplyOut(BaseModel):
    moved: int
    categories: list[CategoryTreeNodeOut]
