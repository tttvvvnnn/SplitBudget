"""Статистика за месяц для вкладки «Статистика»: категории с прошлым месяцем для сравнения,
траты нарастающим итогом по дням (этот и прошлый месяц) и итоги за 12 месяцев.

Одна выборка трат за 12 месяцев (TREND_MONTHS), заканчивая выбранным, — из неё считается всё."""
from __future__ import annotations

import calendar
import datetime as dt
from decimal import Decimal

from app.shared.budgets import month_range
from app.shared.categories import summarize_by_category
from app.shared.schemas import MonthTotal, StatsOut

TREND_MONTHS = 12
ZERO = Decimal("0")

Row = tuple[dt.date, str, str | None, Decimal]  # (дата, категория, подкатегория, сумма)


def shift_month(month: str, delta: int) -> str:
    year, mon = (int(p) for p in month.split("-"))
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def stats_range(month: str) -> tuple[dt.date, dt.date]:
    """[начало самого раннего месяца тренда, конец выбранного). ValueError при плохом формате."""
    _, end = month_range(month)
    start, _ = month_range(shift_month(month, -(TREND_MONTHS - 1)))
    return start, end


def _cumulative(rows: list[Row], month: str) -> list[Decimal]:
    year, mon = (int(p) for p in month.split("-"))
    per_day = [ZERO] * calendar.monthrange(year, mon)[1]
    for date, _, _, amount in rows:
        per_day[date.day - 1] += amount
    running, out = ZERO, []
    for amount in per_day:
        running += amount
        out.append(running)
    return out


def build_stats(month: str, rows: list[Row]) -> StatsOut:
    prev = shift_month(month, -1)
    by_month: dict[str, list[Row]] = {}
    for row in rows:
        by_month.setdefault(row[0].strftime("%Y-%m"), []).append(row)
    cur_rows, prev_rows = by_month.get(month, []), by_month.get(prev, [])

    total, by_category = summarize_by_category((c, s, a) for _, c, s, a in cur_rows)
    prev_total, prev_by_category = summarize_by_category((c, s, a) for _, c, s, a in prev_rows)
    prev_by_name = {c.category: c.total for c in prev_by_category}
    for c in by_category:
        c.prev_total = prev_by_name.get(c.category, ZERO)

    trend = []
    for i in range(TREND_MONTHS - 1, -1, -1):
        m = shift_month(month, -i)
        trend.append(MonthTotal(month=m, total=sum((r[3] for r in by_month.get(m, [])), ZERO)))

    return StatsOut(
        period=month,
        total=total,
        by_category=by_category,
        prev_total=prev_total,
        daily=_cumulative(cur_rows, month),
        prev_daily=_cumulative(prev_rows, prev),
        trend=trend,
    )
