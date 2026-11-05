"""Аналитика «Плана»: как статьи выполнялись за полгода и куда ушли деньги месяца.

План против факта (history): по каждой статье за 6 месяцев плана — выделено и потрачено. Статья,
которая в большинстве прошедших месяцев уходит в минус, получает подсказку: какой процент
покрыл бы её обычные траты. Текущий месяц показываем, но в подсчёт «уходит в минус» не берём —
он ещё не закончился.

Куда ушли деньги (flow): поток месяца плана для диаграммы Санки — доходы (и перенос с прошлого
месяца) → статьи плана → категории трат, отложенное на цели и то, что осталось. Перерасход
статьи — отдельный источник слева: эти деньги взяты из остатков других статей или в долг.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.shared import plan
from app.shared.models import Chat
from app.shared.schemas import (
    PlanFlowLinkOut,
    PlanFlowNodeOut,
    PlanFlowOut,
    PlanHistoryItemOut,
    PlanHistoryOut,
    PlanHistoryPeriodOut,
)

ZERO = Decimal("0")
HISTORY_MONTHS = 6
OVER_SHARE = Decimal("0.5")  # уходит в минус хотя бы в половине прошедших месяцев
FLOW_CATEGORIES = 8  # больше — «Остальное»
FREE_KEY = "free"


def _key(item_id: int | None) -> str:
    return FREE_KEY if item_id is None else str(item_id)


def _round_half(value: Decimal) -> Decimal:
    return (value * 2).quantize(Decimal("1")) / 2


async def history(session: AsyncSession, chat: Chat, date: dt.date, today: dt.date | None = None) -> PlanHistoryOut:
    today = today or dt.date.today()
    current = plan.period_start(chat.plan_start_day or 1, date)
    starts = [current]
    for _ in range(HISTORY_MONTHS - 1):
        starts.insert(0, plan.period_start(chat.plan_start_day or 1, starts[0] - dt.timedelta(days=1)))
    periods: list[PlanHistoryPeriodOut] = []
    rows: dict[str, PlanHistoryItemOut] = {}
    percents: dict[str, list[Decimal | None]] = {}
    bases: list[Decimal] = []
    for n, start in enumerate(starts):
        summary = await plan.summary(session, chat, start)
        if summary.started is None or summary.start < summary.started:
            # плана ещё не было — месяц пустой
            periods.append(PlanHistoryPeriodOut(start=summary.start, end=summary.end - dt.timedelta(days=1),
                                                income=ZERO, finished=summary.end <= today, active=False))
            bases.append(ZERO)
            continue
        periods.append(PlanHistoryPeriodOut(
            start=summary.start, end=summary.end - dt.timedelta(days=1), income=summary.income,
            finished=summary.end <= today, active=True,
        ))
        bases.append(summary.income + max(summary.carried, ZERO))
        for st in summary.items:
            key = _key(st.id)
            row = rows.get(key)
            if row is None:
                row = rows[key] = PlanHistoryItemOut(
                    id=st.id, title=st.title, kind=st.kind,
                    allocated=[None] * len(starts), spent=[None] * len(starts),
                )
                percents[key] = [None] * len(starts)
            row.title = st.title  # название — как в последнем месяце
            row.allocated[n] = st.allocated
            row.spent[n] = st.spent
            row.percent = st.percent
            percents[key][n] = st.percent

    for key, row in rows.items():
        done = [
            i for i, p in enumerate(periods)
            if p.finished and row.allocated[i] is not None and (row.allocated[i] or row.spent[i])
        ]
        row.months = len(done)
        row.over = sum(1 for i in done if (row.spent[i] or ZERO) > (row.allocated[i] or ZERO))
        if row.kind == "spend" and done and row.over >= max(len(done) * OVER_SHARE, 2):
            # Сколько процентов от денег месяца в среднем уходило на статью
            used = [
                (row.spent[i] or ZERO) * 100 / bases[i]
                for i in done if bases[i] > 0
            ]
            if used:
                suggestion = _round_half(sum(used, ZERO) / len(used))
                if row.percent is not None and suggestion > row.percent:
                    row.suggested_percent = suggestion
    items = sorted(rows.values(), key=lambda r: (r.kind == "free", -(r.over or 0)))
    return PlanHistoryOut(periods=periods, items=items)


async def flow(session: AsyncSession, chat: Chat, date: dt.date) -> PlanFlowOut:
    summary, entries = await plan.compute(session, chat, date)
    nodes: list[PlanFlowNodeOut] = []
    links: list[PlanFlowLinkOut] = []

    def link(source: str, target: str, value: Decimal) -> None:
        if value > 0:
            links.append(PlanFlowLinkOut(source=source, target=target, value=value))

    nodes.append(PlanFlowNodeOut(id="income", title="Доходы", column=0, kind="income"))
    if summary.carried > 0:
        nodes.append(PlanFlowNodeOut(id="carried", title="С прошлого месяца", column=0, kind="income"))
    over_total = sum((max(st.spent - max(st.allocated, ZERO), ZERO) for st in summary.items), ZERO)
    if over_total > 0:
        nodes.append(PlanFlowNodeOut(id="over", title="Перерасход", column=0, kind="over"))

    base = summary.income + max(summary.carried, ZERO)
    by_category: dict[str, Decimal] = {}
    item_cats: list[tuple[str, str, Decimal]] = []
    left_total = ZERO
    for st in summary.items:
        key = f"item:{_key(st.id)}"
        allocated = max(st.allocated, ZERO)
        if not allocated and not st.spent:
            continue
        nodes.append(PlanFlowNodeOut(id=key, title=st.title, column=1, kind=st.kind))
        # Деньги статьи — её доля доходов и переноса
        if base > 0:
            link("income", key, allocated * summary.income / base)
            if summary.carried > 0:
                link("carried", key, allocated * summary.carried / base)
        link("over", key, max(st.spent - allocated, ZERO))
        for e in entries.get(st.id, []):
            target = f"goal:{e.goal_id}" if e.source == "deposit" else (e.category or "Другое")
            by_category[target] = by_category.get(target, ZERO) + e.amount
            item_cats.append((key, target, e.amount))
        left = st.allocated - st.spent
        if left > 0:
            link(key, "left", left)
            left_total += left

    # Мелкие категории — в «Остальное», чтобы диаграмма читалась
    spend_targets = sorted((t for t in by_category if not t.startswith("goal:")), key=lambda t: by_category[t], reverse=True)
    keep = set(spend_targets[:FLOW_CATEGORIES])
    merged: dict[tuple[str, str], Decimal] = {}
    for source, target, amount in item_cats:
        if not target.startswith("goal:") and target not in keep:
            target = "rest"
        merged[(source, target)] = merged.get((source, target), ZERO) + amount
    titles = {}
    for (source, target), amount in merged.items():
        if amount <= 0:
            continue
        if target not in titles:
            if target == "rest":
                titles[target] = ("Остальное", "category")
            elif target.startswith("goal:"):
                titles[target] = ("Отложено на цели", "goal")
            else:
                titles[target] = (target, "category")
        link(source, f"cat:{target}", amount)
    # Все цели — одним узлом «Отложено на цели»
    for link_ in links:
        if link_.target.startswith("cat:goal:"):
            link_.target = "cat:goals"
    goal_seen = False
    for target, (title, kind) in sorted(titles.items(), key=lambda kv: -by_category.get(kv[0], ZERO)):
        if kind == "goal":
            if goal_seen:
                continue
            goal_seen = True
            nodes.append(PlanFlowNodeOut(id="cat:goals", title=title, column=2, kind="goal"))
        else:
            nodes.append(PlanFlowNodeOut(id=f"cat:{target}", title=title, column=2, kind="category"))
    if left_total > 0:
        nodes.append(PlanFlowNodeOut(id="left", title="Осталось", column=2, kind="left"))

    # Одинаковые пары источник → цель — одной связью
    combined: dict[tuple[str, str], Decimal] = {}
    for l in links:
        combined[(l.source, l.target)] = combined.get((l.source, l.target), ZERO) + l.value
    return PlanFlowOut(
        start=summary.start,
        end=summary.end - dt.timedelta(days=1),
        income=summary.income,
        carried=summary.carried,
        spent=sum((st.spent for st in summary.items), ZERO),
        nodes=nodes,
        links=[PlanFlowLinkOut(source=s, target=t, value=v.quantize(Decimal("0.01"))) for (s, t), v in combined.items()],
    )
