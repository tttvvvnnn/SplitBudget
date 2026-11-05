"""Предпросмотр и импорт выписки в «Мои финансы».

Берём только покупки — то, что можно опознать и отнести к категории (решение 2026-11-05):
переводы, Инвесткопилка, наличные, поступления не импортируются. Возврат покупки вычитается
из неё. Покупки, которые уже внесены вручную (та же сумма, дата ±2 дня, плательщик — ты,
в любом пространстве), — дубли и не импортируются. Категория: выученное → словарь → ИИ;
что не опознали — по умолчанию не импортируем.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared import ai_category
from app.shared import custom_categories as cc
from app.shared.autocategory import learn_category, normalize, suggest_category
from app.shared.bank_import import base
from app.shared.bank_import.base import Operation, Statement
from app.shared.categories import OTHER
from app.shared.crud import build_equal_shares
from app.shared.models import Chat, Expense, ExpenseShare, Member

DUPLICATE_DAYS = 2  # банк проводит покупку позже, а вручную её вносят днём покупки
REFUND_DAYS = 60  # возврат ищем среди покупок за столько дней до него


@dataclass
class Purchase:
    date: dt.date
    amount: Decimal  # положительная
    title: str
    description: str
    duplicate: Expense | None = None


@dataclass
class Group:
    title: str
    category: str | None  # None — не опознали
    subcategory: str | None
    source: str | None  # learned | dictionary | ai
    purchases: list[Purchase] = field(default_factory=list)


@dataclass
class Skipped:
    kind: str
    count: int
    total: Decimal  # со знаком, как в выписке


def purchases_of(statement: Statement) -> tuple[list[Purchase], list[Skipped]]:
    """Покупки за вычетом возвратов и сводка того, что пропускаем."""
    purchases: list[Purchase] = []
    refunds: list[Operation] = []
    skipped: dict[str, Skipped] = {}
    for op in statement.operations:
        if op.kind == base.EXPENSE:
            purchases.append(Purchase(op.date, -op.amount, op.title, op.description))
        elif op.kind == base.REFUND:
            refunds.append(op)
        else:
            s = skipped.setdefault(op.kind, Skipped(op.kind, 0, Decimal("0")))
            s.count += 1
            s.total += op.amount
    for refund in refunds:
        purchase = _refunded(purchases, refund)
        if purchase is None:
            s = skipped.setdefault(base.REFUND, Skipped(base.REFUND, 0, Decimal("0")))
            s.count += 1
            s.total += refund.amount
            continue
        purchase.amount -= refund.amount
        if purchase.amount <= 0:
            purchases.remove(purchase)
    return purchases, list(skipped.values())


def _refunded(purchases: list[Purchase], refund: Operation) -> Purchase | None:
    """Покупка, которую вернули: то же название (если банк его дал) и сумма не меньше
    возврата; при равенстве суммы — точнее. Ближайшая по дате, не позже возврата."""
    key = normalize(refund.title) if refund.title != "Возврат покупки" else None
    best: tuple[tuple, Purchase] | None = None
    for p in purchases:
        days = (refund.date - p.date).days
        if not 0 <= days <= REFUND_DAYS or p.amount < refund.amount:
            continue
        if key is not None and normalize(p.title) != key:
            continue
        if key is None and p.amount != refund.amount:
            continue  # без названия — только полный возврат той же суммы
        rank = (p.amount != refund.amount, days)
        if best is None or rank < best[0]:
            best = (rank, p)
    return best[1] if best else None


async def mark_duplicates(session: AsyncSession, member: Member, purchases: list[Purchase]) -> None:
    """Покупка уже внесена, если есть трата, которую оплатил ты (в любом пространстве), с той же
    суммой и датой ±DUPLICATE_DAYS. Одна трата закрывает одну покупку: четыре электрички по 178 ₽
    за день и одна внесённая вручную — три новые."""
    if not purchases or member.tg_user_id is None:
        return
    start = min(p.date for p in purchases) - dt.timedelta(days=DUPLICATE_DAYS)
    end = max(p.date for p in purchases) + dt.timedelta(days=DUPLICATE_DAYS)
    result = await session.execute(
        select(Expense)
        .join(Member, Member.id == Expense.payer_member_id)
        .where(
            Member.tg_user_id == member.tg_user_id,
            Expense.expense_date >= start,
            Expense.expense_date <= end,
        )
        .order_by(Expense.expense_date, Expense.id)
    )
    free = list(result.scalars().all())
    # Сначала точные по дате, потом соседние дни — чтобы сосед не забрал чужую пару
    for distance in range(DUPLICATE_DAYS + 1):
        for p in purchases:
            if p.duplicate is not None:
                continue
            for expense in free:
                if expense.amount == p.amount and abs((expense.expense_date - p.date).days) == distance:
                    p.duplicate = expense
                    free.remove(expense)
                    break


async def group_with_categories(session: AsyncSession, chat: Chat, purchases: list[Purchase]) -> list[Group]:
    """Покупки по названию с категорией. ИИ — одной пачкой на то, что не нашлось в правилах и словаре;
    предложения ИИ создать новую категорию не используем: импорт не должен плодить категории."""
    groups: dict[str, Group] = {}
    for p in purchases:
        key = normalize(p.title) or p.title
        group = groups.get(key)
        if group is None:
            group = groups[key] = Group(p.title, None, None, None)
        group.purchases.append(p)
    unknown: list[Group] = []
    for group in groups.values():
        suggestion = await suggest_category(session, chat.id, group.title)
        if suggestion is not None and suggestion.category and suggestion.category != OTHER:
            group.category, group.subcategory, group.source = suggestion.category, suggestion.subcategory, suggestion.source
        else:
            unknown.append(group)
    if unknown:
        answers = await ai_category.ai_guess_many(session, chat, [g.title for g in unknown])
        for group in unknown:
            answer = answers.get(normalize(group.title)[:255])
            if answer is None or answer.action not in ("existing", "new_subcategory") or not answer.category:
                continue
            if answer.category == OTHER and not answer.subcategory:
                continue
            group.category = answer.category
            group.subcategory = answer.subcategory if answer.action == "existing" else None
            group.source = "ai"
    return sorted(groups.values(), key=lambda g: -sum((p.amount for p in g.purchases), Decimal("0")))


@dataclass
class NewExpense:
    date: dt.date
    amount: Decimal
    title: str
    category: str
    subcategory: str | None


async def apply(session: AsyncSession, chat: Chat, member: Member, rows: list[NewExpense]) -> int:
    """Записать покупки тратами «Моих финансов» (без уведомлений и алертов бюджета: это прошлое)
    и запомнить категории названий. → сколько трат добавили."""
    tree = ai_category._visible(await cc.tree_for(session, chat))
    subs_of = {name: set(subs) for name, _, subs in tree}
    learned: set[str] = set()
    for row in rows:
        if row.category not in subs_of or (row.subcategory and row.subcategory not in subs_of[row.category]):
            raise ValueError(f"Нет категории «{row.category}»")
        if row.amount <= 0:
            raise ValueError("Сумма должна быть больше нуля")
        expense = Expense(
            chat_id=chat.id,
            title=row.title.strip()[:255],
            amount=row.amount,
            category=row.category,
            subcategory=row.subcategory or None,
            expense_date=row.date,
            payer_member_id=member.id,
            split_type="equal",
            created_by_member_id=member.id,
        )
        session.add(expense)
        await session.flush()
        for member_id, share in build_equal_shares(row.amount, [member.id]):
            session.add(ExpenseShare(expense_id=expense.id, member_id=member_id, amount=share))
        key = normalize(row.title)
        if key not in learned:
            learned.add(key)
            await learn_category(session, chat.id, row.title, row.category, row.subcategory or None)
    await session.flush()
    return len(rows)
