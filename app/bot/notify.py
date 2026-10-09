"""Отправка уведомлений в семейный чат: о новой трате, о погашении долга, о лимитах и
обязательных платежах. Вызывается как из бота, так и из API (в одном процессе с ботом)."""
from __future__ import annotations

import logging
from decimal import Decimal

from app.bot.bot_instance import bot
from app.bot.keyboards import payment_keyboard
from app.shared.models import Chat, Member
from app.shared.obligations import Notice

logger = logging.getLogger(__name__)


def _fmt(amount: Decimal, currency: str) -> str:
    return f"{amount:,.2f} {currency}".replace(",", " ")


def _member_label(member: Member) -> str:
    if member.username:
        return f"@{member.username}"
    return member.full_name or str(member.tg_user_id)


async def _send(chat: Chat, text: str) -> None:
    # Личное пространство — не чат в Telegram, а раздел мини-аппа: о своих же тратах писать
    # владельцу незачем.
    if chat.is_personal:
        return
    # Уведомление в чат — это побочный эффект, а не часть основной бизнес-операции
    # (трата уже создана/удалена, долг уже погашен и т.д. к моменту вызова). Поэтому ловим
    # любую ошибку, а не только TelegramAPIError: недоступность Telegram, сетевой сбой или
    # что угодно ещё не должны откатывать или ронять уже выполненное действие пользователя.
    try:
        await bot.send_message(chat.id, text)
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось отправить уведомление в чат %s", chat.id, exc_info=True)


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
    await _send(chat, text)


async def notify_expense_deleted(chat: Chat, title: str, actor: Member) -> None:
    await _send(chat, f"🗑 {_member_label(actor)} удалил(а) трату «{title}»")


async def notify_settlement(chat: Chat, from_member: Member, to_member: Member, amount: Decimal) -> None:
    text = (
        f"✅ {_member_label(from_member)} погасил(а) долг перед {_member_label(to_member)} "
        f"на сумму {_fmt(amount, chat.currency)}"
    )
    await _send(chat, text)


async def notify_budget_alerts(alerts: list[tuple[int, str]]) -> None:
    """Уведомления о лимитах (см. app/shared/budgets.alerts_after_expense). Шлём и в личку
    владельцу «Моих финансов» — в отличие от _send, личное пространство тут не пропускаем.
    Бот может написать в личку, только если человек хоть раз нажал у него Start."""
    for chat_id, text in alerts:
        try:
            await bot.send_message(chat_id, text)
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось отправить уведомление о лимите в чат %s", chat_id, exc_info=True)


async def notify_payment_marked(
    chat: Chat, title: str, amount: Decimal | None, actor: Member, paid: bool
) -> None:
    if not paid:
        return
    amount_text = f" — {_fmt(amount, chat.currency)}" if amount is not None else ""
    await _send(chat, f"✅ {_member_label(actor)} отметил(а) оплату «{title}»{amount_text}")


async def notify_obligations(notices: list[Notice]) -> None:
    """Напоминания об обязательных платежах и вопрос «Оплачено?» с кнопками (см.
    app/shared/obligations.due_notices). Для «Моих финансов» пишем в личку владельцу."""
    if not notices:
        return
    try:
        me = await bot.get_me()
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось получить имя бота", exc_info=True)
        return
    for notice in notices:
        markup = None
        if notice.ask_payment_id is not None:
            markup = payment_keyboard(me.username, notice.ask_payment_id, notice.chat_link_id or notice.chat_id)
        try:
            await bot.send_message(notice.chat_id, notice.text, reply_markup=markup)
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось отправить напоминание о платеже в чат %s", notice.chat_id, exc_info=True)
