"""Наблюдения по статистике: короткие выводы карточками сверху вкладки «Статистика» и в итогах
месяца от бота (app/shared/report.py). Вместо «разбери графики сам» — «на кафе в этом месяце
на 45% больше обычного», «7 дней без трат», «Яндекс Плюс подорожал».

Считаются из того же, что и статистика (build_stats): категории со средним и прошлым периодом,
тот же период год назад, цены регулярных покупок, траты по дням. Суммы в тексте — местами {0},
{1}…: их форматирует клиент в валюте пространства. Пороги подобраны так, чтобы не шуметь:
рост на пару сотен рублей или на 10% наблюдением не считается.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

from app.shared.schemas import Insight, StatsOut

if TYPE_CHECKING:
    from app.shared.stats import Row

ZERO = Decimal("0")
MAX_INSIGHTS = 5
NOTABLE_SHARE = Decimal("0.05")  # изменение категории заметно, если это хотя бы 5% всех трат
CATEGORY_UP = 30  # % к обычному
CATEGORY_DOWN = -30
TOTAL_UP = 15
TOTAL_DOWN = -10
YEAR_AGO_CHANGE = 20
YEAR_AGO_MAX = 300  # больше — значит, год назад учёт только начинался, сравнение ни о чём
PRICE_UP = 10
NO_SPEND_MIN = 3
WEEKEND_SHARE = 50
WEEKEND_MIN_COUNT = 10
MANDATORY_SHARE = 50
DRIVER_MIN_COUNT = 3  # «чаще или дороже» по одной-двум покупкам — случайность, а не вывод
RECORD_MIN_COUNT = 5  # «самая крупная за год» — только в категории, где трат хватает для сравнения
NEW_CATEGORY_MONTHS = 3


def plural(n: int, one: str, few: str, many: str) -> str:
    m10, m100 = n % 10, n % 100
    if m10 == 1 and m100 != 11:
        return one
    if 2 <= m10 <= 4 and not 12 <= m100 <= 14:
        return few
    return many


def _pct(now: Decimal, base: Decimal) -> int:
    return round((now - base) * 100 / base)


def _more(pct: int) -> str:
    """«на 45% больше», а рост в разы — «в 3 раза больше»: «на 2163%» не читается."""
    if pct >= 200:
        times = round((pct + 100) / 100)
        return f"в {times} {plural(times, 'раз', 'раза', 'раз')} больше"
    return f"на {pct}% больше"


def _notable(diff: Decimal, base: Decimal) -> bool:
    return abs(diff) >= base * NOTABLE_SHARE


def _longest_run(flags: list[bool]) -> int:
    best = run = 0
    for flag in flags:
        run = run + 1 if flag else 0
        best = max(best, run)
    return best


def build_insights(stats: StatsOut, rows: list[Row], today: dt.date) -> list[Insight]:
    from app.shared.stats import is_mandatory  # stats импортирует этот модуль

    start, end = stats.date_from, stats.date_to + dt.timedelta(days=1)
    cur = [r for r in rows if start <= r.date < end]
    total = stats.total
    if total <= 0:
        return []
    partial = stats.prev_to_date is not None
    out: list[Insight] = []

    # Весь месяц против обычного (среднее за 3 месяца; если месяц идёт — за тот же срок)
    if stats.avg_total:
        pct = _pct(total, stats.avg_total)
        lead = "Пока тратите" if partial else "Месяц вышел"
        if pct >= TOTAL_UP:
            out.append(Insight(
                kind="total_up", icon="📈", tone="bad",
                text=f"{lead} {_more(pct)} обычного: {{0}} против {{1}} в среднем",
                amounts=[total, stats.avg_total],
            ))
        elif pct <= TOTAL_DOWN:
            out.append(Insight(
                kind="total_down", icon="👏", tone="good",
                text=f"{lead} на {-pct}% меньше обычного: {{0}} против {{1}} в среднем",
                amounts=[total, stats.avg_total],
            ))

    # Категории, которые заметно выросли или упали против своего обычного
    ups, downs = [], []
    for c in stats.by_category:
        if not c.avg_total:
            continue
        diff = c.total - c.avg_total
        if not _notable(diff, max(stats.avg_total or ZERO, total)):
            continue
        pct = _pct(c.total, c.avg_total)
        if pct >= CATEGORY_UP:
            ups.append((diff, pct, c))
        elif pct <= CATEGORY_DOWN:
            downs.append((diff, pct, c))
    for diff, pct, c in sorted(ups, key=lambda x: x[0], reverse=True)[:2]:
        out.append(Insight(
            kind="category_up", icon="🔺", tone="bad", category=c.category,
            text=f"«{c.category}» — {{0}}, {_more(pct)} обычного ({{1}} в среднем)",
            amounts=[c.total, c.avg_total],
        ))
    for diff, pct, c in sorted(downs, key=lambda x: x[0])[:1]:
        out.append(Insight(
            kind="category_down", icon="🔻", tone="good", category=c.category,
            text=f"«{c.category}» — {{0}}, на {-pct}% меньше обычного ({{1}} в среднем)",
            amounts=[c.total, c.avg_total],
        ))

    # Чаще или дороже: из-за чего сильнее всего выросла категория против прошлого периода
    best = None
    for c in stats.by_category:
        base = c.prev_to_date if c.prev_to_date is not None else c.prev_total
        if not base or c.prev_count < DRIVER_MIN_COUNT or c.count < DRIVER_MIN_COUNT:
            continue
        diff = c.total - base
        if diff <= 0 or not _notable(diff, total) or _pct(c.total, base) < 20:
            continue
        if best is None or diff > best[0]:
            best = (diff, base, c)
    if best:
        diff, base, c = best
        old_avg, new_avg = base / c.prev_count, c.total / c.count
        by_count = (c.count - c.prev_count) * old_avg
        by_price = c.count * (new_avg - old_avg)
        if by_count >= by_price:
            text = (f"«{c.category}» растёт из-за частоты: {c.count} {plural(c.count, 'раз', 'раза', 'раз')} "
                    f"вместо {c.prev_count}, средний чек {{0}}")
            amounts = [new_avg.quantize(Decimal("1"))]
        else:
            text = (f"«{c.category}» растёт из-за цены: средний чек {{0}} вместо {{1}}, "
                    f"покупок {c.count} против {c.prev_count}")
            amounts = [new_avg.quantize(Decimal("1")), old_avg.quantize(Decimal("1"))]
        out.append(Insight(kind="driver", icon="🔍", tone="info", category=c.category, text=text, amounts=amounts))

    # Тот же период год назад
    if stats.year_ago_total:
        pct = _pct(total, stats.year_ago_total)
        if YEAR_AGO_CHANGE <= abs(pct) <= YEAR_AGO_MAX:
            what = "месяцем" if stats.is_month else "периодом"
            out.append(Insight(
                kind="year_ago", icon="🗓", tone="bad" if pct > 0 else "good",
                text=f"{(_more(pct) if pct > 0 else f'На {-pct}% меньше').capitalize()}, чем тем же {what} год назад ({{0}})",
                amounts=[stats.year_ago_total],
            ))

    # Подорожание регулярной покупки
    up = next((p for p in stats.prices if p.percent >= PRICE_UP), None)
    if up:
        out.append(Insight(
            kind="price_up", icon="🏷", tone="bad", category=up.category,
            text=f"«{up.title}» подорожало: {{0}} → {{1}} (+{up.percent:.0f}%)",
            amounts=[up.old, up.new],
        ))

    # Самая крупная трата в категории за год
    year_from = end - dt.timedelta(days=365)
    # Обязательные (аренда каждый месяц одна и та же) не в счёт, и рекорд — строго больше прочих
    for r in sorted((r for r in cur if r.amount > 0 and not is_mandatory(r)), key=lambda r: r.amount, reverse=True)[:3]:
        others = [
            x.amount for x in rows
            if x.category == r.category and year_from <= x.date < end and x.amount > 0 and x is not r
        ]
        if len(others) >= RECORD_MIN_COUNT - 1 and r.amount > max(others):
            out.append(Insight(
                kind="record", icon="🏆", tone="info", category=r.category,
                text=f"Самая крупная трата в «{r.category}» за год: «{r.title.strip()}» — {{0}}",
                amounts=[r.amount],
            ))
            break

    # Новая категория: раньше на неё не тратили, хотя учёт ведётся давно
    history_from = start - dt.timedelta(days=31 * NEW_CATEGORY_MONTHS)
    if any(r.date < history_from for r in rows):
        before = {r.category for r in rows if history_from <= r.date < start}
        fresh = [c for c in stats.by_category if c.category not in before and _notable(c.total, total)]
        if fresh:
            c = fresh[0]
            out.append(Insight(
                kind="new_category", icon="✨", tone="info", category=c.category,
                text=f"Новое за {NEW_CATEGORY_MONTHS} месяца: «{c.category}» — {{0}}",
                amounts=[c.total],
            ))

    # Дни без трат (обязательные платежи не в счёт: аренда не значит, что в этот день «тратил»)
    if stats.is_month:
        last = min(end, today + dt.timedelta(days=1))
        days = (last - start).days
        if days > 0:
            spent = {r.date for r in cur if r.amount > 0 and not is_mandatory(r)}
            flags = [start + dt.timedelta(days=i) not in spent for i in range(days)]
            free = sum(flags)
            if free >= NO_SPEND_MIN:
                run = _longest_run(flags)
                out.append(Insight(
                    kind="no_spend", icon="🌿", tone="good",
                    text=f"Дней без трат: {free} из {days}"
                         + (f", самая длинная серия — {run} {plural(run, 'день', 'дня', 'дней')}" if run > 1 else ""),
                ))

    # Обязательные траты съедают больше половины
    mandatory = sum((r.amount for r in cur if is_mandatory(r)), ZERO)
    if mandatory * 100 >= total * MANDATORY_SHARE:
        out.append(Insight(
            kind="mandatory", icon="📌", tone="info",
            text=f"Обязательные траты — {round(mandatory * 100 / total)}% всех: {{0}}",
            amounts=[mandatory],
        ))

    # Выходные
    counts = sum(stats.weekday_counts)
    week = sum(stats.weekdays, ZERO)
    if counts >= WEEKEND_MIN_COUNT and week > 0:
        share = round((stats.weekdays[5] + stats.weekdays[6]) * 100 / week)
        if share >= WEEKEND_SHARE:
            out.append(Insight(
                kind="weekend", icon="🎉", tone="info",
                text=f"На выходные приходится {share}% трат (без обязательных)",
            ))
    return out[:MAX_INSIGHTS]
