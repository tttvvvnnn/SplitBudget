"""Общие функции работы с базой, переиспользуемые и ботом, и API."""
from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.config import settings
from app.shared.models import Chat, Member, RecurringExpense, RecurringParticipant

CENT = Decimal("0.01")


async def get_or_create_chat(session: AsyncSession, chat_id: int, title: str) -> Chat:
    chat = await session.get(Chat, chat_id)
    if chat is None:
        chat = Chat(id=chat_id, title=title, currency=settings.DEFAULT_CURRENCY)
        session.add(chat)
        await session.flush()
    elif title and chat.title != title:
        chat.title = title
    return chat


async def get_or_create_member(
    session: AsyncSession,
    chat_id: int,
    tg_user_id: int,
    username: str | None,
    full_name: str,
) -> Member:
    result = await session.execute(
        select(Member).where(Member.chat_id == chat_id, Member.tg_user_id == tg_user_id)
    )
    member = result.scalar_one_or_none()
    if member is None:
        member = Member(
            chat_id=chat_id,
            tg_user_id=tg_user_id,
            username=username,
            full_name=full_name or (username or str(tg_user_id)),
            is_active=True,
        )
        session.add(member)
        await session.flush()
    else:
        changed = False
        if not member.is_active:
            member.is_active = True
            changed = True
        if username and member.username != username:
            member.username = username
            changed = True
        if full_name and member.full_name != full_name:
            member.full_name = full_name
            changed = True
        if changed:
            await session.flush()
    return member


async def deactivate_member(session: AsyncSession, chat_id: int, tg_user_id: int) -> None:
    result = await session.execute(
        select(Member).where(Member.chat_id == chat_id, Member.tg_user_id == tg_user_id)
    )
    member = result.scalar_one_or_none()
    if member is not None:
        member.is_active = False
        await session.flush()


def build_equal_shares(amount: Decimal, participant_ids: list[int]) -> list[tuple[int, Decimal]]:
    """Делит amount поровну между participant_ids, копейки округления отдаёт первому участнику,
    чтобы сумма долей точно равнялась amount."""
    if not participant_ids:
        raise ValueError("Нужно выбрать хотя бы одного участника траты")
    n = len(participant_ids)
    base = (amount / n).quantize(CENT, rounding=ROUND_HALF_UP)
    shares = [base] * n
    diff = amount - (base * n)
    # раскидываем остаток округления по копейке, начиная с первого участника
    diff_cents = int((diff / CENT).to_integral_value(rounding=ROUND_HALF_UP))
    idx = 0
    step = 1 if diff_cents >= 0 else -1
    for _ in range(abs(diff_cents)):
        shares[idx % n] += CENT * step
        idx += 1
    return list(zip(participant_ids, shares))


def build_custom_shares(
    amount: Decimal, custom_shares: list[tuple[int, Decimal]]
) -> list[tuple[int, Decimal]]:
    if not custom_shares:
        raise ValueError("Нужно указать доли участников")
    total = sum((s for _, s in custom_shares), Decimal("0"))
    if total.quantize(CENT) != amount.quantize(CENT):
        raise ValueError(
            f"Сумма долей участников ({total}) не совпадает с суммой траты ({amount})"
        )
    return custom_shares


async def due_recurring_for_month(
    session: AsyncSession, chat_id: int, year_month: str
) -> list[RecurringExpense]:
    """Возвращает активные шаблоны повторяющихся трат этого чата, ещё не сгенерированные за
    указанный месяц (year_month в формате 'YYYY-MM')."""
    result = await session.execute(
        select(RecurringExpense).where(
            RecurringExpense.chat_id == chat_id,
            RecurringExpense.is_active.is_(True),
        )
    )
    templates = result.scalars().all()
    return [t for t in templates if t.last_generated_month != year_month]


async def load_recurring_participants(
    session: AsyncSession, recurring_id: int
) -> list[RecurringParticipant]:
    result = await session.execute(
        select(RecurringParticipant).where(RecurringParticipant.recurring_id == recurring_id)
    )
    return list(result.scalars().all())
