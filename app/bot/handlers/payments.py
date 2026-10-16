"""Кнопки «✅ Оплачено» и «⏭ Не в этом месяце» под вопросом об обязательном платеже и
«✅ Пришла» / «⏳ Ещё нет» под вопросом о регулярном доходе. После «Пришла» — сколько
отложить на цели по плану распределения дохода и кнопка «✅ Отложил»."""
from __future__ import annotations

import datetime as dt
import logging
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import delete, select

from app.bot.notify import notify_budget_alerts
from app.shared import obligations as ob
from app.shared import plan
from app.shared.budgets import alerts_after_expense, month_range
from app.shared.database import async_session_maker
from app.shared.income import record_income, source_date
from app.shared.models import (
    Chat,
    GoalDeposit,
    Income,
    IncomeAsk,
    IncomeSource,
    Member,
    RecurringExpense,
    RecurringPayment,
)

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


@router.callback_query(F.data.startswith("inc:"))
async def on_income_button(callback: CallbackQuery) -> None:
    try:
        _, action, raw_id, month = callback.data.split(":")
        source_id = int(raw_id)
        month_range(month)
    except (ValueError, IndexError):
        await callback.answer()
        return
    markup = None
    async with async_session_maker() as session:
        source = await session.get(IncomeSource, source_id)
        if source is None or source.chat_id != callback.from_user.id:
            await callback.answer("Доход не найден", show_alert=True)
            return
        chat = await session.get(Chat, source.chat_id)
        start, end = month_range(month)
        already = await session.execute(
            select(Income.id).where(
                Income.source_id == source.id, Income.income_date >= start, Income.income_date < end
            )
        )
        if action == "yes" and already.first() is not None:
            text = f"✅ Доход «{source.title}» за этот месяц уже записан"
        elif action == "yes":
            today = dt.date.today()
            # Подтвердили за другой месяц (ответили на вопрос с опозданием) — пишем в тот месяц
            date = today if ob.month_of(today) == month else source_date(source, month)
            income = await record_income(session, chat, source.title, source.amount, date, source.id)
            text = f"✅ Записал доход «{source.title}» — {ob.fmt_amount(source.amount, chat.currency)}"
            savings = await plan.savings_for_income(session, income)
            if savings:
                text += "\n\n🐷 По плану отложить:\n" + "\n".join(
                    f"• {escape(goal.title)} — {ob.fmt_amount(amount, chat.currency)}" for _, goal, amount in savings
                )
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="✅ Отложил", callback_data=f"plan:save:{income.id}")]]
                )
        else:
            # Спросим ещё раз на следующей ежедневной проверке
            await session.execute(
                delete(IncomeAsk).where(IncomeAsk.source_id == source.id, IncomeAsk.month == month)
            )
            text = f"⏳ «{source.title}» ещё не пришла — спрошу завтра"
        await session.commit()
    try:
        await callback.message.edit_text(text, **({"reply_markup": markup} if markup else {}))
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось обновить сообщение о доходе", exc_info=True)
    await callback.answer()


@router.callback_query(F.data.startswith("plan:save:"))
async def on_plan_save_button(callback: CallbackQuery) -> None:
    """«✅ Отложил» — пополнения целей с этого поступления по процентам плана."""
    try:
        income_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer()
        return
    async with async_session_maker() as session:
        income = await session.get(Income, income_id)
        if income is None or income.chat_id != callback.from_user.id:
            await callback.answer("Доход не найден", show_alert=True)
            return
        chat = await session.get(Chat, income.chat_id)
        if await plan.income_saved(session, income.id):
            await callback.answer("Уже отложено")
            return
        savings = await plan.savings_for_income(session, income)
        for _, goal, amount in savings:
            session.add(GoalDeposit(goal_id=goal.id, amount=amount, deposit_date=dt.date.today(), income_id=income.id))
        await session.commit()
    text = callback.message.html_text if callback.message.text else ""
    lines = [text.split("\n\n")[0]] if text else []
    lines.append("🐷 Отложил: " + ", ".join(f"{escape(goal.title)} {ob.fmt_amount(amount, chat.currency)}" for _, goal, amount in savings))
    try:
        await callback.message.edit_text("\n\n".join(lines))
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось обновить сообщение об отложенном", exc_info=True)
    await callback.answer("Отложено")
