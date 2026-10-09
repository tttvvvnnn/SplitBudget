"""Быстрый ввод трат в личке с ботом: пишешь «кофе 250» — трата записывается в «Мои финансы»
с автоподбором категории. Под ответом — кнопки: поменять категорию (бот запомнит
исправление), отменить или перенести трату в семейный чат (поровну на всех)."""
from __future__ import annotations

import datetime as dt
import logging
from html import escape
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.bot.notify import notify_budget_alerts, notify_new_expense
from app.shared.autocategory import learn_category
from app.shared.budgets import alerts_after_expense
from app.shared.categories import CATEGORY_TREE
from app.shared.crud import get_or_create_personal_space
from app.shared.database import async_session_maker
from app.shared.models import Chat, Expense, Member
from app.shared.quick_entry import add_personal_expense, move_to_family, parse_message

logger = logging.getLogger(__name__)
router = Router(name="quick")

HELP = (
    "Чтобы записать трату, напишите название и сумму, например:\n"
    "<code>кофе 250</code>\n<code>винлаб 1 800</code>\n<code>такси 350 вчера</code>\n"
    "Можно несколько трат — каждую с новой строки."
)
MAX_FAMILY_BUTTONS = 3


def _money(amount: Decimal, currency: str) -> str:
    text = f"{amount:,.0f}" if amount == amount.to_integral_value() else f"{amount:,.2f}"
    return f"{text.replace(',', ' ')} {currency}"


def _icon(category: str) -> str:
    return next((icon for name, icon, _ in CATEGORY_TREE if name == category), "🏷️")


def _category_label(expense: Expense) -> str:
    return expense.category + (f" › {expense.subcategory}" if expense.subcategory else "")


def _date_note(expense: Expense) -> str:
    days = (dt.date.today() - expense.expense_date).days
    return {0: "", 1: " · вчера", 2: " · позавчера"}.get(days, f" · {expense.expense_date:%d.%m}")


def _line(expense: Expense, currency: str) -> str:
    return (
        f"{_icon(expense.category)} {escape(expense.title)} — <b>{_money(expense.amount, currency)}</b>"
        f"{_date_note(expense)}\n<i>{escape(_category_label(expense))}</i>"
    )


def _single_markup(expense_id: int, family_chats: list[Chat]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="🏷 Категория", callback_data=f"qe:cat:{expense_id}"),
            InlineKeyboardButton(text="↩️ Отменить", callback_data=f"qe:undo:{expense_id}"),
        ]
    ]
    for chat in family_chats[:MAX_FAMILY_BUTTONS]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"👪 В «{chat.title or 'семейный чат'}» поровну",
                    callback_data=f"qe:fam:{expense_id}:{chat.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _family_chats(session, tg_user_id: int) -> list[Chat]:
    result = await session.execute(
        select(Chat)
        .join(Member, Member.chat_id == Chat.id)
        .where(Member.tg_user_id == tg_user_id, Member.is_active.is_(True), Chat.is_personal.is_(False))
        .order_by(Chat.id)
    )
    return list(result.scalars().all())


@router.message(F.chat.type == "private", F.text, ~F.text.startswith("/"))
async def quick_expense(message: Message) -> None:
    if not message.from_user:
        return
    parsed = parse_message(message.text, dt.date.today())
    if not parsed:
        await message.answer(HELP)
        return

    user = message.from_user
    async with async_session_maker() as session:
        chat, member = await get_or_create_personal_space(session, user.id, user.username, user.full_name)
        expenses = [await add_personal_expense(session, chat, member, p) for p in parsed]
        await session.commit()
        family = await _family_chats(session, user.id)

        if len(expenses) == 1:
            expense = expenses[0]
            await message.answer(
                f"✅ Записал в «Мои финансы»\n{_line(expense, chat.currency)}",
                reply_markup=_single_markup(expense.id, family),
            )
        else:
            total = sum((e.amount for e in expenses), Decimal("0"))
            lines = "\n".join(_line(e, chat.currency) for e in expenses)
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="↩️ Отменить все",
                            callback_data=f"qe:undoall:{expenses[0].id}:{expenses[-1].id}",
                        )
                    ]
                ]
            )
            await message.answer(
                f"✅ Записал в «Мои финансы» {len(expenses)} трат на {_money(total, chat.currency)}\n\n{lines}",
                reply_markup=markup,
            )

        alerts = []
        for expense in expenses:
            alerts.extend(await alerts_after_expense(session, expense.id))
        if alerts:
            await session.commit()
            await notify_budget_alerts(alerts)


def _category_markup(expense_id: int) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=f"{icon} {name}", callback_data=f"qe:c:{expense_id}:{i}")
        for i, (name, icon, _) in enumerate(CATEGORY_TREE)
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _subcategory_markup(expense_id: int, ci: int) -> InlineKeyboardMarkup:
    _, _, subs = CATEGORY_TREE[ci]
    buttons = [
        InlineKeyboardButton(text=sub, callback_data=f"qe:s:{expense_id}:{ci}:{si}") for si, sub in enumerate(subs)
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="Без подкатегории", callback_data=f"qe:s:{expense_id}:{ci}:-1")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _own_personal_expense(session, callback: CallbackQuery, expense_id: int) -> Expense | None:
    expense = await session.get(Expense, expense_id)
    if expense is None or expense.chat_id != callback.from_user.id:
        await callback.answer("Трата не найдена — возможно, её уже удалили или перенесли", show_alert=True)
        return None
    return expense


async def _edit(callback: CallbackQuery, text: str | None = None, markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        if text is None:
            await callback.message.edit_reply_markup(reply_markup=markup)
        else:
            await callback.message.edit_text(text, reply_markup=markup)
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось обновить сообщение о быстрой трате", exc_info=True)


@router.callback_query(F.data.startswith("qe:"))
async def on_quick_button(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    try:
        nums = [int(p) for p in parts[2:]]
    except ValueError:
        await callback.answer()
        return
    if not nums:
        await callback.answer()
        return

    async with async_session_maker() as session:
        personal = await session.get(Chat, callback.from_user.id)
        currency = personal.currency if personal else ""

        if action == "undoall" and len(nums) == 2:
            result = await session.execute(
                select(Expense).where(
                    Expense.chat_id == callback.from_user.id, Expense.id >= nums[0], Expense.id <= nums[1]
                )
            )
            removed = result.scalars().all()
            for expense in removed:
                await session.delete(expense)
            await session.commit()
            await _edit(callback, f"↩️ Отменил {len(removed)} трат")
            await callback.answer()
            return

        expense = await _own_personal_expense(session, callback, nums[0])
        if expense is None:
            return

        if action == "undo":
            text = f"↩️ Отменено: {escape(expense.title)} — {_money(expense.amount, currency)}"
            await session.delete(expense)
            await session.commit()
            await _edit(callback, text)
        elif action == "cat":
            await _edit(callback, markup=_category_markup(expense.id))
        elif action in ("c", "s") and len(nums) >= 2 and 0 <= nums[1] < len(CATEGORY_TREE):
            name, _, subs = CATEGORY_TREE[nums[1]]
            if action == "c" and subs:
                await _edit(callback, markup=_subcategory_markup(expense.id, nums[1]))
                await callback.answer()
                return
            si = nums[2] if action == "s" and len(nums) > 2 else -1
            expense.category = name
            expense.subcategory = subs[si] if 0 <= si < len(subs) else None
            await learn_category(session, expense.chat_id, expense.title, expense.category, expense.subcategory)
            await session.commit()
            family = await _family_chats(session, callback.from_user.id)
            await _edit(
                callback,
                f"✅ Записал в «Мои финансы»\n{_line(expense, currency)}\n🧠 Запомнил на будущее",
                _single_markup(expense.id, family),
            )
        elif action == "fam" and len(nums) == 2:
            family_chat = await session.get(Chat, nums[1])
            payer = (
                await session.execute(
                    select(Member).where(
                        Member.chat_id == nums[1],
                        Member.tg_user_id == callback.from_user.id,
                        Member.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if family_chat is None or family_chat.is_personal or payer is None:
                await callback.answer("Вы не участник этого чата", show_alert=True)
                return
            members = await move_to_family(session, expense, family_chat, payer)
            await session.commit()
            await _edit(
                callback,
                f"👪 Перенёс в «{escape(family_chat.title)}», поровну на {len(members)}\n{_line(expense, family_chat.currency)}",
            )
            labels = [f"@{m.username}" if m.username else m.full_name for m in members]
            await notify_new_expense(
                family_chat, payer, payer, expense.title, expense.amount, _category_label(expense), labels
            )
            alerts = await alerts_after_expense(session, expense.id)
            if alerts:
                await session.commit()
                await notify_budget_alerts(alerts)
        await callback.answer()
