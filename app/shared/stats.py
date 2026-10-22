"""Статистика за период для вкладки «Статистика»: категории с прошлым периодом и средним для
сравнения, траты нарастающим итогом по дням (этот и прошлый месяц), итоги за 12 месяцев (всего
и по категориям), дни недели, крупнейшие траты, частые места, регулярные платежи.

Период — месяц (как раньше) или произвольный диапазон дат: квартал, год, свой. Одна выборка
трат (fetch_range) — с начала прошлого периода или 12 месяцев тренда, заканчивая периодом, —
из неё считается всё."""
from __future__ import annotations

import calendar
import datetime as dt
from decimal import Decimal
from typing import NamedTuple

from app.shared.autocategory import normalize
from app.shared.budgets import month_range
from app.shared.categories import summarize_by_category
from app.shared.schemas import CategoryTrend, MonthTotal, PlaceStat, StatsOut, TopExpense

TREND_MONTHS = 12
AVG_MONTHS = 3
MAX_DAYS = 3 * 366  # длиннее свой период не берём: это уже не статистика, а архив
TOP_EXPENSES = 5
TOP_PLACES = 5
ZERO = Decimal("0")


class Row(NamedTuple):
    date: dt.date
    category: str
    subcategory: str | None
    amount: Decimal
    title: str = ""
    recurring: bool = False


def shift_month(month: str, delta: int) -> str:
    year, mon = (int(p) for p in month.split("-"))
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _ym(date: dt.date) -> str:
    return date.strftime("%Y-%m")


def stats_period(
    month: str | None = None, date_from: dt.date | None = None, date_to: dt.date | None = None
) -> tuple[dt.date, dt.date]:
    """Период статистики [start, end): месяц 'YYYY-MM' или даты с … по … включительно.
    ValueError, если ничего не задано, формат плохой или диапазон пустой/слишком длинный."""
    if date_from is not None or date_to is not None:
        if date_from is None or date_to is None or date_to < date_from:
            raise ValueError("bad range")
        if (date_to - date_from).days >= MAX_DAYS:
            raise ValueError("range too long")
        return date_from, date_to + dt.timedelta(days=1)
    if not month:
        raise ValueError("no period")
    return month_range(month)


def _whole_months(start: dt.date, end: dt.date) -> int | None:
    """Сколько целых календарных месяцев в периоде, если он из них и состоит."""
    if start.day != 1 or end.day != 1:
        return None
    return (end.year - start.year) * 12 + end.month - start.month


def prev_period(start: dt.date, end: dt.date) -> tuple[dt.date, dt.date]:
    """Прошлый период той же длины: для целых месяцев — те же месяцы раньше (квартал → прошлый
    квартал), иначе — столько же дней перед началом."""
    months = _whole_months(start, end)
    if months:
        prev_start, _ = month_range(shift_month(_ym(start), -months))
        return prev_start, start
    return start - (end - start), start


def fetch_range(start: dt.date, end: dt.date) -> tuple[dt.date, dt.date]:
    """Какие траты нужны build_stats: прошлый период и 12 месяцев тренда до конца периода."""
    trend_start, _ = month_range(shift_month(_ym(end - dt.timedelta(days=1)), -(TREND_MONTHS - 1)))
    return min(prev_period(start, end)[0], trend_start), end


def trend_months(end: dt.date) -> list[str]:
    last = _ym(end - dt.timedelta(days=1))
    return [shift_month(last, -i) for i in range(TREND_MONTHS - 1, -1, -1)]


def _cumulative(rows: list[Row], month: str) -> list[Decimal]:
    year, mon = (int(p) for p in month.split("-"))
    per_day = [ZERO] * calendar.monthrange(year, mon)[1]
    for row in rows:
        per_day[row.date.day - 1] += row.amount
    running, out = ZERO, []
    for amount in per_day:
        running += amount
        out.append(running)
    return out


def _places(rows: list[Row]) -> list[PlaceStat]:
    """Названия, которые повторяются хотя бы дважды, — по числу трат, потом по сумме."""
    groups: dict[str, list[Row]] = {}
    for row in rows:
        key = normalize(row.title)
        if key:
            groups.setdefault(key, []).append(row)
    places = [
        PlaceStat(title=items[0].title.strip(), count=len(items), total=sum((r.amount for r in items), ZERO))
        for items in groups.values()
        if len(items) >= 2
    ]
    places.sort(key=lambda p: (p.count, p.total), reverse=True)
    return places[:TOP_PLACES]


def build_stats(start: dt.date, end: dt.date, rows: list[Row]) -> StatsOut:
    """rows — траты за fetch_range(start, end)."""
    rows = [Row(*r) for r in rows]
    prev_start, prev_end = prev_period(start, end)
    cur_rows = [r for r in rows if start <= r.date < end]
    prev_rows = [r for r in rows if prev_start <= r.date < prev_end]
    by_month: dict[str, list[Row]] = {}
    for row in rows:
        by_month.setdefault(_ym(row.date), []).append(row)

    total, by_category = summarize_by_category((r.category, r.subcategory, r.amount) for r in cur_rows)
    prev_total, prev_by_category = summarize_by_category((r.category, r.subcategory, r.amount) for r in prev_rows)
    prev_by_name = {c.category: c.total for c in prev_by_category}
    for c in by_category:
        c.prev_total = prev_by_name.get(c.category, ZERO)

    is_month = _whole_months(start, end) == 1
    month = _ym(start)
    avg_total = None
    if is_month:
        # Среднее — по прошлым месяцам, где вообще были траты: у нового пользователя пустые
        # месяцы до начала учёта иначе занизили бы среднее
        active = [m for m in (shift_month(month, -i) for i in range(1, AVG_MONTHS + 1)) if by_month.get(m)]
        if active:
            n = Decimal(len(active))
            avg_rows = [r for m in active for r in by_month[m]]
            avg_total = (sum((r.amount for r in avg_rows), ZERO) / n).quantize(Decimal("0.01"))
            avg_by_name: dict[str, Decimal] = {}
            for r in avg_rows:
                avg_by_name[r.category] = avg_by_name.get(r.category, ZERO) + r.amount
            for c in by_category:
                c.avg_total = (avg_by_name.get(c.category, ZERO) / n).quantize(Decimal("0.01"))

    months = trend_months(end)
    trend = [MonthTotal(month=m, total=sum((r.amount for r in by_month.get(m, [])), ZERO)) for m in months]
    per_category: dict[str, list[Decimal]] = {}
    for i, m in enumerate(months):
        for r in by_month.get(m, []):
            per_category.setdefault(r.category, [ZERO] * len(months))[i] += r.amount
    category_trend = sorted(
        (CategoryTrend(category=c, totals=t) for c, t in per_category.items()),
        key=lambda c: sum(c.totals, ZERO),
        reverse=True,
    )

    weekdays = [ZERO] * 7
    for r in cur_rows:
        weekdays[r.date.weekday()] += r.amount
    recurring = [r for r in cur_rows if r.recurring]
    top = sorted((r for r in cur_rows if r.amount > 0), key=lambda r: (r.amount, r.date), reverse=True)

    return StatsOut(
        period=month if is_month else f"{start.isoformat()}/{(end - dt.timedelta(days=1)).isoformat()}",
        date_from=start,
        date_to=end - dt.timedelta(days=1),
        is_month=is_month,
        total=total,
        count=len(cur_rows),
        by_category=by_category,
        prev_total=prev_total,
        avg_total=avg_total,
        daily=_cumulative(cur_rows, month) if is_month else [],
        prev_daily=_cumulative(prev_rows, shift_month(month, -1)) if is_month else [],
        trend=trend,
        category_trend=category_trend,
        weekdays=weekdays,
        top_expenses=[
            TopExpense(title=r.title, category=r.category, expense_date=r.date, amount=r.amount)
            for r in top[:TOP_EXPENSES]
        ],
        places=_places(cur_rows),
        recurring_total=sum((r.amount for r in recurring), ZERO),
        recurring_count=len(recurring),
    )
