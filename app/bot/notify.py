"""Отправка уведомлений в семейный чат: о новой трате, о погашении долга, о сгенерированной
повторяющейся трате. Вызывается как из бота, так и из API (в одном процессе с ботом)."""
from __future__ import annotations

import logging
from decimal import Decimal

from app.bot.bot_instance import bot
from app.shared.models import Chat, Member

logger = logging.getLogger(__name__)


def _fmt(amount: Decimal, currency: str) -> str:
    return f"{amount:,.2f} {currency}".replace(",", " ")


def _member_label(member: Member) -> str:
    if member.username:
        return f"@{member.username}"
    return member.full_name or str(member.tg_user_id)


async def _send(chat_id: int, text: str) -> None:
    # Уведомление в чат — это побочный эффект, а не часть основной бизнес-операции
    # (трата уже создана/удалена, долг уже погашен и т.д. к моменту вызова). Поэтому ловим
    # любую ошибку, а не только TelegramAPIError: недоступность Telegram, сетевой сбой или
    # что угодно ещё не должны откатывать или ронять уже выполненное действие пользователя.
    try:
        await bot.send_message(chat_id, text)
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось отправить уведомление в чат %s", chat_id, exc_info=True)


async def notify_new_expense(
    chat: Chat,
    payer: Member,
    created_by: Member,
    title: str,
    amount: Decimal,
    category: str,
    participant_labels: list[str],
) -> None:
    text = (
        f"💸 <b>Новая трата: {title}</b>\n"
        f"Сумма: <b>{_fmt(amount, chat.currency)}</b> ({category})\n"
        f"Оплатил(а): {_member_label(payer)}\n"
        f"Участвуют: {', '.join(participant_labels)}\n"
        f"Добавил(а): {_member_label(created_by)}"
    )
    await _send(chat.id, text)


async def notify_expense_deleted(chat: Chat, title: str, actor: Member) -> None:
    await _send(chat.id, f"🗑 {_member_label(actor)} удалил(а) трату «{title}»")


async def notify_settlement(chat: Chat, from_member: Member, to_member: Member, amount: Decimal) -> None:
    text = (
        f"✅ {_member_label(from_member)} погасил(а) долг перед {_member_label(to_member)} "
        f"на сумму {_fmt(amount, chat.currency)}"
    )
    await _send(chat.id, text)


async def notify_recurring_generated(
    chat: Chat, title: str, amount: Decimal, payer: Member
) -> None:
    text = (
        f"🔁 Автоматически добавлена повторяющаяся трата «{title}» на сумму "
        f"{_fmt(amount, chat.currency)} (оплатил(а) {_member_label(payer)})"
    )
    await _send(chat.id, text)
