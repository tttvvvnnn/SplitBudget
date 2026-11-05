"""Предпросмотр и импорт выписки в «Мои финансы».

Что берём (решения 2026-11-05):
- покупки — в траты, с категорией (выученное → словарь → ИИ); неопознанное по умолчанию не берём;
  возврат покупки вычитается из неё;
- поступления — в доходы: зарплата и банковские переводы, кэшбэк, проценты отмечены сразу,
  входящие СБП (могут быть и своими переводами из другого банка) — нет;
- переводы между договорами, Инвесткопилка, свои переводы, наличные — не берём.

Совпадения с уже внесённым. Трату, которую ты уже записал руками (в «Моих финансах» или
оплатил в семейном чате), ищем по сумме с разбросом, дате и названию или категории и
показываем парой «из выписки ⇄ твоя»: оставить твою, взять из выписки (твою трату обновляем
суммой, датой и названием из выписки) или оставить обе. С доходами так же.

Накладки, которые закрываем при импорте:
- обязательный платёж за месяц (подписка, аренда) ещё «ждёт оплаты», а покупка за него есть
  в выписке — отмечаем платёж оплаченным этой тратой, иначе он висел бы в «Платежах»;
- регулярный доход (зарплата 10-го) в месяце ещё «ожидается», а в выписке он пришёл —
  привязываем доход к источнику, иначе в сводке он считался бы и полученным, и ожидаемым.
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
from app.shared.models import (
    Chat,
    Expense,
    ExpenseShare,
    Income,
    IncomeSource,
    Member,
    RecurringExpense,
)
from app.shared.obligations import day_in_month, ensure_payment, month_of

REFUND_DAYS = 60  # возврат ищем среди покупок за столько дней до него
MATCH_DAYS = 3  # банк проводит покупку позже, а вручную её вносят днём покупки
INCOME_MATCH_DAYS = 5
SOURCE_DAYS = 7  # зарплата «10-го» приходит и 8-го, и 12-го
PAYMENT_DAYS = 5

# Насколько совпадение похоже на ту же покупку. Чем меньше, тем увереннее.
EXACT = "exact"  # та же сумма, ±2 дня, и название или категория совпали
SAME_SUM = "same_sum"  # та же сумма, ±2 дня, а название и категория — нет
LIKELY = "likely"  # сумма с разбросом (округлил при вводе), похожее название или категория
MAYBE = "maybe"  # почти та же сумма в тот же день, больше ничего общего
_RANK = {EXACT: 0, SAME_SUM: 1, LIKELY: 2, MAYBE: 3}
# По умолчанию: совпадение — это та же покупка, оставляем твою; «может быть» — берём обе
DEFAULT_CHOICE = {EXACT: "mine", SAME_SUM: "mine", LIKELY: "mine", MAYBE: "both"}

ZERO = Decimal("0")


@dataclass
class Match:
    level: str
    expense: Expense | None = None
    income: Income | None = None
    space: str | None = None  # название семейного чата, если трата оттуда
    editable: bool = True  # можно «взять из выписки»: только свою трату «Моих финансов»


@dataclass
class Purchase:
    date: dt.date
    amount: Decimal  # положительная
    title: str
    description: str
    category: str | None = None
    match: Match | None = None


@dataclass
class Group:
    title: str
    category: str | None  # None — не опознали
    subcategory: str | None
    source: str | None  # learned | dictionary | ai
    purchases: list[Purchase] = field(default_factory=list)


@dataclass
class IncomeRow:
    date: dt.date
    amount: Decimal
    title: str
    description: str
    checked: bool  # зарплата, кэшбэк — да; входящий СБП — нет
    source: IncomeSource | None = None
    match: Match | None = None


@dataclass
class Skipped:
    kind: str
    count: int
    total: Decimal  # со знаком, как в выписке


@dataclass
class Preview:
    groups: list[Group]
    incomes: list[IncomeRow]
    skipped: list[Skipped]


def _skip(skipped: dict[str, Skipped], op: Operation) -> None:
    s = skipped.setdefault(op.kind, Skipped(op.kind, 0, ZERO))
    s.count += 1
    s.total += op.amount


def split(statement: Statement) -> tuple[list[Purchase], list[IncomeRow], list[Skipped]]:
    """Покупки (за вычетом возвратов), поступления и сводка того, что пропускаем."""
    purchases: list[Purchase] = []
    incomes: list[IncomeRow] = []
    refunds: list[Operation] = []
    skipped: dict[str, Skipped] = {}
    for op in statement.operations:
        if op.kind == base.EXPENSE:
            purchases.append(Purchase(op.date, -op.amount, op.title, op.description))
        elif op.kind == base.REFUND:
            refunds.append(op)
        elif op.kind == base.INCOME and op.amount > 0:
            incomes.append(IncomeRow(op.date, op.amount, op.title, op.description, checked=True))
        elif op.kind == base.TRANSFER and op.amount > 0:
            incomes.append(IncomeRow(op.date, op.amount, "Перевод", op.description, checked=False))
        else:
            _skip(skipped, op)
    for refund in refunds:
        purchase = _refunded(purchases, refund)
        if purchase is None:
            _skip(skipped, refund)
            continue
        purchase.amount -= refund.amount
        if purchase.amount <= 0:
            purchases.remove(purchase)
    return purchases, incomes, list(skipped.values())


def purchases_of(statement: Statement) -> tuple[list[Purchase], list[Skipped]]:
    purchases, incomes, skipped = split(statement)
    return purchases, skipped


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


def similar_titles(a: str, b: str) -> bool:
    """«Пятерочка» ~ «пятёрочка у дома», «ЦППК» ~ «цппк электричка»: общее слово (по первым 4 буквам)."""
    words_a = {w[:4] for w in normalize(a).split() if len(w) >= 3}
    words_b = {w[:4] for w in normalize(b).split() if len(w) >= 3}
    return bool(words_a & words_b)


def amount_close(a: Decimal, b: Decimal) -> bool:
    """Разброс для «округлил при вводе»: 1 625,31 → 1 600 или 1 650. 10%, но не меньше 50 ₽."""
    return abs(a - b) <= max(a * Decimal("0.1"), Decimal("50"))


def match_level(p_amount: Decimal, p_date: dt.date, p_title: str, p_category: str | None,
                e_amount: Decimal, e_date: dt.date, e_title: str, e_category: str | None) -> str | None:
    days = abs((e_date - p_date).days)
    if days > MATCH_DAYS:
        return None
    alike = similar_titles(p_title, e_title) or (
        p_category is not None and p_category != OTHER and p_category == e_category
    )
    if e_amount == p_amount and days <= 2:
        return EXACT if alike else SAME_SUM
    if alike and amount_close(p_amount, e_amount):
        return LIKELY
    if days <= 1 and abs(e_amount - p_amount) <= p_amount * Decimal("0.02"):
        return MAYBE
    return None


def _assign(pairs: list[tuple[tuple, object, object, str]]) -> None:
    """Жадно, от самых уверенных пар: одна запись закрывает одну операцию (четыре электрички
    по 178 ₽ за день и одна внесённая вручную — одно совпадение и три новые траты)."""
    used_ops: set[int] = set()
    used_rows: set[int] = set()
    for _, op, row, level in sorted(pairs, key=lambda x: x[0]):
        if id(op) in used_ops or id(row) in used_rows:
            continue
        used_ops.add(id(op))
        used_rows.add(id(row))
        op.match = Match(level, **({"expense": row} if isinstance(row, Expense) else {"income": row}))


async def match_expenses(session: AsyncSession, chat: Chat, member: Member, purchases: list[Purchase]) -> None:
    """Совпадения покупок с тратами, которые ты оплатил (в любом пространстве). Покупки по
    кредитке и погашения карт не трогаем: в дебетовой выписке их нет."""
    if not purchases or member.tg_user_id is None:
        return
    start = min(p.date for p in purchases) - dt.timedelta(days=MATCH_DAYS)
    end = max(p.date for p in purchases) + dt.timedelta(days=MATCH_DAYS)
    rows = (
        await session.execute(
            select(Expense, Chat)
            .join(Member, Member.id == Expense.payer_member_id)
            .join(Chat, Chat.id == Expense.chat_id)
            .where(
                Member.tg_user_id == member.tg_user_id,
                Expense.expense_date >= start,
                Expense.expense_date <= end,
                Expense.card_id.is_(None),
                Expense.repays_card_id.is_(None),
            )
        )
    ).all()
    chats = {e.id: c for e, c in rows}
    pairs = []
    for p in purchases:
        for expense, _ in rows:
            level = match_level(p.amount, p.date, p.title, p.category,
                                expense.amount, expense.expense_date, expense.title, expense.category)
            if level:
                days = abs((expense.expense_date - p.date).days)
                pairs.append(((_RANK[level], days, abs(expense.amount - p.amount)), p, expense, level))
    _assign(pairs)
    for p in purchases:
        if p.match and p.match.expense is not None:
            space = chats[p.match.expense.id]
            p.match.space = None if space.is_personal else space.title
            p.match.editable = space.id == chat.id


async def match_incomes(session: AsyncSession, chat: Chat, incomes: list[IncomeRow]) -> None:
    """Поступления, уже записанные в доходы (та же сумма или с разбросом, ±5 дней), и к какому
    регулярному доходу привязать новое."""
    if not incomes:
        return
    start = min(i.date for i in incomes) - dt.timedelta(days=SOURCE_DAYS + 31)
    end = max(i.date for i in incomes) + dt.timedelta(days=SOURCE_DAYS + 31)
    existing = list(
        (
            await session.execute(
                select(Income).where(Income.chat_id == chat.id, Income.income_date >= start, Income.income_date <= end)
            )
        ).scalars().all()
    )
    pairs = []
    for row in incomes:
        for income in existing:
            days = abs((income.income_date - row.date).days)
            if days > INCOME_MATCH_DAYS:
                continue
            if income.amount == row.amount:
                level = EXACT
            elif abs(income.amount - row.amount) <= row.amount * Decimal("0.02") or (
                amount_close(row.amount, income.amount)
                and (similar_titles(row.title, income.title) or income.source_id is not None)
            ):
                # Поступлений мало и они крупные: 29 000 руками и 29 000,33 в выписке — одно и то же
                level = LIKELY
            else:
                continue
            pairs.append(((_RANK[level], days, abs(income.amount - row.amount)), row, income, level))
    _assign(pairs)

    # Регулярный доход: в месяце ещё не пришёл, а в выписке есть поступление около его дня
    # на похожую сумму — это он (зарплата 50 000 «10-го» и пришедшие 9-го 49 870)
    sources = list(
        (await session.execute(select(IncomeSource).where(IncomeSource.chat_id == chat.id))).scalars().all()
    )
    taken = {(i.source_id, month_of(i.income_date)) for i in existing if i.source_id}
    months = sorted({month_of(row.date) for row in incomes})
    for source in sources:
        for month in months:  # регулярный доход — один раз в месяц
            if (source.id, month) in taken:
                continue
            due = day_in_month(source.day_of_month, month)
            best: tuple[tuple, IncomeRow] | None = None
            for row in incomes:
                if row.match is not None or row.source is not None or not row.checked:
                    continue
                days = abs((due - row.date).days)
                close = abs(row.amount - source.amount) <= source.amount * Decimal("0.25")
                if days > SOURCE_DAYS or not (close or similar_titles(row.title, source.title)):
                    continue
                rank = (days, abs(row.amount - source.amount))
                if best is None or rank < best[0]:
                    best = (rank, row)
            if best is not None:
                row = best[1]
                row.source = source
                row.title = source.title
                taken.add((source.id, month))


async def categorize(session: AsyncSession, chat: Chat, purchases: list[Purchase]) -> list[Group]:
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
    for group in groups.values():
        for p in group.purchases:
            p.category = group.category
    return sorted(groups.values(), key=lambda g: -sum((p.amount for p in g.purchases), ZERO))


async def build(session: AsyncSession, chat: Chat, member: Member, statement: Statement) -> Preview:
    purchases, incomes, skipped = split(statement)
    groups = await categorize(session, chat, purchases)
    await match_expenses(session, chat, member, purchases)
    await match_incomes(session, chat, incomes)
    return Preview(groups, incomes, skipped)


# --- Импорт -----------------------------------------------------------------------------


@dataclass
class NewExpense:
    date: dt.date
    amount: Decimal
    title: str
    category: str
    subcategory: str | None


@dataclass
class ExpenseUpdate:
    """«Взять из выписки»: твоя трата получает сумму, дату и название из выписки."""
    expense_id: int
    date: dt.date
    amount: Decimal
    title: str
    category: str | None = None
    subcategory: str | None = None


@dataclass
class NewIncome:
    date: dt.date
    amount: Decimal
    title: str
    source_id: int | None = None


@dataclass
class IncomeUpdate:
    income_id: int
    date: dt.date
    amount: Decimal


@dataclass
class Result:
    expenses: int = 0
    updated: int = 0
    incomes: int = 0
    payments: int = 0  # обязательные платежи, отмеченные оплаченными


def _check_category(subs_of: dict[str, set[str]], category: str, subcategory: str | None) -> None:
    if category not in subs_of or (subcategory and subcategory not in subs_of[category]):
        raise ValueError(f"Нет категории «{category}»")


async def apply(
    session: AsyncSession,
    chat: Chat,
    member: Member,
    rows: list[NewExpense],
    updates: list[ExpenseUpdate] = (),
    incomes: list[NewIncome] = (),
    income_updates: list[IncomeUpdate] = (),
) -> Result:
    """Записать выбранное (без уведомлений и алертов бюджета: это прошлое) и запомнить
    категории названий. ValueError — что-то не так во входных данных, ничего не записываем."""
    tree = ai_category._visible(await cc.tree_for(session, chat))
    subs_of = {name: set(subs) for name, _, subs in tree}
    result = Result()
    learned: set[str] = set()
    created: list[Expense] = []

    for row in rows:
        _check_category(subs_of, row.category, row.subcategory)
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
        created.append(expense)
        key = normalize(row.title)
        if key not in learned:
            learned.add(key)
            await learn_category(session, chat.id, row.title, row.category, row.subcategory or None)
        result.expenses += 1

    for upd in updates:
        expense = await session.get(Expense, upd.expense_id)
        if expense is None or expense.chat_id != chat.id or expense.payer_member_id != member.id:
            raise ValueError("Трату нельзя обновить: она не твоя или не из «Моих финансов»")
        if upd.category:
            _check_category(subs_of, upd.category, upd.subcategory)
            expense.category, expense.subcategory = upd.category, upd.subcategory or None
        expense.title = upd.title.strip()[:255]
        expense.amount = upd.amount
        expense.expense_date = upd.date
        shares = (await session.execute(select(ExpenseShare).where(ExpenseShare.expense_id == expense.id))).scalars().all()
        for share in shares:
            await session.delete(share)
        await session.flush()
        for member_id, share in build_equal_shares(upd.amount, [member.id]):
            session.add(ExpenseShare(expense_id=expense.id, member_id=member_id, amount=share))
        result.updated += 1

    for inc in incomes:
        source_id = None
        if inc.source_id is not None:
            source = await session.get(IncomeSource, inc.source_id)
            if source is None or source.chat_id != chat.id:
                raise ValueError("Нет такого регулярного дохода")
            source_id = source.id
        session.add(Income(chat_id=chat.id, title=inc.title.strip()[:255] or "Доход", amount=inc.amount,
                           income_date=inc.date, source_id=source_id))
        result.incomes += 1

    for upd in income_updates:
        income = await session.get(Income, upd.income_id)
        if income is None or income.chat_id != chat.id:
            raise ValueError("Нет такого дохода")
        income.amount = upd.amount
        income.income_date = upd.date
        result.updated += 1

    await session.flush()
    result.payments = await _close_payments(session, chat, created)
    await session.flush()
    return result


async def _close_payments(session: AsyncSession, chat: Chat, created: list[Expense]) -> int:
    """Обязательный платёж «Моих финансов» за месяц ещё ждёт оплаты, а среди новых трат есть
    он (похожее название или дата рядом, та же сумма) — отмечаем оплаченным этой тратой."""
    if not created:
        return 0
    templates = list(
        (
            await session.execute(
                select(RecurringExpense).where(
                    RecurringExpense.chat_id == chat.id,
                    RecurringExpense.is_active.is_(True),
                    RecurringExpense.kind.in_(("rent", "subscription", "other")),
                )
            )
        ).scalars().all()
    )
    closed = 0
    used: set[int] = set()
    for recurring in templates:
        for month in sorted({month_of(e.expense_date) for e in created}):
            payment = await ensure_payment(session, recurring, month)
            if payment is None or payment.status != "pending":
                continue
            for expense in created:
                if expense.id in used or month_of(expense.expense_date) != month:
                    continue
                same_sum = not recurring.amount or abs(expense.amount - recurring.amount) <= recurring.amount * Decimal("0.05")
                near = abs((expense.expense_date - payment.due_date).days) <= PAYMENT_DAYS
                if same_sum and (similar_titles(expense.title, recurring.title) or (near and recurring.amount)):
                    payment.status = "paid"
                    payment.amount = expense.amount
                    payment.expense_id = expense.id
                    payment.asked = True
                    payment.paid_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
                    payment.reminders_sent = "7,3,1"
                    expense.recurring_id = recurring.id
                    used.add(expense.id)
                    closed += 1
                    break
    return closed
