"""Быстрый ввод трат текстом в личке с ботом."""
from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.bot.handlers import quick
from app.shared.database import async_session_maker
from app.shared.models import Expense, ExpenseShare
from app.shared.quick_entry import parse_group_message, parse_line, parse_message

TODAY = dt.date(2026, 10, 9)


@pytest.mark.parametrize(
    ("line", "title", "amount", "date"),
    [
        ("кофе 250", "Кофе", "250", TODAY),
        ("винлаб 1 800", "Винлаб", "1800", TODAY),
        ("Такси 350 вчера", "Такси", "350", TODAY - dt.timedelta(days=1)),
        ("250 шаурма", "Шаурма", "250", TODAY),
        ("продукты 1234,50 руб", "Продукты", "1234.50", TODAY),
        ("ремонт 15к", "Ремонт", "15000", TODAY),
        ("пятёрочка 2 пакета 340", "Пятёрочка 2 пакета", "340", TODAY),
        ("5ka 300", "5ka", "300", TODAY),
        ("1500", "Трата", "1500", TODAY),
    ],
)
def test_parse_line(line, title, amount, date):
    parsed = parse_line(line, TODAY)
    assert parsed is not None
    assert (parsed.title, parsed.amount, parsed.date) == (title, Decimal(amount), date)


@pytest.mark.parametrize("line", ["привет", "/start", "", "кофе 0"])
def test_parse_line_without_amount(line):
    assert parse_line(line, TODAY) is None


def test_parse_message_many_lines():
    assert [p.title for p in parse_message("кофе 250\n\nхлеб 60\nпросто текст", TODAY)] == ["Кофе", "Хлеб"]


class _Bot:
    """Запоминает ответы бота вместо отправки в Telegram."""

    def __init__(self):
        self.sent = []

    def message(self, user_id, text):
        async def answer(text, reply_markup=None):
            self.sent.append((text, reply_markup))

        return SimpleNamespace(
            from_user=SimpleNamespace(id=user_id, username="alice", full_name="Алиса"), text=text, answer=answer
        )

    def callback(self, user_id, data):
        async def edit_text(text, reply_markup=None):
            self.sent.append((text, reply_markup))

        async def edit_reply_markup(reply_markup=None):
            self.sent.append((None, reply_markup))

        async def answer(text=None, show_alert=False):
            pass

        return SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=user_id),
            message=SimpleNamespace(edit_text=edit_text, edit_reply_markup=edit_reply_markup),
            answer=answer,
        )


@pytest.fixture()
def bot(monkeypatch):
    async def silent(*args, **kwargs):
        return None

    monkeypatch.setattr(quick, "notify_new_expense", silent)
    monkeypatch.setattr(quick, "notify_budget_alerts", silent)
    return _Bot()


def _expenses(chat_id):
    async def _run():
        async with async_session_maker() as session:
            result = await session.execute(select(Expense).where(Expense.chat_id == chat_id).order_by(Expense.id))
            out = []
            for e in result.scalars().all():
                shares = await session.execute(select(ExpenseShare.amount).where(ExpenseShare.expense_id == e.id))
                out.append((e.id, e.title, float(e.amount), e.category, e.subcategory, len(shares.scalars().all())))
            return out

    return asyncio.run(_run())


def _buttons(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def test_quick_expense_with_category_fix_and_move(client, seeded_chat, bot):
    uid = seeded_chat.alice_tg_id
    asyncio.run(quick.quick_expense(bot.message(uid, "винлаб 1800")))
    [(eid, title, amount, cat, sub, _)] = _expenses(uid)
    assert (title, amount, cat, sub) == ("Винлаб", 1800.0, "Продукты", "Алкоголь")
    text, markup = bot.sent[-1]
    assert "Мои финансы" in text and f"qe:fam:{eid}:{seeded_chat.chat_id}" in _buttons(markup)

    # Поправили категорию через кнопки — бот запоминает
    asyncio.run(quick.on_quick_button(bot.callback(uid, f"qe:cat:{eid}")))
    ci = next(i for i, b in enumerate(quick.CATEGORY_TREE) if b[0] == "Подарки")
    asyncio.run(quick.on_quick_button(bot.callback(uid, f"qe:c:{eid}:{ci}")))
    assert _expenses(uid)[0][3:5] == ("Подарки", None)
    asyncio.run(quick.quick_expense(bot.message(uid, "винлаб 500")))
    assert _expenses(uid)[-1][3] == "Подарки"

    # Чужая кнопка не работает
    asyncio.run(quick.on_quick_button(bot.callback(seeded_chat.bob_tg_id, f"qe:undo:{eid}")))
    assert len(_expenses(uid)) == 2

    # Перенос в семейный чат — поровну на двоих
    asyncio.run(quick.on_quick_button(bot.callback(uid, f"qe:fam:{eid}:{seeded_chat.chat_id}")))
    assert [e[0] for e in _expenses(uid)] != [eid] and eid not in [e[0] for e in _expenses(uid)]
    moved = [e for e in _expenses(seeded_chat.chat_id) if e[0] == eid]
    assert moved and moved[0][5] == 2


def test_quick_many_and_undo_all(client, seeded_chat, bot):
    uid = seeded_chat.alice_tg_id
    asyncio.run(quick.quick_expense(bot.message(uid, "кофе 250\nхлеб 60")))
    assert [e[1] for e in _expenses(uid)] == ["Кофе", "Хлеб"]
    text, markup = bot.sent[-1]
    assert "2 трат" in text
    asyncio.run(quick.on_quick_button(bot.callback(uid, _buttons(markup)[0])))
    assert _expenses(uid) == []


def test_quick_help_without_amount(client, seeded_chat, bot):
    asyncio.run(quick.quick_expense(bot.message(seeded_chat.alice_tg_id, "привет")))
    assert "кофе 250" in bot.sent[-1][0]
    assert _expenses(seeded_chat.alice_tg_id) == []


@pytest.mark.parametrize(
    ("text", "titles"),
    [
        ("кофе 250", ["Кофе"]),
        ("такси 350 вчера", ["Такси"]),
        ("кофе 250\nхлеб 60", ["Кофе", "Хлеб"]),
        ("встретимся в 5", []),
        ("купил 2 билета на завтра в 19", []),
        ("+40000 аванс", []),
        ("кофе 250\nпривет", []),
        ("ну и цены, отдал за всё это 3500 в итоге представляешь", []),
        ("приду к 19", []),
        ("пятёрочка 2 пакета 340", []),
    ],
)
def test_parse_group_message(text, titles):
    assert [p.title for p in parse_group_message(text, TODAY)] == titles


def _group_callback(bot, chat_id, author_id, presser_id, original_text, action):
    deleted = []
    cb = bot.callback(presser_id, f"qg:{action}")

    async def delete():
        deleted.append(True)

    cb.from_user = SimpleNamespace(id=presser_id, username="alice", full_name="Алиса")
    cb.message.chat = SimpleNamespace(id=chat_id, type="group")
    cb.message.delete = delete
    cb.message.reply_to_message = SimpleNamespace(from_user=SimpleNamespace(id=author_id), text=original_text)
    return cb, deleted


def test_group_split_mine_and_refuse(client, seeded_chat, bot):
    uid, chat_id = seeded_chat.alice_tg_id, seeded_chat.chat_id

    # Чужой нажать не может
    cb, _ = _group_callback(bot, chat_id, uid, seeded_chat.bob_tg_id, "кофе 250", "split")
    asyncio.run(quick.on_group_choice(cb))
    assert _expenses(chat_id) == []

    # Поровну на всех — в семейный чат, платит автор, доли на двоих
    cb, _ = _group_callback(bot, chat_id, uid, uid, "кофе 250", "split")
    asyncio.run(quick.on_group_choice(cb))
    [(eid, title, amount, _, _, shares)] = _expenses(chat_id)
    assert (title, amount, shares) == ("Кофе", 250.0, 2)
    text, markup = bot.sent[-1]
    assert "поровну на 2" in text and "платит Алиса" in text
    assert not any(b.startswith("qe:fam") for b in _buttons(markup))

    # Категорию групповой траты можно поправить
    ci = next(i for i, b in enumerate(quick.CATEGORY_TREE) if b[0] == "Подарки")
    asyncio.run(quick.on_quick_button(bot.callback(uid, f"qe:c:{eid}:{ci}")))
    assert _expenses(chat_id)[0][3] == "Подарки"

    # В мои финансы — в личное пространство
    cb, _ = _group_callback(bot, chat_id, uid, uid, "/t хлеб 60\nмолоко 90", "mine")
    asyncio.run(quick.on_group_choice(cb))
    assert [e[1] for e in _expenses(uid)] == ["Хлеб", "Молоко"]
    assert "Мои финансы" in bot.sent[-1][0]
    assert len(_expenses(chat_id)) == 1

    # Это не трата — вопрос удаляется
    cb, deleted = _group_callback(bot, chat_id, uid, uid, "кофе 250", "no")
    asyncio.run(quick.on_group_choice(cb))
    assert deleted and len(_expenses(chat_id)) == 1
