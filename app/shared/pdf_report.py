"""PDF-отчёт «Моих финансов» за месяц плана: статистика с графиками, план со всеми тратами
каждой статьи, покупки по кредиткам, кредиты и цели.

Месяц — месяц плана (с chat.plan_start_day), чтобы статистика и план совпадали по датам.
Траты считаются так же, как во вкладке «Статистика» «Моих финансов»: личные плюс сколько
потратил сам в семейных чатах (app/shared/my_spending.py), без покупок кредиткой.

Сборка: collect() собирает данные, render_html() рисует HTML с графиками в SVG (без JS, чтобы
их понимал WeasyPrint), to_pdf() превращает HTML в PDF. Тема — светлая или тёмная, как в
приложении у пользователя.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import math
from pathlib import Path
from dataclasses import dataclass, field
from decimal import Decimal
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared import plan
from app.shared.credits import cards_of, debt_of, ledger, loans_of
from app.shared.custom_categories import tree_for
from app.shared.goals import goals_status
from app.shared.loans import remaining
from app.shared.models import Chat, CreditCard, Expense, Income
from app.shared.my_spending import my_expenses
from app.shared.report import MONTHS
from app.shared.schemas import StatsOut
from app.shared.stats import Row, build_stats, fetch_range, prev_period, trend_months

ZERO = Decimal("0")
THEMES = ("light", "dark")
# Те же цвета, что у графиков во вкладке «Статистика» (webapp/js/tabs/stats.js)
PALETTE = ("#2481cc", "#34c759", "#ff9500", "#ff3b30", "#af52de", "#5ac8fa", "#ffcc00",
           "#ff2d55", "#30b0c7", "#a2845e")
OTHER_COLOR = "#8e8e93"
INCOME_COLOR = "#34c759"
DONUT_CATEGORIES = 8  # остальное в кольце — «Остальное»
MONTHS_SHORT = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
MONTHS_GENITIVE = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
                   "сентября", "октября", "ноября", "декабря")
WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
CURRENCY_SIGNS = {"RUB": "₽", "USD": "$", "EUR": "€"}
LOGO = Path(__file__).resolve().parents[2] / "webapp" / "favicon.svg"
KIND_LABELS = {"spend": "расходы", "goal": "накопления", "free": "остаток"}

COLORS = {
    "light": {"bg": "#ffffff", "card": "#f3f7fc", "text": "#0f1b2d", "hint": "#66768c",
              "border": "#dfe6ef", "accent": "#2481cc", "danger": "#e5372c", "success": "#23a047",
              "warning": "#e08600", "grid": "#e4eaf2", "track": "#e4ebf3"},
    "dark": {"bg": "#0d1726", "card": "#16233a", "text": "#eaf1fa", "hint": "#8b9bb3",
             "border": "#24344d", "accent": "#6cb8f5", "danger": "#ff6259", "success": "#4cd471",
             "warning": "#ffa630", "grid": "#22324a", "track": "#24344d"},
}


@dataclass
class CardPurchases:
    card: CreditCard
    debt: Decimal
    purchases: list[tuple[Expense, Decimal]]  # покупка и сколько из неё ещё не погашено


@dataclass
class Report:
    chat: Chat
    start: dt.date
    end: dt.date  # не включительно
    today: dt.date
    stats: StatsOut
    daily: list[Decimal]  # траты нарастающим итогом по дням месяца плана
    prev_daily: list[Decimal]  # то же за прошлый месяц той же длины
    income_total: Decimal
    incomes: list[Income]
    income_trend: list[Decimal]  # по месяцам stats.trend
    plan: plan.PlanSummary | None
    entries: dict[int | None, list[plan.Entry]]
    cards: list[CardPurchases]
    loans: list[tuple[str, Decimal | None, Decimal]]  # название, остаток долга, платёж в месяц
    goals: list[tuple[str, Decimal, Decimal]]  # название, накоплено, цель
    icons: dict[str, str] = field(default_factory=dict)


def _cumulative(rows: list[Row], start: dt.date, days: int) -> list[Decimal]:
    per_day = [ZERO] * days
    for r in rows:
        index = (r.date - start).days
        if 0 <= index < days:
            per_day[index] += r.amount
    running, out = ZERO, []
    for amount in per_day:
        running += amount
        out.append(running)
    return out


async def collect(session: AsyncSession, chat: Chat, date: dt.date, today: dt.date | None = None) -> Report:
    """Данные отчёта за месяц плана, в который попадает date. chat — «Мои финансы»."""
    today = today or dt.date.today()
    summary, entries = await plan.compute(session, chat, date)
    start, end = summary.start, summary.end

    fetch_start, fetch_end = fetch_range(start, end)
    rows = [
        Row(m.expense.expense_date, m.expense.category, m.expense.subcategory, m.amount,
            m.expense.title, m.expense.recurring_id is not None)
        for m in await my_expenses(session, chat.id, fetch_start, fetch_end)
        if m.amount
    ]
    stats = build_stats(start, end, rows, today)
    days = (end - start).days
    prev_start, _ = prev_period(start, end)
    daily = _cumulative(rows, start, days)
    if today < end:
        daily = daily[: max((today - start).days + 1, 0)]

    incomes = (
        await session.execute(
            select(Income).where(Income.chat_id == chat.id, Income.income_date >= fetch_start,
                                 Income.income_date < fetch_end).order_by(Income.income_date)
        )
    ).scalars().all()
    by_month: dict[str, Decimal] = {}
    for inc in incomes:
        key = inc.income_date.strftime("%Y-%m")
        by_month[key] = by_month.get(key, ZERO) + inc.amount
    period_incomes = [i for i in incomes if start <= i.income_date < end]

    cards = []
    for card in await cards_of(session, chat.id):
        purchases = (
            await session.execute(
                select(Expense).where(Expense.card_id == card.id, Expense.expense_date >= start,
                                      Expense.expense_date < end).order_by(Expense.expense_date, Expense.id)
            )
        ).scalars().all()
        debt = await debt_of(session, card)
        if card.is_archived and not purchases:
            continue
        left = (await ledger(session, card)).left if purchases else {}
        cards.append(CardPurchases(card, debt, [(e, left.get(e.id, ZERO)) for e in purchases]))

    loans = [
        (s.recurring.title, remaining(s.recurring, today), s.recurring.amount)
        for s in await loans_of(session, chat, today)
        if s.recurring.is_active
    ]
    goals = [(g.goal.title, g.saved, g.goal.target) for g in await goals_status(session, chat.id, today)
             if not g.goal.is_archived]

    icons = {}
    for node in await tree_for(session, chat):
        icons[node.name] = node.icon

    return Report(
        chat=chat,
        start=start,
        end=end,
        today=today,
        stats=stats,
        daily=daily,
        prev_daily=_cumulative(rows, prev_start, days),
        income_total=sum((i.amount for i in period_incomes), ZERO),
        incomes=period_incomes,
        income_trend=[by_month.get(m, ZERO) for m in trend_months(end)],
        plan=summary if summary.items and summary.started else None,
        entries=entries,
        cards=cards,
        loans=loans,
        goals=goals,
        icons=icons,
    )


# ---------- форматирование ----------

def _money(amount: Decimal | float, currency: str, sign: bool = False) -> str:
    value = float(amount)
    text = f"{abs(value):,.0f}".replace(",", " ")
    prefix = ("+" if value > 0 else "−" if value < 0 else "") if sign else ("−" if value < 0 else "")
    return f"{prefix}{text} {CURRENCY_SIGNS.get(currency, currency)}"


def _short(amount: float) -> str:
    """Подпись оси: 12 тыс, 1,2 млн."""
    if abs(amount) >= 1_000_000:
        return f"{amount / 1_000_000:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " млн"
    if abs(amount) >= 1000:
        return f"{amount / 1000:.0f} тыс"
    return f"{amount:.0f}"


def _date(d: dt.date) -> str:
    return f"{d.day} {MONTHS_GENITIVE[d.month - 1]}"


def _day(d: dt.date) -> str:
    return f"{d.day:02d}.{d.month:02d}"


def period_label(start: dt.date, end: dt.date) -> str:
    last = end - dt.timedelta(days=1)
    if start.day == 1 and last.month == start.month:
        return f"{MONTHS[start.month - 1].capitalize()} {start.year}"
    return f"{_date(start)} – {_date(last)} {last.year}"


def file_name(report: Report) -> str:
    return f"SplitBudget-{report.start.isoformat()}.pdf"


def _pct(part: Decimal | float, whole: Decimal | float) -> float:
    return float(part) * 100 / float(whole) if whole else 0.0


def _nice_max(value: float) -> float:
    if value <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * exp:
            return step * exp
    return 10 * exp


# ---------- графики (SVG) ----------

def _arc(cx: float, cy: float, r: float, a: float) -> tuple[float, float]:
    return cx + r * math.sin(a), cy - r * math.cos(a)


def donut_svg(parts: list[tuple[float, str]], total_label: str, c: dict) -> str:
    size, outer, inner = 180, 86, 58
    cx = cy = size / 2
    total = sum(v for v, _ in parts)
    out = [f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">']
    if total <= 0:
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{(outer + inner) / 2}" fill="none" stroke="{c["track"]}" '
                   f'stroke-width="{outer - inner}"/>')
    angle = 0.0
    for value, color in parts:
        if value <= 0:
            continue
        sweep = 2 * math.pi * value / total
        if sweep >= 2 * math.pi - 1e-6:
            # Полный круг одной дугой не нарисовать — две половинки
            out.append(f'<circle cx="{cx}" cy="{cy}" r="{(outer + inner) / 2}" fill="none" stroke="{color}" '
                       f'stroke-width="{outer - inner}"/>')
            break
        a2 = angle + sweep
        x1, y1 = _arc(cx, cy, outer, angle)
        x2, y2 = _arc(cx, cy, outer, a2)
        x3, y3 = _arc(cx, cy, inner, a2)
        x4, y4 = _arc(cx, cy, inner, angle)
        large = 1 if sweep > math.pi else 0
        out.append(
            f'<path d="M{x1:.2f},{y1:.2f} A{outer},{outer} 0 {large} 1 {x2:.2f},{y2:.2f} '
            f'L{x3:.2f},{y3:.2f} A{inner},{inner} 0 {large} 0 {x4:.2f},{y4:.2f} Z" fill="{color}" '
            f'stroke="{c["bg"]}" stroke-width="1.5"/>'
        )
        angle = a2
    out.append(f'<text x="{cx}" y="{cy - 4}" text-anchor="middle" font-size="10" fill="{c["hint"]}">Потрачено</text>')
    out.append(f'<text x="{cx}" y="{cy + 14}" text-anchor="middle" font-size="15" font-weight="700" '
               f'fill="{c["text"]}">{escape(total_label)}</text>')
    out.append("</svg>")
    return "".join(out)


def _axis(width: float, height: float, left: float, top: float, bottom: float, top_value: float, c: dict) -> list[str]:
    out = []
    plot_h = height - top - bottom
    for i in range(5):
        y = top + plot_h * i / 4
        value = top_value * (4 - i) / 4
        out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width}" y2="{y:.1f}" stroke="{c["grid"]}" stroke-width="0.8"/>')
        out.append(f'<text x="{left - 6}" y="{y + 3:.1f}" text-anchor="end" font-size="8" fill="{c["hint"]}">{_short(value)}</text>')
    return out


def line_svg(current: list[Decimal], previous: list[Decimal], start: dt.date, c: dict) -> str:
    """Траты нарастающим итогом: этот месяц плана сплошной, прошлый — пунктиром."""
    width, height, left, top, bottom = 500, 140, 44, 8, 22
    days = max(len(previous), len(current), 2)
    top_value = _nice_max(max([float(v) for v in current + previous] or [0]))
    plot_w, plot_h = width - left - 4, height - top - bottom
    out = [f'<svg width="100%" viewBox="0 0 {width} {height}">'] + _axis(width, height, left, top, bottom, top_value, c)

    def points(values: list[Decimal]) -> str:
        return " ".join(
            f"{left + plot_w * i / (days - 1):.1f},{top + plot_h * (1 - float(v) / top_value):.1f}"
            for i, v in enumerate(values)
        )

    if previous:
        out.append(f'<polyline points="{points(previous)}" fill="none" stroke="{c["hint"]}" stroke-width="1.5" '
                   f'stroke-dasharray="4 3"/>')
    if current:
        pts = points(current)
        first_x = left
        last_x = left + plot_w * (len(current) - 1) / (days - 1)
        base = top + plot_h
        out.append(f'<polygon points="{first_x},{base} {pts} {last_x:.1f},{base}" fill="{c["accent"]}" fill-opacity="0.12"/>')
        out.append(f'<polyline points="{pts}" fill="none" stroke="{c["accent"]}" stroke-width="2.2"/>')
    for i in range(0, days, max(days // 6, 1)):
        x = left + plot_w * i / (days - 1)
        anchor = "start" if i == 0 else "end" if i > days - 4 else "middle"
        out.append(f'<text x="{x:.1f}" y="{height - 6}" text-anchor="{anchor}" font-size="8" fill="{c["hint"]}">'
                   f'{_day(start + dt.timedelta(days=i))}</text>')
    out.append("</svg>")
    return "".join(out)


def bars_svg(labels: list[str], values: list[float], c: dict, line: list[float] | None = None,
             highlight: int | None = None, height: int = 140) -> str:
    """Столбики (траты) и, если есть, линия (доходы) поверх."""
    width, left, top, bottom = 500, 44, 8, 22
    top_value = _nice_max(max(values + (line or []) + [0]))
    plot_w, plot_h = width - left - 4, height - top - bottom
    n = max(len(values), 1)
    slot = plot_w / n
    bar = slot * 0.62
    out = [f'<svg width="100%" viewBox="0 0 {width} {height}">'] + _axis(width, height, left, top, bottom, top_value, c)
    for i, v in enumerate(values):
        h = plot_h * v / top_value
        x = left + slot * i + (slot - bar) / 2
        opacity = "1" if highlight is None or i == highlight else "0.45"
        if h > 0:
            out.append(f'<rect x="{x:.1f}" y="{top + plot_h - h:.1f}" width="{bar:.1f}" height="{h:.1f}" rx="2.5" '
                       f'fill="{c["accent"]}" fill-opacity="{opacity}"/>')
        out.append(f'<text x="{x + bar / 2:.1f}" y="{height - 6}" text-anchor="middle" font-size="8" '
                   f'fill="{c["hint"]}">{escape(labels[i])}</text>')
    if line and any(line):
        pts = " ".join(f"{left + slot * i + slot / 2:.1f},{top + plot_h * (1 - v / top_value):.1f}" for i, v in enumerate(line))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{INCOME_COLOR}" stroke-width="2"/>')
        for p in pts.split():
            x, y = p.split(",")
            out.append(f'<circle cx="{x}" cy="{y}" r="2.6" fill="{INCOME_COLOR}"/>')
    out.append("</svg>")
    return "".join(out)


def _progress(percent: float, color: str, c: dict) -> str:
    width = max(min(percent, 100), 0)
    return (f'<div class="bar" style="background:{c["track"]}"><div style="width:{width:.1f}%;'
            f'background:{color}"></div></div>')


# ---------- HTML ----------

CSS = """
@page { size: A4; margin: 14mm 13mm 16mm; background: %(bg)s;
  @bottom-left { content: "SplitBudget · Мои финансы"; font-family: Inter, "DejaVu Sans", sans-serif;
    font-size: 7.5pt; color: %(hint)s; }
  @bottom-right { content: counter(page) " / " counter(pages); font-family: Inter, "DejaVu Sans", sans-serif;
    font-size: 7.5pt; color: %(hint)s; } }
* { box-sizing: border-box; }
body { font-family: Inter, "DejaVu Sans", sans-serif; font-size: 9.5pt; color: %(text)s; margin: 0; line-height: 1.35; }
h1 { font-size: 20pt; margin: 0; letter-spacing: -0.3pt; }
h2 { font-size: 13.5pt; margin: 18pt 0 8pt; letter-spacing: -0.2pt; }
h2.page { page-break-before: always; margin-top: 0; }
h3 { font-size: 10.5pt; margin: 0 0 6pt; }
.hint { color: %(hint)s; }
.small { font-size: 8pt; }
.head { display: flex; align-items: center; margin-bottom: 12pt; }
.head img { width: 34pt; height: 34pt; margin-right: 10pt; }
.head .title { flex: 1; }
.head .meta { margin-left: auto; text-align: right; }
.card { background: %(card)s; border-radius: 10pt; padding: 10pt 12pt; margin-bottom: 9pt; break-inside: avoid; }
.kpis { display: flex; margin-bottom: 9pt; }
.kpis > .kpi + .kpi { margin-left: 7pt; }
.kpi { flex: 1; background: %(card)s; border-radius: 10pt; padding: 9pt 11pt; }
.kpi .v { font-size: 14.5pt; font-weight: 700; margin-top: 2pt; }
.kpi .d { font-size: 8pt; margin-top: 2pt; }
.up { color: %(danger)s; } .down { color: %(success)s; }
.over { color: %(danger)s; } .ok { color: %(success)s; } .warn { color: %(warning)s; }
.donut td { border: none; vertical-align: middle; padding: 0; }
.donut td.ring { width: 150pt; }
.legend td { padding: 2.6pt 4pt; border-bottom: 0.6pt solid %(border)s; }
.dot { display: inline-block; width: 7pt; height: 7pt; border-radius: 50%%; flex: none; }
.grow { flex: 1; }
.num { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
.two { display: flex; } .two > .card { flex: 1; } .two > .card + .card { margin-left: 9pt; }
.chart-legend { font-size: 8pt; color: %(hint)s; margin-top: 3pt; }
.chart-legend span { margin-right: 10pt; }
.swatch { display: inline-block; width: 12pt; height: 0; border-top: 2pt solid; vertical-align: middle; margin-right: 3pt; }
table { width: 100%%; border-collapse: collapse; }
td, th { padding: 3pt 4pt; border-bottom: 0.6pt solid %(border)s; vertical-align: top; }
th { text-align: left; font-weight: 600; color: %(hint)s; font-size: 8pt; }
tr:last-child td { border-bottom: none; }
tr { break-inside: avoid; }
.sub td { color: %(hint)s; font-size: 8.5pt; padding-left: 16pt; }
.total td { font-weight: 700; border-top: 0.8pt solid %(border)s; }
.bar { height: 5pt; border-radius: 3pt; overflow: hidden; margin: 5pt 0 4pt; }
.bar div { height: 100%%; border-radius: 3pt; }
.item-head { display: flex; align-items: baseline; }
.item-head > * { margin-right: 7pt; }
.item-head > *:last-child { margin-right: 0; }
.item-head .t { font-size: 11pt; font-weight: 700; }
.item-figs { font-size: 8.5pt; }
.item-figs > span { display: inline-block; margin-right: 14pt; white-space: nowrap; }
.item-figs b { font-size: 9.5pt; }
.entries { margin-top: 5pt; font-size: 8.5pt; }
.entries td { padding: 2.2pt 4pt; }
.entries td.date { width: 34pt; color: %(hint)s; }
.entries td.cat { color: %(hint)s; }
.item { break-inside: auto; }
.item .entries tr { break-inside: avoid; }
.empty { color: %(hint)s; font-size: 8.5pt; margin-top: 4pt; }
.badge { font-size: 7.5pt; padding: 1pt 5pt; border-radius: 6pt; background: %(track)s; color: %(hint)s; }
"""


def _icon(report: Report, category: str | None) -> str:
    return report.icons.get(category or "", "📦")


def _delta(cur: Decimal, base: Decimal | None, label: str, currency: str) -> str:
    if base is None or not base:
        return f'<div class="d hint">{escape(label)}: нет данных</div>'
    diff = cur - base
    cls = "up" if diff > 0 else "down"
    return (f'<div class="d"><span class="{cls}">{_money(diff, currency, sign=True)} '
            f'({_pct(diff, base):+.0f}%)</span> <span class="hint">{escape(label)}</span></div>')


def _overview(r: Report, c: dict) -> str:
    s, cur = r.stats, r.chat.currency
    spent = s.total
    left = r.income_total - spent
    partial = s.prev_to_date is not None
    base = s.prev_to_date if partial else s.prev_total
    kpis = [
        ("Доходы", _money(r.income_total, cur),
         f'<div class="d hint">{len(r.incomes)} поступл.</div>'),
        ("Потрачено", _money(spent, cur),
         _delta(spent, base, "к тем же дням прошлого" if partial else "к прошлому месяцу", cur)),
        ("Осталось", f'<span class="{"over" if left < 0 else ""}">{_money(left, cur)}</span>',
         f'<div class="d hint">{_pct(left, r.income_total):.0f}% дохода</div>' if r.income_total else ""),
        ("Трат", str(s.count), f'<div class="d hint">в среднем {_money(spent / s.count if s.count else 0, cur)}</div>'),
    ]
    return '<div class="kpis">' + "".join(
        f'<div class="kpi"><div class="hint small">{t}</div><div class="v">{v}</div>{d}</div>' for t, v, d in kpis
    ) + "</div>"


def _colors(stats: StatsOut) -> dict[str, str]:
    return {cat.category: PALETTE[i] if i < len(PALETTE) else OTHER_COLOR for i, cat in enumerate(stats.by_category)}


def _categories(r: Report, c: dict) -> str:
    s, cur = r.stats, r.chat.currency
    if not s.by_category:
        return '<div class="card hint">В этом месяце трат нет.</div>'
    colors = _colors(s)
    head = s.by_category[:DONUT_CATEGORIES]
    rest = sum((x.total for x in s.by_category[DONUT_CATEGORIES:]), ZERO)
    parts = [(float(x.total), colors[x.category]) for x in head] + ([(float(rest), OTHER_COLOR)] if rest else [])
    def row(color: str, name: str, total: Decimal) -> str:
        return (f'<tr><td style="width:10pt"><span class="dot" style="background:{color}"></span></td><td>{name}</td>'
                f'<td class="num hint">{_pct(total, s.total):.0f}%</td><td class="num">{_money(total, cur)}</td></tr>')

    legend = "".join(row(colors[x.category], f"{_icon(r, x.category)} {escape(x.category)}", x.total) for x in head)
    if rest:
        legend += row(OTHER_COLOR, "Остальное", rest)
    return (f'<div class="card"><h3>По категориям</h3><table class="donut"><tr><td class="ring">'
            f'{donut_svg(parts, _money(s.total, cur), c)}</td><td><table class="legend">{legend}</table></td></tr></table></div>')


def _daily(r: Report, c: dict) -> str:
    return (
        f'<div class="card"><h3>Траты по дням, нарастающим итогом</h3>'
        f'{line_svg(r.daily, r.prev_daily, r.start, c)}'
        f'<div class="chart-legend"><span><i class="swatch" style="border-color:{c["accent"]}"></i>этот месяц</span>'
        f'<span><i class="swatch" style="border-color:{c["hint"]};border-top-style:dashed"></i>прошлый месяц</span></div></div>'
    )


def _trend(r: Report, c: dict) -> str:
    months = [m.month for m in r.stats.trend]
    labels = [MONTHS_SHORT[int(m[5:]) - 1] for m in months]
    values = [float(m.total) for m in r.stats.trend]
    income = [float(v) for v in r.income_trend]
    return (
        f'<div class="card"><h3>12 месяцев</h3>{bars_svg(labels, values, c, line=income, highlight=len(values) - 1)}'
        f'<div class="chart-legend"><span><i class="dot" style="background:{c["accent"]}"></i> траты</span>'
        f'<span><i class="swatch" style="border-color:{INCOME_COLOR}"></i>доходы</span>'
        f'<span>по календарным месяцам</span></div></div>'
    )


def _weekdays(r: Report, c: dict) -> str:
    values = [float(v) for v in r.stats.weekdays]
    return f'<div class="card"><h3>По дням недели</h3>{bars_svg(list(WEEKDAYS), values, c, height=110)}</div>'


def _category_table(r: Report) -> str:
    s, cur = r.stats, r.chat.currency
    if not s.by_category:
        return ""
    colors = _colors(s)
    rows = []
    for x in s.by_category:
        prev = x.prev_total or ZERO
        diff = x.total - prev
        delta = (f'<span class="{"up" if diff > 0 else "down"}">{_money(diff, cur, sign=True)}</span>'
                 if prev else '<span class="hint">—</span>')
        rows.append(
            f'<tr><td><span class="dot" style="background:{colors[x.category]}"></span> '
            f'{_icon(r, x.category)} <b>{escape(x.category)}</b></td><td class="num hint">{x.count}</td>'
            f'<td class="num">{_pct(x.total, s.total):.0f}%</td><td class="num"><b>{_money(x.total, cur)}</b></td>'
            f'<td class="num">{delta}</td></tr>'
        )
        subs = [sub for sub in x.subcategories if sub.subcategory]
        if subs:
            for sub in x.subcategories:
                rows.append(
                    f'<tr class="sub"><td>{escape(sub.subcategory or "Без подкатегории")}</td>'
                    f'<td class="num">{sub.count}</td><td></td><td class="num">{_money(sub.total, cur)}</td><td></td></tr>'
                )
    return (
        '<div class="card"><h3>Категории и подкатегории</h3><table>'
        '<tr><th>Категория</th><th class="num">Трат</th><th class="num">Доля</th><th class="num">Сумма</th>'
        '<th class="num">К прошлому</th></tr>' + "".join(rows) + "</table></div>"
    )


def _tops(r: Report) -> str:
    s, cur = r.stats, r.chat.currency
    if not s.top_expenses:
        return ""
    top = "".join(
        f'<tr><td class="hint">{_day(t.expense_date)}</td><td>{_icon(r, t.category)} {escape(t.title)}</td>'
        f'<td class="num">{_money(t.amount, cur)}</td></tr>'
        for t in s.top_expenses
    )
    places = "".join(
        f'<tr><td>{escape(p.title)}</td><td class="num hint">{p.count}×</td><td class="num">{_money(p.total, cur)}</td></tr>'
        for p in s.places
    ) or '<tr><td class="hint">Повторяющихся мест нет</td></tr>'
    return (f'<div class="two"><div class="card"><h3>Крупнейшие траты</h3><table>{top}</table></div>'
            f'<div class="card"><h3>Частые места</h3><table>{places}</table></div></div>')


def _entry_rows(r: Report, entries: list[plan.Entry]) -> str:
    cur = r.chat.currency
    rows = []
    for e in sorted(entries, key=lambda e: e.date):
        if e.source == "deposit":
            what = "🎯 пополнение цели"
        elif e.source == "card":
            what = "💳 платёж по карте"
        else:
            what = f"{_icon(r, e.category)} {escape(e.category or '')}"
            if e.subcategory:
                what += f" › {escape(e.subcategory)}"
        rows.append(
            f'<tr><td class="date">{_day(e.date)}</td><td>{escape(e.title)}</td><td class="cat">{what}</td>'
            f'<td class="num">{_money(e.amount, cur)}</td></tr>'
        )
    return "".join(rows)


def _plan(r: Report, c: dict) -> str:
    p, cur = r.plan, r.chat.currency
    if p is None:
        return ('<h2 class="page">План</h2><div class="card hint">Плана пока нет — его можно составить во вкладке '
                '«План» «Моих финансов».</div>')
    left = sum((i.remaining for i in p.items if i.kind in ("spend", "free")), ZERO)
    out = [
        '<h2 class="page">План</h2>',
        f'<div class="card"><div class="item-figs"><span>Поступило <b>{_money(p.income, cur)}</b></span>'
        f'<span>Распределено <b>{float(p.percent_total):.0f}%</b></span>'
        f'<span>Можно тратить <b class="{"over" if left < 0 else ""}">{_money(left, cur)}</b></span>'
        + (f'<span>Перенесено из прошлых месяцев <b>{_money(p.carried, cur, sign=True)}</b></span>' if p.carried else "")
        + "</div></div>",
    ]
    for item in p.items:
        entries = r.entries.get(item.id, [])
        used = _pct(item.spent, item.allocated) if item.allocated > 0 else (100.0 if item.spent else 0.0)
        if item.remaining < 0:
            color, cls = c["danger"], "over"
        elif used >= 80 and item.kind != "goal":
            color, cls = c["warning"], "warn"
        else:
            color, cls = (c["success"] if item.kind == "goal" else c["accent"]), "ok"
        extra = ""
        if item.kind == "goal" and item.goal_title:
            extra = (f'<span>Цель «{escape(item.goal_title)}»: <b>{_money(item.goal_saved or 0, cur)}</b> из '
                     f'{_money(item.goal_target or 0, cur)}</span>')
        if item.carried:
            extra += f'<span>Перенос <b>{_money(item.carried, cur, sign=True)}</b></span>'
        if item.card_debt:
            extra += f'<span>Долг по картам <b>{_money(item.card_debt, cur)}</b></span>'
        cats = ", ".join(escape(x) for x in item.categories)
        spent_word = "отложено" if item.kind == "goal" else "потрачено"
        out.append(
            f'<div class="card item"><div class="item-head"><span class="t">{escape(item.title)}</span>'
            f'<span class="badge">{float(item.percent):.0f}% · {KIND_LABELS.get(item.kind, "")}</span>'
            f'<span class="grow"></span><span class="num {cls}"><b>{_money(item.remaining, cur)}</b> осталось</span></div>'
            + (f'<div class="hint small">{cats}</div>' if cats else "")
            + _progress(used, color, c)
            + f'<div class="item-figs"><span>Выделено <b>{_money(item.allocated, cur)}</b></span>'
            f'<span>{spent_word.capitalize()} <b>{_money(item.spent, cur)}</b> ({used:.0f}%)</span>{extra}</div>'
            + (f'<table class="entries">{_entry_rows(r, entries)}'
               f'<tr class="total"><td></td><td>Итого · {len(entries)}</td><td></td>'
               f'<td class="num">{_money(sum((e.amount for e in entries), ZERO), cur)}</td></tr></table>'
               if entries else '<div class="empty">Трат в статье не было.</div>')
            + "</div>"
        )
    return "".join(out)


def _cards(r: Report) -> str:
    cur = r.chat.currency
    if not r.cards:
        return ""
    out = ['<h2>Покупки по кредитным картам</h2>',
           '<div class="hint small" style="margin:-4pt 0 8pt">Это не траты месяца: тратой становится погашение '
           'карты, оно уже есть в плане. Здесь — на что ушли деньги с карт.</div>']
    for cp in r.cards:
        total = sum((e.amount for e, _ in cp.purchases), ZERO)
        rows = "".join(
            f'<tr><td class="date">{_day(e.expense_date)}</td><td>{escape(e.title)}</td>'
            f'<td class="cat">{_icon(r, e.category)} {escape(e.category)}'
            f'{" › " + escape(e.subcategory) if e.subcategory else ""}</td>'
            f'<td class="num">{_money(e.amount, cur)}</td>'
            f'<td class="num hint">{"погашено" if not left else "долг " + _money(left, cur)}</td></tr>'
            for e, left in cp.purchases
        )
        out.append(
            f'<div class="card item"><div class="item-head"><span class="t">💳 {escape(cp.card.title)}</span>'
            f'<span class="grow"></span><span>Долг сейчас <b>{_money(cp.debt, cur)}</b></span></div>'
            + (f'<table class="entries">{rows}<tr class="total"><td></td><td>Итого · {len(cp.purchases)}</td><td></td>'
               f'<td class="num">{_money(total, cur)}</td><td></td></tr></table>'
               if cp.purchases else '<div class="empty">Покупок картой в этом месяце не было.</div>')
            + "</div>"
        )
    return "".join(out)


def _loans_goals(r: Report, c: dict) -> str:
    cur = r.chat.currency
    blocks = []
    if r.loans:
        rows = "".join(
            f'<tr><td>🏦 {escape(t)}</td><td class="num hint">{_money(pay, cur)}/мес</td>'
            f'<td class="num"><b>{_money(left, cur) if left is not None else "—"}</b></td></tr>'
            for t, left, pay in r.loans
        )
        blocks.append(f'<div class="card"><h3>Кредиты</h3><table><tr><th>Кредит</th><th class="num">Платёж</th>'
                      f'<th class="num">Остаток долга</th></tr>{rows}</table></div>')
    if r.goals:
        rows = "".join(
            f'<div style="margin-bottom:6pt"><div class="item-head"><span>🎯 {escape(t)}</span><span class="grow"></span>'
            f'<span class="num"><b>{_money(saved, cur)}</b> <span class="hint">из {_money(target, cur)}</span></span></div>'
            f'{_progress(_pct(saved, target), c["success"], c)}</div>'
            for t, saved, target in r.goals
        )
        blocks.append(f'<div class="card"><h3>Цели</h3>{rows}</div>')
    if not blocks:
        return ""
    return "<h2>Кредиты и цели</h2>" + "".join(blocks)


def _incomes(r: Report) -> str:
    if not r.incomes:
        return ""
    cur = r.chat.currency
    rows = "".join(
        f'<tr><td class="date">{_day(i.income_date)}</td><td>{escape(i.title)}</td>'
        f'<td class="num">{_money(i.amount, cur)}</td></tr>'
        for i in r.incomes
    )
    return f'<div class="card"><h3>Поступления</h3><table class="entries">{rows}</table></div>'


def _logo() -> str:
    try:
        return LOGO.read_text(encoding="utf-8")
    except OSError:
        return ""


def render_html(r: Report, theme: str = "light") -> str:
    c = COLORS[theme if theme in THEMES else "light"]
    logo_svg = _logo()
    generated = f"Сформировано {_date(r.today)} {r.today.year}"
    logo = f'<img src="data:image/svg+xml;base64,{base64.b64encode(logo_svg.encode()).decode()}">' if logo_svg else ""
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        f"<title>SplitBudget · {escape(period_label(r.start, r.end))}</title>"
        f"<style>{CSS % c}</style></head><body>"
        f'<div class="head">{logo}<div class="title"><h1>Мои финансы</h1><div class="hint">{escape(period_label(r.start, r.end))}'
        f"</div></div><div class=\"meta hint small\">SplitBudget<br>{generated}</div></div>"
        + _overview(r, c)
        + _categories(r, c)
        + _daily(r, c)
        + _trend(r, c)
        + '<h2 class="page">Подробнее о тратах</h2>'
        + _category_table(r)
        + _tops(r)
        + _incomes(r)
        + _weekdays(r, c)
        + _plan(r, c)
        + _cards(r)
        + _loans_goals(r, c)
        + "</body></html>"
    )


def _write_pdf(html: str) -> bytes:
    from weasyprint import HTML  # тяжёлый импорт — только когда отчёт действительно нужен

    return HTML(string=html).write_pdf()


async def to_pdf(html: str) -> bytes:
    """WeasyPrint работает синхронно и небыстро — в отдельном потоке, чтобы не держать бота и API."""
    return await asyncio.to_thread(_write_pdf, html)
