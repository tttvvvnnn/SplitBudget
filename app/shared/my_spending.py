"""Сколько из каждой траты на самом деле потратил пользователь — для «Моих финансов»
(«Все траты», статистика, План, лимиты, прогноз).

Считаем по реальным деньгам, а не по долям. Кто оплатил семейную трату, тот потратил её
целиком, пока остальные не вернули ему свои доли. Возврат (Settlement) закрывает самые
старые неоплаченные доли — и они «переезжают» к должнику в той же категории. Поэтому итог
пользователя всегда равен «сколько заплатил сам − сколько ему вернули + сколько вернул он».

Пример: я оплатил продукты 500 ₽ пополам. Пока жена не вернула 250, у меня в «Продуктах»
500, у неё 0. Вернула 250 — у меня 250, у неё 250. Возврат задним числом пересчитывает
и прошлые месяцы.

Взаимные долги тоже закрываются: если я заплатил за жену 250, а она за меня 150, то 150 из
каждой стороны считаются погашенными (как будто мы обменялись деньгами), — суммы по
кошельку не меняются, а по категориям раскладываются честнее. В чатах на троих и больше
зачёт считается по общему долгу участника, без разбивки по тому, кому именно он должен.

Хранить ничего не нужно: всё пересчитывается из трат, долей и погашений.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.models import Chat, Expense, ExpenseShare, Member, Settlement

ZERO = Decimal("0")


@dataclass
class MyExpense:
    expense: Expense
    chat: Chat
    amount: Decimal  # сколько из траты сейчас на пользователе (может быть 0)
    i_paid: bool


def _fifo(items: list[tuple[Expense, Decimal]], closed: Decimal) -> dict[int, Decimal]:
    """Распределяет закрытую сумму по позициям от самых старых: {expense_id: закрыто}."""
    out = {}
    for expense, amount in items:
        part = min(amount, closed)
        out[expense.id] = part
        closed -= part
    return out


async def _in_chat(session: AsyncSession, member: Member, chat: Chat, start: dt.date, end: dt.date) -> list[MyExpense]:
    if chat.is_personal:
        # В личном пространстве владелец единственный участник — трата целиком его
        rows = (
            await session.execute(
                select(Expense).where(Expense.chat_id == chat.id, Expense.expense_date >= start, Expense.expense_date < end)
            )
        ).scalars().all()
        return [MyExpense(e, chat, e.amount, True) for e in rows]

    # Возвраты пересчитывают и прошлые траты, поэтому берём всю историю чата
    mine = select(ExpenseShare.expense_id).where(ExpenseShare.member_id == member.id)
    expenses = (
        await session.execute(
            select(Expense)
            .where(Expense.chat_id == chat.id, or_(Expense.payer_member_id == member.id, Expense.id.in_(mine)))
            .order_by(Expense.expense_date, Expense.id)
        )
    ).scalars().all()
    if not expenses:
        return []
    shares: dict[int, list[ExpenseShare]] = {}
    for share in (
        await session.execute(select(ExpenseShare).where(ExpenseShare.expense_id.in_([e.id for e in expenses])))
    ).scalars().all():
        shares.setdefault(share.expense_id, []).append(share)

    # credit — сколько я заплатил за других, debt — сколько другие заплатили за меня
    credit: list[tuple[Expense, Decimal]] = []
    debt: list[tuple[Expense, Decimal]] = []
    for e in expenses:
        if e.payer_member_id == member.id:
            others = sum((s.amount for s in shares.get(e.id, []) if s.member_id != member.id), ZERO)
            credit.append((e, others))
        else:
            own = sum((s.amount for s in shares.get(e.id, []) if s.member_id == member.id), ZERO)
            debt.append((e, own))

    async def settled(column) -> Decimal:
        total = await session.scalar(
            select(func.sum(Settlement.amount)).where(Settlement.chat_id == chat.id, column == member.id)
        )
        return Decimal(total or 0)

    received, paid = await settled(Settlement.to_member_id), await settled(Settlement.from_member_id)
    credit_total = sum((a for _, a in credit), ZERO)
    debt_total = sum((a for _, a in debt), ZERO)
    # Взаимозачёт: часть долга в обе стороны, которая не нужна для баланса
    netted = max(ZERO, min(credit_total - received, debt_total - paid))
    returned = _fifo(credit, min(credit_total, received + netted))
    repaid = _fifo(debt, min(debt_total, paid + netted))

    out = []
    for e in expenses:
        if not (start <= e.expense_date < end):
            continue
        if e.payer_member_id == member.id:
            out.append(MyExpense(e, chat, e.amount - returned.get(e.id, ZERO), True))
        else:
            out.append(MyExpense(e, chat, repaid.get(e.id, ZERO), False))
    return out


async def my_expenses(session: AsyncSession, tg_user_id: int, start: dt.date, end: dt.date) -> list[MyExpense]:
    """Траты в [start, end), которые пользователь оплатил или в которых у него есть доля, — по всем
    его пространствам (в т.ч. чатам, из которых он вышел: история остаётся). Новые сверху.
    Трата с amount == 0 — за пользователя заплатили, и он ещё не вернул."""
    rows = (
        await session.execute(select(Member, Chat).join(Chat, Chat.id == Member.chat_id).where(Member.tg_user_id == tg_user_id))
    ).all()
    out: list[MyExpense] = []
    for member, chat in rows:
        out += await _in_chat(session, member, chat, start, end)
    out.sort(key=lambda m: (m.expense.expense_date, m.expense.id), reverse=True)
    return out
