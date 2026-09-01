"""Планировщик генерации повторяющихся трат (аренда, подписки и т.п.).

Раз в сутки проверяет все активные шаблоны RecurringExpense и создаёт из них обычную
Expense, если сегодняшнее число совпадает с днём в шаблоне и в этом месяце трата ещё не
была сгенерирована.
"""
from __future__ import annotations

import datetime as dt
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import or_, select

from app.bot.notify import notify_recurring_generated
from app.shared.config import settings
from app.shared.crud import build_custom_shares, build_equal_shares, load_recurring_participants
from app.shared.database import async_session_maker
from app.shared.models import Chat, Expense, ExpenseShare, Member, RecurringExpense

logger = logging.getLogger(__name__)


async def generate_due_recurring_expenses() -> None:
    today = dt.date.today()
    year_month = today.strftime("%Y-%m")

    async with async_session_maker() as session:
        # NB: last_generated_month может быть NULL (ни разу не генерировалась) — обычное
        # "!= year_month" в SQL для NULL даёт NULL (не true), поэтому такие строки нужно
        # включать через отдельное условие IS NULL.
        result = await session.execute(
            select(RecurringExpense).where(
                RecurringExpense.is_active.is_(True),
                RecurringExpense.day_of_month == today.day,
                or_(
                    RecurringExpense.last_generated_month.is_(None),
                    RecurringExpense.last_generated_month != year_month,
                ),
            )
        )
        templates = result.scalars().all()

        for template in templates:
            participants = await load_recurring_participants(session, template.id)
            if not participants:
                continue

            if template.split_type == "custom":
                pairs = [(p.member_id, p.custom_amount) for p in participants if p.custom_amount]
                try:
                    shares = build_custom_shares(template.amount, pairs)
                except ValueError as exc:
                    logger.warning("Пропуск повторяющейся траты #%s: %s", template.id, exc)
                    template.last_generated_month = year_month
                    continue
            else:
                ids = [p.member_id for p in participants]
                shares = build_equal_shares(template.amount, ids)

            expense = Expense(
                chat_id=template.chat_id,
                title=template.title,
                amount=template.amount,
                category=template.category,
                expense_date=today,
                payer_member_id=template.payer_member_id,
                split_type=template.split_type,
                created_by_member_id=template.payer_member_id,
                recurring_id=template.id,
            )
            session.add(expense)
            await session.flush()

            for member_id, amount in shares:
                session.add(ExpenseShare(expense_id=expense.id, member_id=member_id, amount=amount))

            template.last_generated_month = year_month
            await session.flush()

            chat = await session.get(Chat, template.chat_id)
            payer = await session.get(Member, template.payer_member_id)
            await session.commit()

            if chat and payer:
                await notify_recurring_generated(chat, template.title, template.amount, payer)

        await session.commit()


def setup_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.SCHEDULER_TIMEZONE)
    # Проверяем каждый день в 09:00 по настроенному часовому поясу
    scheduler.add_job(generate_due_recurring_expenses, CronTrigger(hour=9, minute=0))
    return scheduler
