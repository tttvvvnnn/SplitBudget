"""План распределения дохода в «Моих финансах»: статьи в процентах от каждого поступления.

Каждое поступление (Income) делится по процентам статей; статья «Свободно» получает
остаток процентов. По статье видно: выделено, потрачено, осталось.

Виды статей (PlanItem.kind):
- spend — расходная: к ней привязаны категории или отдельные подкатегории трат
  («Транспорт › Топливо»), траты этих категорий (личные и семейные — сколько потратил ты)
  списываются
  с неё. Если категория в одной статье, а её подкатегория в другой — трата идёт в статью
  с подкатегорией;
- goal — накопительная: выделенное откладывается на цель по кнопке «Отложил».
Покупки кредиткой и проценты по ней живут в карте и статьи не трогают (app/shared/my_spending.py).
Тратой становится погашение карты — трата «Кредиты» с repays_card_id (так записывается и
«Внести»); она списывается со статьи своей категории, например «Кредитки». Старые платежи
«Внести», которые тратой не записывались, идут в статью по категории карты. Траты без статьи
и пополнения целей без статьи списываются со «Свободно».

Месяц плана начинается с chat.plan_start_day (по умолчанию 1-го). Проценты статей у каждого
месяца свои (PlanItemMonth): новый месяц начинается с процентов прошлого, правка меняет только
свой месяц и следующие, прошлые месяцы остаются как были. Статья, добавленная в месяце, в
прошлых месяцах не видна; удалённая пропадает с месяца удаления.

В конце месяца статьи обнуляются: всё, что осталось (или ушло в минус), — взаимозачёт: перерасход
одной статьи покрывается остатками других. Итоговый остаток переходит в новый месяц как
текущие деньги и делится по процентам нового месяца вместе с поступлениями. Минус (потрачено
больше, чем было) не делится, а уменьшает «Свободно». Хранить перенос не нужно — он считается
от начала плана как доходы минус все списания.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.credits import cards_of, debt_of
from app.shared.models import (
    Chat,
    CreditCard,
    CreditOperation,
    Goal,
    GoalDeposit,
    Income,
    PlanAlert,
    PlanItem,
    PlanItemMonth,
)
from app.shared.my_spending import my_expenses

KINDS = ("spend", "goal")
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
    kind: str  # spend | goal | free
    percent: Decimal
    allocated: Decimal
    spent: Decimal
    categories: list[str] = field(default_factory=list)
    goal_id: int | None = None
    goal_title: str | None = None
    goal_saved: Decimal | None = None
    goal_target: Decimal | None = None
    carried: Decimal = ZERO  # только у «Свободно»: перерасход прошлых месяцев (минус переноса)
    card_debt: Decimal | None = None  # долг по картам, платежи по которым идут в эту статью

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
class Entry:
    """Списание плана: трата (твоя доля), платёж по кредитной карте или пополнение цели."""

    date: dt.date
    title: str
    amount: Decimal
    source: str  # expense | card | deposit
    category: str | None = None
    subcategory: str | None = None
    goal_id: int | None = None


@dataclass
class _Flows:
    """Все движения денег плана в диапазоне дат."""

    incomes: list[tuple[dt.date, Decimal]]
    outflows: list[Entry]


async def items_of(session: AsyncSession, chat_id: int) -> list[PlanItem]:
    result = await session.execute(
        select(PlanItem).where(PlanItem.chat_id == chat_id).order_by(PlanItem.position, PlanItem.id)
    )
    return list(result.scalars().all())


async def _months_of(session: AsyncSession, item_ids: list[int]) -> dict[int, list[PlanItemMonth]]:
    out: dict[int, list[PlanItemMonth]] = {i: [] for i in item_ids}
    if item_ids:
        rows = await session.execute(
            select(PlanItemMonth).where(PlanItemMonth.item_id.in_(item_ids)).order_by(PlanItemMonth.period)
        )
        for row in rows.scalars().all():
            out[row.item_id].append(row)
    return out


def _percent_in(item: PlanItem, months: list[PlanItemMonth], end: dt.date) -> Decimal | None:
    """Процент статьи в месяце плана, который кончается end; None — статьи в нём нет."""
    if not months:
        return item.percent  # статья из времён до помесячного плана — во всех месяцах
    percent = None
    for m in months:
        if m.period < end:
            percent = m.percent
    return percent


async def month_items(session: AsyncSession, chat: Chat, date: dt.date) -> list[tuple[PlanItem, Decimal]]:
    """Статьи месяца плана, в который попадает date, с их процентами в этом месяце."""
    end = period_end(period_start(chat.plan_start_day or 1, date))
    items = await items_of(session, chat.id)
    months = await _months_of(session, [i.id for i in items])
    out = []
    for item in items:
        percent = _percent_in(item, months[item.id], end)
        if percent is not None:
            out.append((item, percent))
    return out


async def set_percent(
    session: AsyncSession, chat: Chat, item: PlanItem, date: dt.date, percent: Decimal | None, today: dt.date | None = None
) -> None:
    """Задать процент статьи (None — убрать статью) в месяце плана, куда попадает date.

    Следующие месяцы наследуют новое значение. Если месяц уже прошёл, следующий за ним
    сохраняет прежний процент: правка прошлого не меняет текущий план. Новую статью
    (item.id ещё нет) сначала нужно добавить в сессию — id получим через flush."""
    start_day = chat.plan_start_day or 1
    start = period_start(start_day, date)
    end = period_end(start)
    if item.id is None:
        await session.flush()
        months: list[PlanItemMonth] = []
    else:
        months = (await _months_of(session, [item.id]))[item.id]
        if not months:
            # Статья до помесячного плана: её процент остаётся за всеми прошлыми месяцами
            months = [PlanItemMonth(item_id=item.id, period=dt.date(2000, 1, 1), percent=item.percent)]
            session.add(months[0])
    past = start < period_start(start_day, today or dt.date.today())
    next_end = period_end(end)
    next_before = _percent_in(item, months, next_end) if months else None

    inside = [m for m in months if start <= m.period < end]
    if inside:
        inside[-1].percent = percent
        for extra in inside[:-1]:
            await session.delete(extra)
    else:
        months.append(PlanItemMonth(item_id=item.id, period=start, percent=percent))
        session.add(months[-1])
    if past and not any(end <= m.period < next_end for m in months) and next_before != percent:
        session.add(PlanItemMonth(item_id=item.id, period=end, percent=next_before))
    if percent is not None:
        item.percent = percent
    await session.flush()


async def is_used(session: AsyncSession, item: PlanItem) -> bool:
    """Есть ли статья хоть в одном месяце плана."""
    months = (await _months_of(session, [item.id]))[item.id]
    return not months or any(m.percent is not None for m in months)


async def money_entries(session: AsyncSession, chat: Chat, start: dt.date, end: dt.date) -> list[Entry]:
    """Сколько своих денег ушло в [start, end): траты (твоя доля; покупки кредиткой в них не
    входят, погашения карт входят) плюс старые платежи «Внести», которые тратой не записывались."""
    # Как и во вкладке «Траты» «Моих финансов»: сколько владелец потратил сам (app/shared/my_spending.py)
    out = [
        Entry(m.expense.expense_date, m.expense.title, m.amount, "expense", m.expense.category, m.expense.subcategory)
        for m in await my_expenses(session, chat.id, start, end)
        if m.amount
    ]
    payments = (
        await session.execute(
            select(CreditOperation, CreditCard)
            .join(CreditCard, CreditCard.id == CreditOperation.card_id)
            .where(
                CreditCard.chat_id == chat.id,
                CreditOperation.kind == "payment",
                CreditOperation.op_date >= start,
                CreditOperation.op_date < end,
            )
        )
    ).all()
    out += [Entry(o.op_date, c.title, o.amount, "card", c.category, c.subcategory) for o, c in payments]
    return out


async def _flows(session: AsyncSession, chat: Chat, start: dt.date, end: dt.date) -> _Flows:
    incomes = (
        await session.execute(
            select(Income.income_date, Income.amount).where(
                Income.chat_id == chat.id, Income.income_date >= start, Income.income_date < end
            )
        )
    ).all()
    out = await money_entries(session, chat, start, end)
    deposits = (
        await session.execute(
            select(GoalDeposit.deposit_date, Goal.title, GoalDeposit.goal_id, GoalDeposit.amount)
            .join(Goal, Goal.id == GoalDeposit.goal_id)
            .where(Goal.chat_id == chat.id, GoalDeposit.deposit_date >= start, GoalDeposit.deposit_date < end)
        )
    ).all()
    out += [Entry(d, t, a, "deposit", goal_id=g) for d, t, g, a in deposits]
    return _Flows(incomes=[(d, a) for d, a in incomes], outflows=out)


async def compute(session: AsyncSession, chat: Chat, date: dt.date) -> tuple[PlanSummary, dict[int | None, list[Entry]]]:
    """План на месяц плана, в который попадает date, и списания каждой статьи
    (ключ None — «Свободно»)."""
    start_day = chat.plan_start_day or 1
    start = period_start(start_day, date)
    end = period_end(start)
    all_items = await items_of(session, chat.id)
    month = await month_items(session, chat, start)
    items = [i for i, _ in month]
    percents = {i.id: p for i, p in month}

    started = None
    carried = ZERO
    if all_items:
        first = min(i.created_at for i in all_items).date()
        started = period_start(start_day, first)
        if started < start:
            # Все остатки прошлых месяцев переходят в «Свободно»: доходы минус все списания
            past = await _flows(session, chat, started, start)
            carried = sum((a for _, a in past.incomes), ZERO) - sum((e.amount for e in past.outflows), ZERO)

    flows = await _flows(session, chat, start, end)
    income = sum((a for _, a in flows.incomes), ZERO)
    # Перенесённые деньги — такие же текущие деньги месяца: делятся по процентам вместе с
    # поступлениями. Минус не делится — он уменьшает «Свободно».
    base = income + max(carried, ZERO)

    goal_ids = {i.goal_id for i in items if i.kind == "goal" and i.goal_id}
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

    # Куда идёт трата: «Категория › Подкатегория» точнее, чем вся категория
    by_key: dict[str, int] = {}
    by_goal: dict[int, int] = {}
    for item in items:
        if item.kind == "spend":
            for key in loads(item.categories):
                by_key.setdefault(str(key), item.id)
        elif item.kind == "goal" and item.goal_id in goals:
            by_goal.setdefault(item.goal_id, item.id)

    def item_for(e: Entry) -> int | None:
        if e.source == "deposit":
            return by_goal.get(e.goal_id)
        if e.subcategory and f"{e.category}{SUB_SEP}{e.subcategory}" in by_key:
            return by_key[f"{e.category}{SUB_SEP}{e.subcategory}"]
        return by_key.get(e.category or "")

    entries: dict[int | None, list[Entry]] = {}
    for e in flows.outflows:
        entries.setdefault(item_for(e), []).append(e)
    for lst in entries.values():
        lst.sort(key=lambda e: e.date, reverse=True)

    statuses: list[ItemStatus] = []
    for item in items:
        st = ItemStatus(
            id=item.id,
            title=item.title,
            kind=item.kind,
            percent=percents[item.id],
            allocated=share_of(base, percents[item.id]),
            spent=sum((e.amount for e in entries.get(item.id, [])), ZERO),
        )
        if item.kind == "spend":
            st.categories = [str(c) for c in loads(item.categories)]
        elif item.kind == "goal":
            st.goal_id = item.goal_id
            if item.goal_id in goals:
                goal, saved = goals[item.goal_id]
                st.goal_title, st.goal_saved, st.goal_target = goal.title, saved, goal.target
        statuses.append(st)

    # Долг по картам — у статьи, куда идут платежи по ним: столько ещё предстоит внести
    debts: dict[int | None, Decimal] = {}
    for card in await cards_of(session, chat.id):
        if card.is_archived:
            continue
        key = item_for(Entry(start, card.title, ZERO, "card", card.category, card.subcategory))
        debts[key] = debts.get(key, ZERO) + await debt_of(session, card)
    for st in statuses:
        st.card_debt = debts.get(st.id)

    free_percent = max(HUNDRED - sum(percents.values(), ZERO), ZERO)
    free = ItemStatus(
        id=None,
        title=FREE_TITLE,
        kind="free",
        percent=free_percent,
        # Остаток от округления долей статей тоже достаётся «Свободно»
        allocated=base - sum((s.allocated for s in statuses), ZERO) + min(carried, ZERO),
        spent=sum((e.amount for e in entries.get(None, [])), ZERO),
        carried=min(carried, ZERO),
        card_debt=debts.get(None),
    )
    statuses.append(free)
    summary_ = PlanSummary(
        start=start,
        end=end,
        start_day=start_day,
        income=income,
        incomes_count=len(flows.incomes),
        carried=carried,
        items=statuses,
        started=started,
    )
    return summary_, entries


async def summary(session: AsyncSession, chat: Chat, date: dt.date) -> PlanSummary:
    """План на месяц плана, в который попадает date."""
    return (await compute(session, chat, date))[0]


async def item_entries(session: AsyncSession, chat: Chat, date: dt.date, item_id: int | None) -> list[Entry]:
    """На что ушли деньги статьи (None — «Свободно») в месяце плана, свежие сверху."""
    return (await compute(session, chat, date))[1].get(item_id, [])


async def savings_for_income(session: AsyncSession, income: Income) -> list[tuple[PlanItem, Goal, Decimal]]:
    """Сколько отложить на цели с этого поступления по плану: [(статья, цель, сумма)]."""
    out = []
    chat = await session.get(Chat, income.chat_id)
    for item, percent in await month_items(session, chat, income.income_date):
        if item.kind != "goal" or not item.goal_id:
            continue
        goal = await session.get(Goal, item.goal_id)
        amount = share_of(income.amount, percent)
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
