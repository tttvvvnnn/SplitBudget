"""Быстрый ввод трат текстом в личке с ботом: «кофе 250», «винлаб 1 800», «такси 350 вчера».
Плюс перед суммой — доход: «+120000 зарплата», «аванс +40к».

Каждая строка сообщения — отдельная трата в «Моих финансах». Категория подбирается так же,
как в форме (app/shared/autocategory.py): выученное по прошлым тратам или словарь магазинов.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.autocategory import learn_category, suggest_category
from app.shared.categories import OTHER
from app.shared.crud import build_equal_shares
from app.shared.models import Chat, Expense, ExpenseShare, Member

# Сумма: «250», «1 800», «1800.50», «1 800,5», «2к», «1.5k», с необязательной валютой после
_AMOUNT = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:[  ]\d{3})+|\d+)(?:[.,](\d{1,2}))?\s*(к|k|тыс\.?)?\s*(?:₽|р\.?|руб\.?|rub)?(?!\w)",
    re.IGNORECASE,
)
_DAYS_AGO = {"сегодня": 0, "вчера": 1, "позавчера": 2}
MAX_LINES = 20
GROUP_MAX_LINES = 5
GROUP_MAX_WORDS = 6
GROUP_MIN_AMOUNT = 10
# «приду в 19», «встречаемся к 18» — время, а не трата
_PREPOSITIONS = {"в", "во", "к", "ко", "на", "до", "после", "около", "с", "со", "по", "через"}


@dataclass
class ParsedExpense:
    title: str
    amount: Decimal
    date: dt.date
    is_income: bool = False


def parse_line(line: str, today: dt.date, strict: bool = False) -> ParsedExpense | None:
    """Одна строка → трата или None, если суммы нет.

    strict — для семейного чата, где пишут и обычные сообщения: сумма должна стоять в
    начале или в конце строки («кофе 250», «250 кофе»), название — не длиннее GROUP_MAX_WORDS
    слов, сумма — не меньше GROUP_MIN_AMOUNT, число в строке одно, перед суммой нет предлога,
    без доходов. Так «встретимся в 5», «приду к 19» или «купил 2 билета на завтра в 19»
    тратами не считаются."""
    text = line.strip()
    if not text or text.startswith("/"):
        return None
    matches = list(_AMOUNT.finditer(text))
    if not matches:
        return None
    # Если чисел несколько («пятёрочка 2 пакета 340»), сумма — последнее
    match = matches[-1]
    whole = re.sub(r"\s", "", match.group(1))
    try:
        amount = Decimal(f"{whole}.{match.group(2) or '0'}")
    except InvalidOperation:
        return None
    if match.group(3):
        amount *= 1000
    if amount <= 0:
        return None

    before = text[: match.start()].rstrip()
    is_income = before.endswith("+")
    if is_income:
        before = before[:-1]
    after = text[match.end():]
    rest = (before + " " + after).strip()
    date = today
    words = []
    for word in rest.split():
        key = word.lower().strip(",.")
        if key in _DAYS_AGO:
            date = today - dt.timedelta(days=_DAYS_AGO[key])
        else:
            words.append(word)
    if strict:
        meaningful = lambda part: [w for w in part.split() if w.lower().strip(",.") not in _DAYS_AGO]  # noqa: E731
        if is_income or amount < GROUP_MIN_AMOUNT or len(words) > GROUP_MAX_WORDS or len(matches) > 1:
            return None
        if words and words[-1].lower() in _PREPOSITIONS:
            return None
        if meaningful(before) and meaningful(after):
            return None
    title = " ".join(words).strip(" ,.-—:+") or ("Доход" if is_income else "Трата")
    return ParsedExpense(
        title=title[:1].upper() + title[1:255],
        amount=amount.quantize(Decimal("0.01")),
        date=date,
        is_income=is_income,
    )


def parse_message(text: str, today: dt.date) -> list[ParsedExpense]:
    lines = [ln for ln in text.splitlines() if ln.strip()][:MAX_LINES]
    return [p for p in (parse_line(ln, today) for ln in lines) if p is not None]


def parse_group_message(text: str, today: dt.date) -> list[ParsedExpense]:
    """Сообщение в семейном чате — траты, только если каждая его строка похожа на трату
    (см. strict в parse_line); иначе это обычная переписка, и бот молчит."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines or len(lines) > GROUP_MAX_LINES:
        return []
    parsed = [parse_line(ln, today, strict=True) for ln in lines]
    return parsed if all(parsed) else []


async def add_personal_expense(
    session: AsyncSession, chat: Chat, member: Member, parsed: ParsedExpense
) -> Expense:
    """Записать трату в «Мои финансы» с автоподбором категории."""
    return await add_split_expense(session, chat, member, [member], parsed)


async def add_split_expense(
    session: AsyncSession, chat: Chat, payer: Member, members: list[Member], parsed: ParsedExpense
) -> Expense:
    """Записать трату в чат: платит payer, делится поровну между members. Категория —
    автоподбор по этому чату."""
    suggestion = await suggest_category(session, chat.id, parsed.title)
    expense = Expense(
        chat_id=chat.id,
        title=parsed.title,
        amount=parsed.amount,
        category=suggestion.category if suggestion else OTHER,
        subcategory=suggestion.subcategory if suggestion else None,
        expense_date=parsed.date,
        payer_member_id=payer.id,
        split_type="equal",
        created_by_member_id=payer.id,
    )
    session.add(expense)
    await session.flush()
    for member_id, share in build_equal_shares(parsed.amount, [m.id for m in members]):
        session.add(ExpenseShare(expense_id=expense.id, member_id=member_id, amount=share))
    await learn_category(session, chat.id, expense.title, expense.category, expense.subcategory)
    await session.flush()
    return expense


async def move_to_family(
    session: AsyncSession, expense: Expense, family_chat: Chat, payer: Member
) -> list[Member]:
    """Перенести быструю трату из «Моих финансов» в семейный чат: платит payer (участник
    этого чата), делится поровну между всеми активными участниками. Возвращает участников."""
    members = await active_members(session, family_chat.id) or [payer]
    await session.execute(delete(ExpenseShare).where(ExpenseShare.expense_id == expense.id))
    expense.chat_id = family_chat.id
    expense.payer_member_id = payer.id
    expense.created_by_member_id = payer.id
    expense.split_type = "equal"
    for member_id, share in build_equal_shares(expense.amount, [m.id for m in members]):
        session.add(ExpenseShare(expense_id=expense.id, member_id=member_id, amount=share))
    await learn_category(session, family_chat.id, expense.title, expense.category, expense.subcategory)
    await session.flush()
    return members


async def active_members(session: AsyncSession, chat_id: int) -> list[Member]:
    result = await session.execute(
        select(Member).where(Member.chat_id == chat_id, Member.is_active.is_(True)).order_by(Member.id)
    )
    return list(result.scalars().all())
