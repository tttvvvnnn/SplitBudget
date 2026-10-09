"""Быстрый ввод трат текстом в личке с ботом: «кофе 250», «винлаб 1 800», «такси 350 вчера».

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


@dataclass
class ParsedExpense:
    title: str
    amount: Decimal
    date: dt.date


def parse_line(line: str, today: dt.date) -> ParsedExpense | None:
    """Одна строка → трата или None, если суммы нет."""
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

    rest = (text[: match.start()] + " " + text[match.end():]).strip()
    date = today
    words = []
    for word in rest.split():
        key = word.lower().strip(",.")
        if key in _DAYS_AGO:
            date = today - dt.timedelta(days=_DAYS_AGO[key])
        else:
            words.append(word)
    title = " ".join(words).strip(" ,.-—:") or "Трата"
    return ParsedExpense(title=title[:1].upper() + title[1:255], amount=amount.quantize(Decimal("0.01")), date=date)


def parse_message(text: str, today: dt.date) -> list[ParsedExpense]:
    lines = [ln for ln in text.splitlines() if ln.strip()][:MAX_LINES]
    return [p for p in (parse_line(ln, today) for ln in lines) if p is not None]


async def add_personal_expense(
    session: AsyncSession, chat: Chat, member: Member, parsed: ParsedExpense
) -> Expense:
    """Записать трату в «Мои финансы» с автоподбором категории."""
    suggestion = await suggest_category(session, chat.id, parsed.title)
    expense = Expense(
        chat_id=chat.id,
        title=parsed.title,
        amount=parsed.amount,
        category=suggestion.category if suggestion else OTHER,
        subcategory=suggestion.subcategory if suggestion else None,
        expense_date=parsed.date,
        payer_member_id=member.id,
        split_type="equal",
        created_by_member_id=member.id,
    )
    session.add(expense)
    await session.flush()
    session.add(ExpenseShare(expense_id=expense.id, member_id=member.id, amount=parsed.amount))
    await learn_category(session, chat.id, expense.title, expense.category, expense.subcategory)
    await session.flush()
    return expense


async def move_to_family(
    session: AsyncSession, expense: Expense, family_chat: Chat, payer: Member
) -> list[Member]:
    """Перенести быструю трату из «Моих финансов» в семейный чат: платит payer (участник
    этого чата), делится поровну между всеми активными участниками. Возвращает участников."""
    result = await session.execute(
        select(Member).where(Member.chat_id == family_chat.id, Member.is_active.is_(True)).order_by(Member.id)
    )
    members = list(result.scalars().all()) or [payer]
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
