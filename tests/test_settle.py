"""«Рассчитаться» в группе: /settle, кнопки «Перевёл(а)», вопрос про скрытие чата."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.bot.handlers import settle
from app.shared.database import async_session_maker
from app.shared.models import Chat, Member, Settlement


@pytest.fixture()
def sent():
    return []


def _callback(sent, chat_id, user_id, data):
    async def edit_text(text, reply_markup=None):
        sent.append((text, reply_markup))

    alerts = []

    async def answer(text=None, show_alert=False):
        alerts.append(text)

    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id), edit_text=edit_text),
        answer=answer,
        alerts=alerts,
    )


def _buttons(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _message(chat_id):
    async def _run():
        async with async_session_maker() as session:
            return await settle.settle_message(session, await session.get(Chat, chat_id))

    return asyncio.run(_run())


def _add_expense(client, headers, chat_id, payer_id, participant_ids, amount):
    r = client.post(
        f"/api/chats/{chat_id}/expenses",
        headers=headers,
        data={
            "title": "Шашлык", "amount": str(amount), "category": "Продукты", "expense_date": "2026-10-01",
            "payer_member_id": str(payer_id), "split_type": "equal", "participant_ids": json.dumps(participant_ids),
        },
    )
    assert r.status_code == 201, r.text


def test_settle_flow(client, seeded_chat, auth_header, sent):
    alice = auth_header(seeded_chat.alice_init_data)
    a, b = seeded_chat.alice_member_id, seeded_chat.bob_member_id
    _add_expense(client, alice, seeded_chat.chat_id, a, [a, b], 3000)

    text, markup = _message(seeded_chat.chat_id)
    assert "@bob → @alice" in text and "1 500" in text
    assert _buttons(markup) == [f"st:pay:{b}:{a}"]

    # Посторонний (не должник и не получатель) отметить не может — участник чата, но третий
    async def _third():
        async with async_session_maker() as session:
            session.add(Member(chat_id=seeded_chat.chat_id, tg_user_id=seeded_chat.bob_tg_id + 5, full_name="Вика"))
            await session.commit()

    asyncio.run(_third())
    cb = _callback(sent, seeded_chat.chat_id, seeded_chat.bob_tg_id + 5, f"st:pay:{b}:{a}")
    asyncio.run(settle.on_settle_button(cb))
    assert "только должник или получатель" in cb.alerts[-1]

    # Боб отметил перевод — долг закрыт, сообщение спрашивает про скрытие
    cb = _callback(sent, seeded_chat.chat_id, seeded_chat.bob_tg_id, f"st:pay:{b}:{a}")
    asyncio.run(settle.on_settle_button(cb))

    async def _settlements():
        async with async_session_maker() as session:
            rows = await session.execute(select(Settlement).where(Settlement.chat_id == seeded_chat.chat_id))
            return [(s.from_member_id, s.to_member_id, float(s.amount)) for s in rows.scalars()]

    assert asyncio.run(_settlements()) == [(b, a, 1500.0)]
    text, markup = sent[-1]
    assert "Все в расчёте" in text and _buttons(markup) == ["st:hide", "st:keep"]

    # Повторное нажатие старой кнопки ничего не записывает
    asyncio.run(settle.on_settle_button(_callback(sent, seeded_chat.chat_id, seeded_chat.bob_tg_id, f"st:pay:{b}:{a}")))
    assert len(asyncio.run(_settlements())) == 1

    # «Оставить» ничего не скрывает, «Убрать» — только у нажавшего
    asyncio.run(settle.on_settle_button(_callback(sent, seeded_chat.chat_id, seeded_chat.alice_tg_id, "st:keep")))
    asyncio.run(settle.on_settle_button(_callback(sent, seeded_chat.chat_id, seeded_chat.bob_tg_id, "st:hide")))
    assert [c["id"] for c in client.get("/api/my-chats", headers=alice).json()] == [seeded_chat.chat_id]
    bob = auth_header(seeded_chat.bob_init_data)
    assert client.get("/api/my-chats", headers=bob).json() == []


def test_settle_up_button_sends_to_chat(client, seeded_chat, auth_header, monkeypatch):
    sent = []

    async def send_message(chat_id, text, reply_markup=None):
        sent.append((chat_id, text))

    monkeypatch.setattr("app.api.routers.balances.bot", SimpleNamespace(send_message=send_message))
    r = client.post(f"/api/chats/{seeded_chat.chat_id}/settle-up", headers=auth_header(seeded_chat.alice_init_data))
    assert r.status_code == 204, r.text
    assert sent and sent[0][0] == seeded_chat.chat_id and "Все в расчёте" in sent[0][1]
