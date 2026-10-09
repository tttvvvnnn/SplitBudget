"""«Рассчитаться» в групповом чате: /settle (или кнопка «Позвать рассчитаться» в приложении).

Бот присылает минимальный список переводов «кто → кому, сколько» с кнопкой «✅ Перевёл(а)»
на каждый. Нажать может должник или получатель — записывается погашение долга (Settlement)
на текущую сумму этого долга, сообщение обновляется. Когда всё закрыто — «Все в расчёте 🎉»
и вопрос, убрать ли чат из своего списка в приложении. Сам бот ничего не скрывает: каждый
решает за себя (семейный чат обычно оставляют, разовый с друзьями — убирают).
"""
from __future__ import annotations

import logging
from decimal import Decimal
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.notify import _member_label
from app.shared.balance import compute_net_balances, simplify_debts
from app.shared.database import async_session_maker
from app.shared.models import Chat, Member, Settlement
from app.shared.obligations import fmt_amount

logger = logging.getLogger(__name__)
router = Router(name="settle")

HIDE_QUESTION = (
    "\n\nЕсли чат больше не нужен (например, разовая встреча), его можно убрать из своего "
    "списка в приложении. Чат пропадёт только у того, кто нажал, — вернуть можно в «🙈 Скрытые»."
)


async def settle_message(session: AsyncSession, chat: Chat) -> tuple[str, InlineKeyboardMarkup]:
    """Текст и кнопки сообщения «Рассчитаться» по текущим долгам чата."""
    debts = simplify_debts(await compute_net_balances(session, chat.id))
    if not debts:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text="🙈 Убрать из моего списка", callback_data="st:hide"),
                InlineKeyboardButton(text="👌 Оставить", callback_data="st:keep"),
            ]]
        )
        return "🤝 <b>Все в расчёте 🎉</b>" + HIDE_QUESTION, markup

    ids = {m for f, t, _ in debts for m in (f, t)}
    members = {
        m.id: m for m in (await session.execute(select(Member).where(Member.id.in_(ids)))).scalars().all()
    }
    label = lambda mid: escape(_member_label(members[mid])) if mid in members else "?"  # noqa: E731
    lines = [
        f"{i}. {label(f)} → {label(t)} — <b>{fmt_amount(amount, chat.currency)}</b>"
        for i, (f, t, amount) in enumerate(debts, 1)
    ]
    rows = [
        [InlineKeyboardButton(text=f"✅ {i}. Перевёл(а)", callback_data=f"st:pay:{f}:{t}")]
        for i, (f, t, _) in enumerate(debts, 1)
    ]
    text = (
        "🤝 <b>Рассчитаться</b>\n\n" + "\n".join(lines)
        + "\n\nПеревели — нажмите кнопку (может должник или получатель)."
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("settle", "raschet"), F.chat.type.in_({"group", "supergroup"}))
async def settle_command(message: Message) -> None:
    async with async_session_maker() as session:
        chat = await session.get(Chat, message.chat.id)
        if chat is None:
            await message.answer("Сначала напишите /start, чтобы я начал вести учёт в этом чате.")
            return
        text, markup = await settle_message(session, chat)
    await message.answer(text, reply_markup=markup)


async def _member_of(session: AsyncSession, chat_id: int, tg_user_id: int) -> Member | None:
    return (
        await session.execute(select(Member).where(Member.chat_id == chat_id, Member.tg_user_id == tg_user_id))
    ).scalar_one_or_none()


@router.callback_query(F.data.startswith("st:"))
async def on_settle_button(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    chat_id = callback.message.chat.id if callback.message else None
    if chat_id is None:
        await callback.answer()
        return
    async with async_session_maker() as session:
        chat = await session.get(Chat, chat_id)
        member = await _member_of(session, chat_id, callback.from_user.id)
        if chat is None or member is None:
            await callback.answer("Вы не участник учёта в этом чате", show_alert=True)
            return

        if action == "hide":
            member.is_hidden = True
            await session.commit()
            await callback.answer(
                "Чат убран из вашего списка в приложении. Вернуть — «🙈 Скрытые» в шапке.", show_alert=True
            )
            return
        if action == "keep":
            await callback.answer("Хорошо, чат остаётся в списке")
            return
        if action != "pay" or len(parts) != 4:
            await callback.answer()
            return

        try:
            from_id, to_id = int(parts[2]), int(parts[3])
        except ValueError:
            await callback.answer()
            return
        if member.id not in (from_id, to_id):
            await callback.answer("Отметить перевод может только должник или получатель", show_alert=True)
            return
        # Сумму берём текущую: после сообщения могли добавиться траты
        debts = simplify_debts(await compute_net_balances(session, chat_id))
        amount = next((a for f, t, a in debts if f == from_id and t == to_id), Decimal("0"))
        if amount <= 0:
            await callback.answer("Этот долг уже закрыт")
        else:
            session.add(
                Settlement(
                    chat_id=chat_id,
                    from_member_id=from_id,
                    to_member_id=to_id,
                    amount=amount,
                    note="Рассчитались в чате",
                    created_by_member_id=member.id,
                )
            )
            await session.commit()
            await callback.answer(f"Записал: {fmt_amount(amount, chat.currency)}")
        text, markup = await settle_message(session, chat)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось обновить сообщение «Рассчитаться»", exc_info=True)
