"""Расчёт баланса участников чата и упрощение списка долгов (кто кому должен)."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.models import Expense, ExpenseShare, Settlement

TWO_PLACES = Decimal("0.01")


async def compute_net_balances(session: AsyncSession, chat_id: int) -> dict[int, Decimal]:
    """Возвращает net-баланс на участника: > 0 значит участнику должны, < 0 значит участник должен.

    Логика: тот, кто оплатил трату (payer), считается внёсшим всю сумму в общий котёл, поэтому его
    баланс растёт на (сумма траты − его собственная доля, если он тоже в числе участников).
    Каждый участник, за которым числится доля (ExpenseShare), должен эту долю — его баланс падает.
    Дополнительно учитываются ручные погашения (Settlement): от_member перевёл to_member — у него
    баланс растёт (долг погашен), у получателя падает (ему больше не должны на эту сумму).
    """
    balances: dict[int, Decimal] = {}

    def add(member_id: int, delta: Decimal) -> None:
        balances[member_id] = balances.get(member_id, Decimal("0")) + delta

    # Траты + доли
    expenses_result = await session.execute(
        select(Expense).where(Expense.chat_id == chat_id)
    )
    expenses = expenses_result.scalars().all()

    if expenses:
        expense_ids = [e.id for e in expenses]
        shares_result = await session.execute(
            select(ExpenseShare).where(ExpenseShare.expense_id.in_(expense_ids))
        )
        shares_by_expense: dict[int, list[ExpenseShare]] = {}
        for share in shares_result.scalars().all():
            shares_by_expense.setdefault(share.expense_id, []).append(share)

        for expense in expenses:
            shares = shares_by_expense.get(expense.id, [])
            # Каждый участник (включая того, кто платил) должен свою долю "в общий котёл" —
            # вычитаем её всем без исключения.
            for share in shares:
                add(share.member_id, -share.amount)
            # Плативший внёс в этот "котёл" полную сумму траты.
            add(expense.payer_member_id, expense.amount)

    # Ручные погашения долгов
    settlements_result = await session.execute(select(Settlement).where(Settlement.chat_id == chat_id))
    for settlement in settlements_result.scalars().all():
        add(settlement.from_member_id, settlement.amount)
        add(settlement.to_member_id, -settlement.amount)

    return {mid: amount.quantize(TWO_PLACES) for mid, amount in balances.items()}


def simplify_debts(balances: dict[int, Decimal]) -> list[tuple[int, int, Decimal]]:
    """Жадный алгоритм минимизации числа переводов: сводит вектор чистых балансов
    к минимальному набору пар (должник → кредитор, сумма)."""
    debtors = [[mid, -amt] for mid, amt in balances.items() if amt < 0]
    creditors = [[mid, amt] for mid, amt in balances.items() if amt > 0]
    debtors.sort(key=lambda x: x[1], reverse=True)
    creditors.sort(key=lambda x: x[1], reverse=True)

    result: list[tuple[int, int, Decimal]] = []
    i, j = 0, 0
    while i < len(debtors) and j < len(creditors):
        debtor_id, debt_amount = debtors[i]
        creditor_id, credit_amount = creditors[j]
        transfer = min(debt_amount, credit_amount)
        if transfer > 0:
            result.append((debtor_id, creditor_id, transfer.quantize(TWO_PLACES)))
        debtors[i][1] -= transfer
        creditors[j][1] -= transfer
        if debtors[i][1] <= Decimal("0.001"):
            i += 1
        if creditors[j][1] <= Decimal("0.001"):
            j += 1
    return result
