"""Быстрый ввод трат текстом.

В личке с ботом: «кофе 250» сразу записывается в «Мои финансы» с автоподбором категории.
Под ответом — кнопки: поменять категорию (бот запомнит исправление), отменить или
перенести трату в семейный чат (поровну на всех).

В семейном чате: на сообщение, похожее на трату («кофе 250», см. parse_group_message), бот
отвечает кнопками «👪 Поровну на всех» / «👤 В мои финансы» / «✖️ Это не трата» —
выбирает автор. Обычную переписку бот не трогает; явно — командой /t кофе 250."""
from __future__ import annotations

import datetime as dt
import logging
from html import escape
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.bot.notify import notify_budget_alerts, notify_new_expense
from app.shared.autocategory import learn_category
from app.shared.budgets import alerts_after_expense
from app.shared.categories import CATEGORY_TREE
from app.shared.crud import get_or_create_member, get_or_create_personal_space
from app.shared.database import async_session_maker
from app.shared.income import record_income
from app.shared.models import Chat, Expense, Income, Member
from app.shared.quick_entry import (
    ParsedExpense,
    active_members,
    add_personal_expense,
    add_split_expense,
    move_to_family,
    parse_group_message,
    parse_message,
)

logger = logging.getLogger(__name__)
router = Router(name="quick")

HELP = (
    "Чтобы записать трату, напишите название и сумму, например:\n"
    "<code>кофе 250</code>\n<code>винлаб 1 800</code>\n<code>такси 350 вчера</code>\n"
    "Доход — с плюсом: <code>+120000 зарплата</code>\n"
    "Можно несколько записей — каждую с новой строки."
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
        expenses = [await add_personal_expense(session, chat, member, p) for p in parsed if not p.is_income]
        incomes = [
            await record_income(session, chat, p.title, p.amount, p.date) for p in parsed if p.is_income
        ]
        await session.commit()
        family = await _family_chats(session, user.id)

        if len(expenses) == 1 and not incomes:
            expense = expenses[0]
            await message.answer(
                f"✅ Записал в «Мои финансы»\n{_line(expense, chat.currency)}",
                reply_markup=_single_markup(expense.id, family),
            )
        else:
            parts = []
            if expenses:
                total = sum((e.amount for e in expenses), Decimal("0"))
                parts.append(f"✅ Записал {len(expenses)} трат на {_money(total, chat.currency)}")
                parts.append("\n".join(_line(e, chat.currency) for e in expenses))
            if incomes:
                parts.append("\n".join(
                    f"💰 Доход: {escape(i.title)} — <b>+{_money(i.amount, chat.currency)}</b>" for i in incomes
                ))
            first_e, last_e = (expenses[0].id, expenses[-1].id) if expenses else (0, 0)
            first_i, last_i = (incomes[0].id, incomes[-1].id) if incomes else (0, 0)
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="↩️ Отменить" if len(expenses) + len(incomes) == 1 else "↩️ Отменить все",
                            callback_data=f"qe:undoall:{first_e}:{last_e}:{first_i}:{last_i}",
                        )
                    ]
                ]
            )
            await message.answer("\n\n".join(parts), reply_markup=markup)

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


def _created_by(tg_user_id: int):
    """Условие «трату создал этот пользователь Telegram» (в любом его пространстве)."""
    return Expense.created_by_member_id.in_(select(Member.id).where(Member.tg_user_id == tg_user_id))


async def _own_expense(session, callback: CallbackQuery, expense_id: int) -> Expense | None:
    expense = (
        await session.execute(select(Expense).where(Expense.id == expense_id, _created_by(callback.from_user.id)))
    ).scalar_one_or_none()
    if expense is None:
        await callback.answer("Трата не найдена — возможно, её уже удалили или перенесли", show_alert=True)
        return None
    return expense


def _in_private(callback: CallbackQuery) -> bool:
    chat = getattr(callback.message, "chat", None)
    return chat is None or chat.type == "private"


def _header(chat: Chat, members_count: int | None = None) -> str:
    if chat.is_personal:
        return "✅ Записал в «Мои финансы»"
    split = f", поровну на {members_count}" if members_count else ""
    return f"✅ Записал в «{escape(chat.title or 'семейный чат')}»{split}"


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
        if action == "undoall" and len(nums) in (2, 4):
            removed = 0
            queries = []
            if nums[0]:
                queries.append(
                    select(Expense).where(Expense.id >= nums[0], Expense.id <= nums[1], _created_by(callback.from_user.id))
                )
            if len(nums) == 4 and nums[2]:
                queries.append(
                    select(Income).where(
                        Income.id >= nums[2], Income.id <= nums[3], Income.chat_id == callback.from_user.id
                    )
                )
            for query in queries:
                for row in (await session.execute(query)).scalars().all():
                    await session.delete(row)
                    removed += 1
            await session.commit()
            await _edit(callback, f"↩️ Отменил записей: {removed}")
            await callback.answer()
            return

        expense = await _own_expense(session, callback, nums[0])
        if expense is None:
            return
        chat = await session.get(Chat, expense.chat_id)
        currency = chat.currency

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
            family = await _family_chats(session, callback.from_user.id) if chat.is_personal and _in_private(callback) else []
            await _edit(
                callback,
                f"{_header(chat)}\n{_line(expense, currency)}\n🧠 Запомнил на будущее",
                _single_markup(expense.id, family),
            )
        elif action == "fam" and len(nums) == 2 and chat.is_personal:
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


# ---------------- Семейный чат ----------------

GROUP_CHOICE = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="👪 Поровну на всех", callback_data="qg:split"),
            InlineKeyboardButton(text="👤 В мои финансы", callback_data="qg:mine"),
        ],
        [InlineKeyboardButton(text="✖️ Это не трата", callback_data="qg:no")],
    ]
)


def _entry_text(text: str) -> str:
    """Текст траты без команды: «/t кофе 250» → «кофе 250»."""
    if text.startswith("/"):
        return text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
    return text


async def _ask_group(message: Message, parsed: list[ParsedExpense]) -> None:
    async with async_session_maker() as session:
        chat = await session.get(Chat, message.chat.id)
        currency = chat.currency if chat else ""
    lines = "\n".join(
        f"💸 {escape(p.title)} — <b>{_money(p.amount, currency)}</b>"
        + {0: "", 1: " · вчера", 2: " · позавчера"}.get((dt.date.today() - p.date).days, "")
        for p in parsed
    )
    await message.reply(f"{lines}\nКак записать?", reply_markup=GROUP_CHOICE)


@router.message(Command("t"), F.chat.type.in_({"group", "supergroup"}))
async def group_command(message: Message, command: CommandObject) -> None:
    parsed = parse_message(command.args or "", dt.date.today())
    parsed = [p for p in parsed if not p.is_income]
    if not parsed:
        await message.reply("Напишите трату после команды, например: <code>/t кофе 250</code>")
        return
    await _ask_group(message, parsed)


@router.message(F.chat.type.in_({"group", "supergroup"}), F.text, ~F.text.startswith("/"))
async def group_expense(message: Message) -> None:
    if not message.from_user or message.from_user.is_bot:
        return
    parsed = parse_group_message(message.text, dt.date.today())
    if parsed:
        await _ask_group(message, parsed)


@router.callback_query(F.data.startswith("qg:"))
async def on_group_choice(callback: CallbackQuery) -> None:
    action = callback.data.split(":", 1)[1]
    original = getattr(callback.message, "reply_to_message", None)
    if original is None or original.from_user is None:
        await callback.answer("Не нашёл исходное сообщение — напишите трату ещё раз", show_alert=True)
        return
    if original.from_user.id != callback.from_user.id:
        await callback.answer("Выбрать может только тот, кто написал трату", show_alert=True)
        return
    if action == "no":
        try:
            await callback.message.delete()
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось удалить вопрос о трате", exc_info=True)
        await callback.answer()
        return

    parsed = [p for p in parse_message(_entry_text(original.text or ""), dt.date.today()) if not p.is_income]
    if not parsed:
        await callback.answer("Не получилось разобрать трату", show_alert=True)
        return

    user = callback.from_user
    async with async_session_maker() as session:
        if action == "split":
            chat = await session.get(Chat, callback.message.chat.id)
            payer = await get_or_create_member(session, chat.id, user.id, user.username, user.full_name)
            members = await active_members(session, chat.id) or [payer]
        else:
            chat, payer = await get_or_create_personal_space(session, user.id, user.username, user.full_name)
            members = [payer]
        expenses = [await add_split_expense(session, chat, payer, members, p) for p in parsed]
        await session.commit()

        header = _header(chat, len(members) if action == "split" else None)
        if action == "split":
            header += f" (платит {escape(payer.full_name)})"
        body = "\n".join(_line(e, chat.currency) for e in expenses)
        if len(expenses) == 1:
            markup = _single_markup(expenses[0].id, [])
        else:
            markup = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(
                    text="↩️ Отменить все", callback_data=f"qe:undoall:{expenses[0].id}:{expenses[-1].id}"
                )]]
            )
        await _edit(callback, f"{header}\n{body}", markup)
        await callback.answer()

        alerts = []
        for expense in expenses:
            alerts.extend(await alerts_after_expense(session, expense.id))
        if alerts:
            await session.commit()
            await notify_budget_alerts(alerts)
