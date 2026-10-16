"""План распределения дохода в «Моих финансах»: статьи в процентах от каждого поступления.

Каждое поступление (Income) делится по процентам статей; статья «Свободно» получает
остаток процентов. По статье видно: выделено, потрачено, осталось.

Виды статей (PlanItem.kind):
- spend — расходная: к ней привязаны категории или отдельные подкатегории трат
  («Транспорт › Топливо»), траты этих категорий (личные и твоя доля семейных) списываются
  с неё. Если категория в одной статье, а её подкатегория в другой — трата идёт в статью
  с подкатегорией;
- goal — накопительная: выделенное откладывается на цель по кнопке «Отложил»;
- debt — долговая: её закрывают оплаченные обязательные платежи (кредитка, кредит).
Траты без статьи и пополнения целей без статьи списываются со «Свободно».

Месяц плана начинается с chat.plan_start_day (по умолчанию 1-го). В конце месяца статьи
обнуляются: всё, что осталось (или ушло в минус), переходит в «Свободно» следующего месяца.
Так происходит взаимозачёт: перерасход одной статьи покрывается остатками других. Хранить
перенос не нужно — он считается от начала плана как доходы минус все списания.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.models import (
    Chat,
    Expense,
    ExpenseShare,
    Goal,
    GoalDeposit,
    Income,
    Member,
    PlanAlert,
    PlanItem,
    RecurringExpense,
    RecurringPayment,
)

KINDS = ("spend", "goal", "debt")
FREE_TITLE = "Свободно"
SUB_SEP = " › "  # «Категория › Подкатегория» в списке категорий статьи
ALERT_LEVELS = (100, 80)
ZERO = Decimal("0")
CENT = Decimal("0.01")
HUNDRED = Decimal("100")


def loads(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _add_months(date: dt.date, months: int) -> dt.date:
    index = date.year * 12 + date.month - 1 + months
    return date.replace(year=index // 12, month=index % 12 + 1)


def period_start(start_day: int, date: dt.date) -> dt.date:
    """Начало месяца плана, в который попадает date."""
    if date.day >= start_day:
        return date.replace(day=start_day)
    return _add_months(date.replace(day=start_day), -1)


def period_end(start: dt.date) -> dt.date:
    """Первый день следующего месяца плана (не включительно)."""
    return _add_months(start, 1)


def share_of(amount: Decimal, percent: Decimal) -> Decimal:
    return (amount * percent / HUNDRED).quantize(CENT)


@dataclass
class ItemStatus:
    id: int | None  # None — «Свободно»
    title: str
    kind: str  # spend | goal | debt | free
    percent: Decimal
    allocated: Decimal
    spent: Decimal
    categories: list[str] = field(default_factory=list)
    goal_id: int | None = None
    goal_title: str | None = None
    goal_saved: Decimal | None = None
    goal_target: Decimal | None = None
    recurring_ids: list[int] = field(default_factory=list)
    debt_left: Decimal | None = None
    carried: Decimal = ZERO  # только у «Свободно»: остатки статей прошлых месяцев

    @property
    def remaining(self) -> Decimal:
        return self.allocated - self.spent


@dataclass
class PlanSummary:
    start: dt.date
    end: dt.date  # не включительно
    start_day: int
    income: Decimal
    incomes_count: int
    carried: Decimal
    items: list[ItemStatus]
    started: dt.date | None  # начало первого месяца плана; None — статей ещё нет

    @property
    def percent_total(self) -> Decimal:
        return sum((i.percent for i in self.items if i.kind != "free"), ZERO)


@dataclass
class _Flows:
    """Все движения денег плана в диапазоне дат."""

    incomes: list[tuple[dt.date, Decimal]]
    expenses: list[tuple[dt.date, str, str | None, Decimal]]  # без платежей долговых статей
    deposits: list[tuple[dt.date, int, Decimal]]
    payments: list[tuple[dt.date, int, Decimal]]  # оплаченные платежи долговых статей


async def items_of(session: AsyncSession, chat_id: int) -> list[PlanItem]:
    result = await session.execute(
        select(PlanItem).where(PlanItem.chat_id == chat_id).order_by(PlanItem.position, PlanItem.id)
    )
    return list(result.scalars().all())


async def _flows(session: AsyncSession, chat: Chat, start: dt.date, end: dt.date, debt_ids: set[int]) -> _Flows:
    incomes = (
        await session.execute(
            select(Income.income_date, Income.amount).where(
                Income.chat_id == chat.id, Income.income_date >= start, Income.income_date < end
            )
        )
    ).all()
    # Как и во вкладке «Траты» «Моих финансов»: личные траты и доля владельца в семейных
    expenses = (
        await session.execute(
            select(Expense.expense_date, Expense.category, Expense.subcategory, Expense.recurring_id, ExpenseShare.amount)
            .join(ExpenseShare, ExpenseShare.expense_id == Expense.id)
            .join(Member, Member.id == ExpenseShare.member_id)
            .where(Member.tg_user_id == chat.id, Expense.expense_date >= start, Expense.expense_date < end)
        )
    ).all()
    deposits = (
        await session.execute(
            select(GoalDeposit.deposit_date, GoalDeposit.goal_id, GoalDeposit.amount)
            .join(Goal, Goal.id == GoalDeposit.goal_id)
            .where(Goal.chat_id == chat.id, GoalDeposit.deposit_date >= start, GoalDeposit.deposit_date < end)
        )
    ).all()
    payments: list[tuple[dt.date, int, Decimal]] = []
    if debt_ids:
        rows = (
            await session.execute(
                select(RecurringPayment).where(
                    RecurringPayment.recurring_id.in_(debt_ids), RecurringPayment.status == "paid"
                )
            )
        ).scalars().all()
        for p in rows:
            date = p.paid_at.date() if p.paid_at else p.due_date
            if start <= date < end:
                payments.append((date, p.recurring_id, p.amount or ZERO))
    return _Flows(
        incomes=[(d, a) for d, a in incomes],
        # Платёж долговой статьи (кредит записывается тратой) считается в ней, а не в категории
        expenses=[(d, c, sub, a) for d, c, sub, rid, a in expenses if rid is None or rid not in debt_ids],
        deposits=[(d, g, a) for d, g, a in deposits],
        payments=payments,
    )


def _outflow(flows: _Flows) -> Decimal:
    return (
        sum((a for *_, a in flows.expenses), ZERO)
        + sum((a for _, _, a in flows.deposits), ZERO)
        + sum((a for _, _, a in flows.payments), ZERO)
    )


async def summary(session: AsyncSession, chat: Chat, date: dt.date) -> PlanSummary:
    """План на месяц плана, в который попадает date."""
    start_day = chat.plan_start_day or 1
    start = period_start(start_day, date)
    end = period_end(start)
    items = await items_of(session, chat.id)
    debt_ids = {int(r) for i in items if i.kind == "debt" for r in loads(i.recurring_ids)}

    started = None
    carried = ZERO
    if items:
        first = min(i.created_at for i in items).date()
        started = period_start(start_day, first)
        if started < start:
            # Все остатки прошлых месяцев переходят в «Свободно»: доходы минус все списания
            past = await _flows(session, chat, started, start, debt_ids)
            carried = sum((a for _, a in past.incomes), ZERO) - _outflow(past)

    flows = await _flows(session, chat, start, end, debt_ids)
    income = sum((a for _, a in flows.incomes), ZERO)

    goal_ids = {i.goal_id for i in items if i.goal_id}
    goals = {}
    if goal_ids:
        rows = (await session.execute(select(Goal).where(Goal.id.in_(goal_ids)))).scalars().all()
        saved = dict(
            (
                await session.execute(
                    select(GoalDeposit.goal_id, func.coalesce(func.sum(GoalDeposit.amount), 0))
                    .where(GoalDeposit.goal_id.in_(goal_ids))
                    .group_by(GoalDeposit.goal_id)
                )
            ).all()
        )
        goals = {g.id: (g, Decimal(saved.get(g.id, 0))) for g in rows}
    recurrings = {}
    if debt_ids:
        rows = (
            await session.execute(select(RecurringExpense).where(RecurringExpense.id.in_(debt_ids)))
        ).scalars().all()
        recurrings = {r.id: r for r in rows}

    # Куда идёт трата: «Категория › Подкатегория» точнее, чем вся категория
    by_key: dict[str, int] = {}
    for item in items:
        if item.kind == "spend":
            for key in loads(item.categories):
                by_key.setdefault(str(key), item.id)

    def item_for(category: str, subcategory: str | None) -> int | None:
        if subcategory and f"{category}{SUB_SEP}{subcategory}" in by_key:
            return by_key[f"{category}{SUB_SEP}{subcategory}"]
        return by_key.get(category)

    expense_items = [(item_for(c, sub), a) for _, c, sub, a in flows.expenses]
    statuses: list[ItemStatus] = []
    matched_goals: set[int] = set()
    for item in items:
        st = ItemStatus(
            id=item.id,
            title=item.title,
            kind=item.kind,
            percent=item.percent,
            allocated=share_of(income, item.percent),
            spent=ZERO,
        )
        if item.kind == "spend":
            st.categories = [str(c) for c in loads(item.categories)]
            st.spent = sum((a for i, a in expense_items if i == item.id), ZERO)
        elif item.kind == "goal":
            st.goal_id = item.goal_id
            if item.goal_id in goals:
                goal, saved = goals[item.goal_id]
                matched_goals.add(goal.id)
                st.goal_title, st.goal_saved, st.goal_target = goal.title, saved, goal.target
                st.spent = sum((a for _, g, a in flows.deposits if g == goal.id), ZERO)
        elif item.kind == "debt":
            st.recurring_ids = [int(r) for r in loads(item.recurring_ids)]
            ids = set(st.recurring_ids)
            st.spent = sum((a for _, r, a in flows.payments if r in ids), ZERO)
            debts = [recurrings[r].debt for r in ids if r in recurrings and recurrings[r].debt is not None]
            st.debt_left = sum(debts, ZERO) if debts else None
        statuses.append(st)

    free_percent = max(HUNDRED - sum((i.percent for i in items), ZERO), ZERO)
    free = ItemStatus(
        id=None,
        title=FREE_TITLE,
        kind="free",
        percent=free_percent,
        # Остаток от округления долей статей тоже достаётся «Свободно»
        allocated=income - sum((s.allocated for s in statuses), ZERO) + carried,
        spent=sum((a for i, a in expense_items if i is None), ZERO)
        + sum((a for _, g, a in flows.deposits if g not in matched_goals), ZERO),
        carried=carried,
    )
    statuses.append(free)
    return PlanSummary(
        start=start,
        end=end,
        start_day=start_day,
        income=income,
        incomes_count=len(flows.incomes),
        carried=carried,
        items=statuses,
        started=started,
    )


async def savings_for_income(session: AsyncSession, income: Income) -> list[tuple[PlanItem, Goal, Decimal]]:
    """Сколько отложить на цели с этого поступления по плану: [(статья, цель, сумма)]."""
    out = []
    for item in await items_of(session, income.chat_id):
        if item.kind != "goal" or not item.goal_id:
            continue
        goal = await session.get(Goal, item.goal_id)
        amount = share_of(income.amount, item.percent)
        if goal is not None and amount > 0:
            out.append((item, goal, amount))
    return out


async def income_saved(session: AsyncSession, income_id: int) -> bool:
    result = await session.execute(select(GoalDeposit.id).where(GoalDeposit.income_id == income_id).limit(1))
    return result.first() is not None


async def collect_alerts(session: AsyncSession, chat: Chat, today: dt.date) -> list[str] | None:
    """Уведомления 80% / 100% по расходным статьям плана. None — плана нет (тогда работают
    обычные лимиты). Отправленное помечается в PlanAlert, коммитит вызывающий."""
    if not chat.is_personal or not await items_of(session, chat.id):
        return None
    plan = await summary(session, chat, today)
    period = plan.start.isoformat()
    messages = []
    for st in plan.items:
        if st.kind != "spend" or st.allocated <= 0:
            continue
        percent = st.spent * 100 / st.allocated
        level = next((lvl for lvl in ALERT_LEVELS if percent >= lvl), None)
        if level is None:
            continue
        sent = await session.execute(
            select(PlanAlert.level).where(PlanAlert.item_id == st.id, PlanAlert.period == period)
        )
        if any(lvl >= level for lvl in sent.scalars().all()):
            continue
        session.add(PlanAlert(item_id=st.id, period=period, level=level))
        icon, verb = ("🚨", "превышена") if level == 100 else ("⚠️", "почти исчерпана")
        money = lambda a: f"{a:,.0f} {chat.currency}".replace(",", " ")  # noqa: E731
        messages.append(
            f"{icon} Мои финансы · Статья «{st.title}» {verb}: "
            f"{money(st.spent)} из {money(st.allocated)} ({percent:.0f}%)"
        )
    return messages
