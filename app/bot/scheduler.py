"""Планировщик обязательных платежей (аренда, кредиты, карты, подписки).

Раз в сутки рассылает напоминания за 7, 3 и 1 день до платежа, а в день платежа спрашивает
«Оплачено?» с кнопками (см. app/shared/obligations.py, app/bot/handlers/payments.py).
Трата записывается только после подтверждения оплаты. Так же в день зарплаты спрашивает
«Пришла?» (app/shared/income.py), а 1-го числа присылает итоги прошлого месяца
(app/shared/report.py), если по прогнозу лимит будет превышен — предупреждает
(app/shared/forecast.py), а найденные по истории трат подписки предлагает сделать
обязательными платежами (app/shared/subscriptions.py).
"""
from __future__ import annotations

import datetime as dt
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.bot.notify import notify_obligations, send_texts
from app.shared.config import settings
from app.shared.database import async_session_maker
from app.shared.forecast import due_forecast_alerts
from app.shared.income import due_income_asks
from app.shared.obligations import due_notices
from app.shared.report import due_reports
from app.shared.subscriptions import due_subscription_asks

logger = logging.getLogger(__name__)


async def process_obligations(today: dt.date | None = None) -> None:
    async with async_session_maker() as session:
        day = today or dt.date.today()
        notices = await due_notices(session, day)
        notices += await due_income_asks(session, day)
        notices += await due_subscription_asks(session, day)
        reports = await due_reports(session, day)
        reports += await due_forecast_alerts(session, day)
        await session.commit()
    await notify_obligations(notices)
    await send_texts(reports)


def setup_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.SCHEDULER_TIMEZONE)
    # Проверяем каждый день в 09:00 по настроенному часовому поясу
    scheduler.add_job(process_obligations, CronTrigger(hour=9, minute=0))
    return scheduler
