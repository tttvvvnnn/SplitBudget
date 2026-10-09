"""Кнопки «✅ Оплачено» и «⏭ Не в этом месяце» под вопросом об обязательном платеже."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.notify import notify_budget_alerts
from app.shared import obligations as ob
from app.shared.budgets import alerts_after_expense
from app.shared.database import async_session_maker
from app.shared.models import Chat, Member, RecurringExpense, RecurringPayment

logger = logging.getLogger(__name__)
router = Router(name="payments")


@router.callback_query(F.data.startswith("rp:"))
async def on_payment_button(callback: CallbackQuery) -> None:
    try:
        _, action, raw_id = callback.data.split(":")
        payment_id = int(raw_id)
    except ValueError:
        await callback.answer()
        return

    async with async_session_maker() as session:
        payment = await session.get(RecurringPayment, payment_id)
        recurring = await session.get(RecurringExpense, payment.recurring_id) if payment else None
        chat = await session.get(Chat, recurring.chat_id) if recurring else None
        if chat is None:
            await callback.answer("Платёж не найден", show_alert=True)
            return
        # Нажать может только участник этого пространства: в личке — её владелец, в группе —
        # кнопка под сообщением именно этого чата.
        message_chat_id = callback.message.chat.id if callback.message else None
        allowed = callback.from_user.id == chat.id if chat.is_personal else message_chat_id == chat.id
        if not allowed:
            await callback.answer("Это не ваш платёж", show_alert=True)
            return

        actor = (
            await session.execute(
                select(Member).where(Member.chat_id == chat.id, Member.tg_user_id == callback.from_user.id)
            )
        ).scalar_one_or_none()

        expense_id = None
        try:
            if action == "pay":
                expense_id = await ob.pay(session, payment, None, actor.id if actor else None)
                result = f"✅ Оплачено: «{recurring.title}» — {ob.fmt_amount(payment.amount, chat.currency)}"
                if recurring.kind == "card" and recurring.debt is not None:
                    result += f"\nДолг по карте: {ob.fmt_amount(recurring.debt, chat.currency)}"
            elif action == "skip":
                await ob.skip(session, payment)
                result = f"⏭ «{recurring.title}» — в этом месяце не платим"
            else:
                await callback.answer()
                return
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await session.commit()

        if actor is not None and not chat.is_personal:
            result += f" ({actor.full_name})"
        try:
            await callback.message.edit_text(result)
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось обновить сообщение о платеже", exc_info=True)
        await callback.answer()

        if expense_id:
            alerts = await alerts_after_expense(session, expense_id)
            if alerts:
                await session.commit()
                await notify_budget_alerts(alerts)
