"""Итоги месяца: 1-го числа бот присылает короткий отчёт за прошлый месяц — в личку по
«Моим финансам» и в каждый семейный чат. Командой /summary — то же за текущий месяц.

В отчёте: сколько потрачено и сравнение с прошлым месяцем, топ категорий, что выросло
сильнее всего, лимиты, обязательные платежи; в личном — доходы и сколько осталось,
в семейном — кто сколько заплатил.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from html import escape

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.budgets import budget_label, budgets_status, month_range, spending
from app.shared.categories import CATEGORY_TREE
from app.shared.models import Chat, Expense, Income, Member, MonthlyReport
from app.shared.obligations import fmt_amount, month_of, obligations

ZERO = Decimal("0")
MONTHS = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)
TOP = 3
# Рост категории попадает в отчёт, если он заметный, а не на пару сотен рублей
GROWTH_MIN_SHARE = Decimal("0.05")


def prev_month(month: str) -> str:
    start, _ = month_range(month)
    return month_of(start - dt.timedelta(days=1))


def _icon(category: str) -> str:
    return next((icon for name, icon, _ in CATEGORY_TREE if name == category), "🏷️")


def _by_category(totals: dict[tuple[str, str | None], Decimal]) -> dict[str, Decimal]:
    return {cat: amount for (cat, sub), amount in totals.items() if cat and sub is None}


def _percent_change(now: Decimal, before: Decimal) -> str:
    if before <= 0:
        return ""
    change = (now - before) * 100 / before
    arrow = "↑" if change > 0 else "↓"
    return f" ({arrow}{abs(change):.0f}% к прошлому месяцу)"


async def build_report(session: AsyncSession, chat: Chat, month: str, partial: bool = False) -> str | None:
    """Текст отчёта за месяц или None, если в месяце ничего не было. partial — месяц ещё идёт
    (команда /summary): тогда без сравнения «к прошлому месяцу», оно было бы нечестным."""
    money = lambda amount: fmt_amount(amount, chat.currency)  # noqa: E731
    now = await spending(session, chat, month)
    total = now.get(("", None), ZERO)

    start, end = month_range(month)
    incomes = ZERO
    if chat.is_personal:
        result = await session.execute(
            select(func.coalesce(func.sum(Income.amount), 0)).where(
                Income.chat_id == chat.id, Income.income_date >= start, Income.income_date < end
            )
        )
        incomes = Decimal(result.scalar_one())
    if total <= 0 and incomes <= 0:
        return None

    year, mon = (int(p) for p in month.split("-"))
    title = MONTHS[mon - 1].capitalize() + (f" {year}" if year != dt.date.today().year else "")
    where = "Мои финансы · " if chat.is_personal else ""
    head = f"📊 <b>{where}{'Пока за' if partial else 'Итоги:'} {title.lower() if partial else title}</b>"
    lines = [head, ""]

    before = await spending(session, chat, prev_month(month))
    before_total = before.get(("", None), ZERO)
    lines.append(f"Потрачено: <b>{money(total)}</b>{'' if partial else _percent_change(total, before_total)}")
    if chat.is_personal and incomes > 0:
        left = incomes - total
        lines.append(f"Доходы: {money(incomes)} · {'осталось' if left >= 0 else 'перерасход'} {money(abs(left))}")

    cats = _by_category(now)
    if cats:
        top = sorted(cats.items(), key=lambda kv: kv[1], reverse=True)[:TOP]
        lines.append("")
        lines.append("Больше всего:")
        lines.extend(f"{_icon(cat)} {escape(cat)} — {money(amount)}" for cat, amount in top)

    if not partial and total > 0:
        prev_cats = _by_category(before)
        growth = [
            (cat, amount - prev_cats.get(cat, ZERO))
            for cat, amount in cats.items()
            if amount - prev_cats.get(cat, ZERO) >= total * GROWTH_MIN_SHARE and prev_cats.get(cat, ZERO) > 0
        ]
        if growth:
            cat, diff = max(growth, key=lambda x: x[1])
            lines.append(f"\n📈 Сильнее всего выросло: {_icon(cat)} {escape(cat)} +{money(diff)}")

    statuses = [s for s in await budgets_status(session, chat, month) if s.budget.amount > 0]
    if statuses:
        over = [s for s in statuses if s.spent > s.budget.amount]
        lines.append("")
        if over:
            lines.append(
                "🚨 Превышены лимиты: "
                + ", ".join(f"«{escape(budget_label(s.budget))}» ({s.spent * 100 / s.budget.amount:.0f}%)" for s in over)
            )
        lines.append(f"🎯 В рамках лимитов: {len(statuses) - len(over)} из {len(statuses)}")

    items = [o for o in await obligations(session, chat, month) if o.counts]
    if items:
        paid = sum((o.share for o in items if o.payment.status == "paid"), ZERO)
        planned = sum((o.share for o in items if o.payment.status != "skipped"), ZERO)
        lines.append(f"📌 Обязательные платежи: оплачено {money(paid)} из {money(planned)}")

    if not chat.is_personal:
        result = await session.execute(
            select(Member.full_name, func.sum(Expense.amount))
            .join(Member, Member.id == Expense.payer_member_id)
            .where(Expense.chat_id == chat.id, Expense.expense_date >= start, Expense.expense_date < end)
            .group_by(Member.id)
            .order_by(func.sum(Expense.amount).desc())
        )
        payers = result.all()
        if len(payers) > 1:
            lines.append("")
            lines.append("Платили:")
            lines.extend(
                f"• {escape(name)} — {money(Decimal(amount))} ({Decimal(amount) * 100 / total:.0f}%)"
                for name, amount in payers
            )
    return "\n".join(lines)


async def due_reports(session: AsyncSession, today: dt.date) -> list[tuple[int, str]]:
    """1-го числа — отчёты за прошлый месяц для всех чатов, где были траты или доходы.
    Отправленное помечается в MonthlyReport (вызывающий коммитит)."""
    if today.day != 1:
        return []
    month = prev_month(month_of(today))
    sent = set(
        (await session.execute(select(MonthlyReport.chat_id).where(MonthlyReport.month == month))).scalars().all()
    )
    out = []
    for chat in (await session.execute(select(Chat))).scalars().all():
        if chat.id in sent:
            continue
        text = await build_report(session, chat, month)
        session.add(MonthlyReport(chat_id=chat.id, month=month))
        if text:
            out.append((chat.id, text))
    await session.flush()
    return out
