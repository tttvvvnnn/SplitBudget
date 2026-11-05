"""Статистика за период для вкладки «Статистика»: категории с прошлым периодом, средним и тем
же периодом год назад для сравнения, траты нарастающим итогом по дням (этот и прошлый месяц),
итоги за 12 месяцев (всего, по категориям и обязательные), траты по дням за год, дни недели,
крупнейшие траты, частые места, регулярные платежи, изменение цен и наблюдения (insights.py).

Период — месяц (как раньше) или произвольный диапазон дат: квартал, год, свой. Одна выборка
трат (fetch_range) — с начала прошлого периода, того же периода год назад или 12 месяцев
тренда, заканчивая периодом, — из неё считается всё."""
from __future__ import annotations

import calendar
import datetime as dt
from decimal import Decimal
from typing import NamedTuple

from app.shared.autocategory import normalize
from app.shared.budgets import month_range
from app.shared.categories import summarize_by_category
from app.shared.insights import build_insights
from app.shared.schemas import CategoryTrend, MonthTotal, PlaceStat, PriceChange, StatsOut, TopExpense

TREND_MONTHS = 12
AVG_MONTHS = 3
MAX_DAYS = 3 * 366  # длиннее свой период не берём: это уже не статистика, а архив
TOP_EXPENSES = 5
TOP_PLACES = 5
TOP_PRICES = 8
YEAR_AGO_MIN_COUNT = 5  # пара трат год назад — это начало учёта, а не «тот же месяц»
# Цена — это одна и та же покупка: подписка, обед в том же месте. Чеки «Пятёрочки» на 300 и на
# 5000 — разные корзины, а не подорожание, поэтому разброс сумм в группе ограничен.
PRICE_SPREAD = Decimal("2.5")
PRICE_MIN_CHANGE = Decimal("3")  # меньше 3% — шум и копейки округления
PRICE_RECENT_MONTHS = 3  # последняя цена — не старше трёх месяцев, иначе это уже не «сейчас»
ZERO = Decimal("0")
# Обязательные платежи не показывают, в какие дни человек тратит: аренда в 40 тысяч, оплаченная
# в пятницу, сделала бы пятницу «днём трат». В график по дням недели они не идут: траты из
# «Платежей», категория «Кредиты» (в ней и погашения кредиток) и аренда с коммуналкой.
MANDATORY_CATEGORIES = {"Кредиты"}
MANDATORY_SUBCATEGORIES = {("Дом", "Аренда"), ("Дом", "Коммуналка")}


class Row(NamedTuple):
    date: dt.date
    category: str
    subcategory: str | None
    amount: Decimal
    title: str = ""
    recurring: bool = False


def is_mandatory(row: Row) -> bool:
    return (
        row.recurring
        or row.category in MANDATORY_CATEGORIES
        or (row.category, row.subcategory) in MANDATORY_SUBCATEGORIES
    )


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


def year_ago_period(start: dt.date, end: dt.date) -> tuple[dt.date, dt.date] | None:
    """Тот же период год назад: октябрь 2026 → октябрь 2025, III квартал → III квартал прошлого
    года. Только для месяца и квартала: у года это и есть прошлый период, у своего диапазона —
    непонятно, что сравнивать."""
    months = _whole_months(start, end)
    if not months or months >= 12:
        return None
    first = _ym(start)
    return month_range(shift_month(first, -12))[0], month_range(shift_month(first, months - 12))[0]


def fetch_range(start: dt.date, end: dt.date) -> tuple[dt.date, dt.date]:
    """Какие траты нужны build_stats: прошлый период, тот же период год назад и 12 месяцев
    тренда до конца периода."""
    trend_start, _ = month_range(shift_month(_ym(end - dt.timedelta(days=1)), -(TREND_MONTHS - 1)))
    starts = [prev_period(start, end)[0], trend_start]
    year_ago = year_ago_period(start, end)
    if year_ago:
        starts.append(year_ago[0])
    return min(starts), end


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


def _median(values: list[Decimal]) -> Decimal:
    values = sorted(values)
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def prices(rows: list[Row], months: list[str]) -> tuple[list[PriceChange], Decimal | None]:
    """Личная инфляция: как изменилась цена того, что покупаешь регулярно. Группа — траты с
    одним названием (нормализованным) за months; цена месяца — медиана его трат. Сравниваем
    первый месяц с последним, если между ними хотя бы два месяца, а последний — из последних
    трёх. Плюс общий индекс: на сколько процентов подорожали эти покупки, с весом по сумме трат."""
    window = set(months)
    recent = set(months[-PRICE_RECENT_MONTHS:])
    groups: dict[str, list[Row]] = {}
    for r in rows:
        key = normalize(r.title)
        if key and r.amount > 0 and _ym(r.date) in window:
            groups.setdefault(key, []).append(r)
    out: list[tuple[PriceChange, Decimal]] = []
    for items in groups.values():
        by_month: dict[str, list[Decimal]] = {}
        for r in items:
            by_month.setdefault(_ym(r.date), []).append(r.amount)
        if len(items) < 3 or len(by_month) < 2:
            continue
        amounts = [r.amount for r in items]
        if max(amounts) > min(amounts) * PRICE_SPREAD:
            continue
        first, last = min(by_month), max(by_month)
        if last not in recent or months.index(last) - months.index(first) < 2:
            continue
        old, new = _median(by_month[first]), _median(by_month[last])
        pct = ((new - old) * 100 / old).quantize(Decimal("0.1"))
        latest = max(items, key=lambda r: r.date)
        change = PriceChange(
            title=latest.title.strip(), category=latest.category, old=old, new=new, percent=pct,
            first_month=first, last_month=last, count=len(items),
        )
        out.append((change, sum(amounts, ZERO)))
    if not out:
        return [], None
    weight = sum((w for _, w in out), ZERO)
    index = (sum((c.percent * w for c, w in out), ZERO) / weight).quantize(Decimal("0.1"))
    changed = [c for c, _ in out if abs(c.percent) >= PRICE_MIN_CHANGE]
    changed.sort(key=lambda c: abs(c.percent), reverse=True)
    return changed[:TOP_PRICES], index


def build_stats(start: dt.date, end: dt.date, rows: list[Row], today: dt.date | None = None) -> StatsOut:
    """rows — траты за fetch_range(start, end). Если период ещё идёт, сравнение (prev_to_date и
    среднее) — с тем же сроком от начала прошлых периодов: 22 дня октября против 22 дней
    сентября, а не против всего сентября."""
    rows = [Row(*r) for r in rows]
    today = today or dt.date.today()
    prev_start, prev_end = prev_period(start, end)
    # сколько дней периода уже прошло, если он ещё идёт
    cutoff = (today - start).days + 1 if start <= today < end - dt.timedelta(days=1) else None
    cur_rows = [r for r in rows if start <= r.date < end]
    prev_rows = [r for r in rows if prev_start <= r.date < prev_end]
    by_month: dict[str, list[Row]] = {}
    for row in rows:
        by_month.setdefault(_ym(row.date), []).append(row)

    total, by_category = summarize_by_category((r.category, r.subcategory, r.amount) for r in cur_rows)
    prev_total, prev_by_category = summarize_by_category((r.category, r.subcategory, r.amount) for r in prev_rows)
    prev_by_name = {c.category: c for c in prev_by_category}
    for c in by_category:
        prev = prev_by_name.get(c.category)
        c.prev_total = prev.total if prev else ZERO
        c.prev_count = prev.count if prev else 0
    prev_to_date = None
    if cutoff:
        to_date = [r for r in prev_rows if (r.date - prev_start).days < cutoff]
        prev_to_date, to_date_by_category = summarize_by_category((r.category, r.subcategory, r.amount) for r in to_date)
        to_date_by_name = {c.category: c for c in to_date_by_category}
        for c in by_category:
            prev = to_date_by_name.get(c.category)
            c.prev_to_date = prev.total if prev else ZERO
            c.prev_count = prev.count if prev else 0  # «чаще или дороже» — с тем же сроком

    # Тот же период год назад (если идёт — за тот же срок): видно сезонность — подарки в декабре,
    # отпуск летом. Без трат в нём (учёта ещё не было или только начался) сравнивать не с чем.
    year_ago_total = None
    year_ago = year_ago_period(start, end)
    if year_ago:
        ya_rows = [
            r for r in rows
            if year_ago[0] <= r.date < year_ago[1] and (not cutoff or (r.date - year_ago[0]).days < cutoff)
        ]
        if len(ya_rows) >= YEAR_AGO_MIN_COUNT:
            year_ago_total, ya_by_category = summarize_by_category((r.category, r.subcategory, r.amount) for r in ya_rows)
            ya_by_name = {c.category: c.total for c in ya_by_category}
            for c in by_category:
                c.year_ago_total = ya_by_name.get(c.category, ZERO)

    is_month = _whole_months(start, end) == 1
    month = _ym(start)
    avg_total = None
    if is_month:
        # Среднее — по прошлым месяцам, где вообще были траты: у нового пользователя пустые
        # месяцы до начала учёта иначе занизили бы среднее
        active = [m for m in (shift_month(month, -i) for i in range(1, AVG_MONTHS + 1)) if by_month.get(m)]
        if active:
            n = Decimal(len(active))
            avg_rows = [r for m in active for r in by_month[m] if not cutoff or r.date.day <= cutoff]
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
    # Обязательные траты по месяцам: сколько уходит на аренду, кредиты и «Платежи» и сколько
    # остаётся «на жизнь» (остальное — trend минус это)
    mandatory_trend = [sum((r.amount for r in by_month.get(m, []) if is_mandatory(r)), ZERO) for m in months]
    # Траты по дням за 12 месяцев тренда для тепловой карты года — без обязательных платежей,
    # как и дни недели: аренда раз в месяц не делает день «днём трат»
    days_from, _ = month_range(months[0])
    day_totals = [ZERO] * (end - days_from).days
    for r in rows:
        if days_from <= r.date < end and not is_mandatory(r):
            day_totals[(r.date - days_from).days] += r.amount
    price_changes, price_index = prices(rows, months)

    weekdays = [ZERO] * 7
    weekday_counts = [0] * 7
    mandatory = [r for r in cur_rows if is_mandatory(r)]
    for r in cur_rows:
        if is_mandatory(r):
            continue
        weekdays[r.date.weekday()] += r.amount
        if r.amount > 0:
            weekday_counts[r.date.weekday()] += 1
    recurring = [r for r in cur_rows if r.recurring]
    top = sorted((r for r in cur_rows if r.amount > 0), key=lambda r: (r.amount, r.date), reverse=True)

    out = StatsOut(
        period=month if is_month else f"{start.isoformat()}/{(end - dt.timedelta(days=1)).isoformat()}",
        date_from=start,
        date_to=end - dt.timedelta(days=1),
        is_month=is_month,
        total=total,
        count=len(cur_rows),
        by_category=by_category,
        prev_total=prev_total,
        prev_to_date=prev_to_date,
        avg_total=avg_total,
        daily=_cumulative(cur_rows, month) if is_month else [],
        prev_daily=_cumulative(prev_rows, shift_month(month, -1)) if is_month else [],
        year_ago_total=year_ago_total,
        trend=trend,
        category_trend=category_trend,
        mandatory_trend=mandatory_trend,
        days_from=days_from,
        day_totals=day_totals,
        weekdays=weekdays,
        weekday_counts=weekday_counts,
        weekdays_excluded=sum((r.amount for r in mandatory), ZERO),
        weekdays_excluded_count=len(mandatory),
        top_expenses=[
            TopExpense(title=r.title, category=r.category, expense_date=r.date, amount=r.amount)
            for r in top[:TOP_EXPENSES]
        ],
        places=_places(cur_rows),
        recurring_total=sum((r.amount for r in recurring), ZERO),
        recurring_count=len(recurring),
        prices=price_changes,
        price_index=price_index,
    )
    out.insights = build_insights(out, rows, today)
    return out
