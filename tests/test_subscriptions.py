"""Подписки, найденные по истории трат, и обязательный платёж из них."""
from __future__ import annotations

import asyncio
import datetime as dt
import json

from app.shared import obligations as ob
from app.shared.database import async_session_maker
from app.shared.models import Chat, RecurringExpense
from app.shared.subscriptions import due_subscription_asks

PERSONAL_TG_ID = 990000500


def _months_back(n: int) -> dt.date:
    today = dt.date.today()
    index = today.year * 12 + today.month - 1 - n
    return dt.date(index // 12, index % 12 + 1, min(today.day, 5))


def _add(client, headers, chat_id, me, title, amount, date):
    r = client.post(
        f"/api/chats/{chat_id}/expenses",
        headers=headers,
        data={
            "title": title, "amount": str(amount), "category": "Подписки", "expense_date": date.isoformat(),
            "payer_member_id": str(me), "split_type": "equal", "participant_ids": json.dumps([me]),
        },
    )
    assert r.status_code == 201, r.text


def test_subscription_hints(client, auth_header):
    from tests.conftest import make_init_data

    headers = auth_header(make_init_data({"id": PERSONAL_TG_ID, "first_name": "Тест"}))
    chat_id = client.get("/api/personal", headers=headers).json()["id"]
    me = client.get(f"/api/chats/{chat_id}/me", headers=headers).json()["member"]["id"]
    for n in range(4):
        _add(client, headers, chat_id, me, "Яндекс Плюс", 399, _months_back(n))  # 4 месяца подряд
        for _ in range(3):
            _add(client, headers, chat_id, me, "Пятёрочка", 500, _months_back(n))  # слишком часто
    for n in range(3):
        _add(client, headers, chat_id, me, "Спортзал", 2000 + n * 1000, _months_back(n))  # суммы скачут
    for n in range(2):
        _add(client, headers, chat_id, me, "Кино", 600, _months_back(n))  # только 2 месяца
    for n in (0, 1, 2):
        _add(client, headers, chat_id, me, "Netflix", 999, _months_back(n))

    hints = client.get(f"/api/chats/{chat_id}/subscription-hints", headers=headers).json()
    assert sorted(h["title"] for h in hints) == ["Netflix", "Яндекс Плюс"]
    assert next(h for h in hints if h["title"] == "Яндекс Плюс")["months"] == 4

    # Бот спрашивает про каждую один раз
    async def _asks():
        async with async_session_maker() as session:
            out = await due_subscription_asks(session, dt.date.today())
            await session.commit()
            return [n for n in out if n.chat_id == chat_id]

    asks = asyncio.run(_asks())
    assert len(asks) == 2 and all(n.buttons for n in asks)
    assert asyncio.run(_asks()) == []

    # «Не подписка» — больше не предлагаем
    r = client.post(f"/api/chats/{chat_id}/subscription-hints/dismiss", headers=headers, json={"keyword": "netflix"})
    assert r.status_code == 204
    # «Сделать платежом» — подписка, а трата этого месяца засчитана оплатой
    r = client.post(f"/api/chats/{chat_id}/subscription-hints/accept", headers=headers, json={"keyword": "яндекс плюс"})
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "subscription" and float(r.json()["amount"]) == 399.0
    assert client.get(f"/api/chats/{chat_id}/subscription-hints", headers=headers).json() == []

    async def _payment():
        async with async_session_maker() as session:
            chat = await session.get(Chat, chat_id)
            items = await ob.obligations(session, chat, ob.month_of(dt.date.today()))
            await session.commit()
            return [(o.recurring.title, o.payment.status) for o in items if isinstance(o.recurring, RecurringExpense)]

    assert asyncio.run(_payment()) == [("Яндекс Плюс", "paid")]


def test_parallel_first_payment(client, auth_header):
    """Несколько запросов сразу создают первый платёж месяца — без 500."""
    from tests.conftest import make_init_data

    headers = auth_header(make_init_data({"id": PERSONAL_TG_ID + 1, "first_name": "Тест"}))
    chat_id = client.get("/api/personal", headers=headers).json()["id"]
    me = client.get(f"/api/chats/{chat_id}/me", headers=headers).json()["member"]["id"]
    r = client.post(
        f"/api/chats/{chat_id}/recurring", headers=headers,
        json={"title": "Аренда", "amount": "1000", "category": "Дом", "day_of_month": 28, "kind": "rent",
              "payer_member_id": me, "participants": [{"member_id": me}]},
    )
    assert r.status_code == 201, r.text

    async def _one():
        async with async_session_maker() as session:
            chat = await session.get(Chat, chat_id)
            items = await ob.obligations(session, chat, ob.month_of(dt.date.today()))
            await session.commit()
            return len(items)

    async def _all():
        return await asyncio.gather(*(_one() for _ in range(4)))

    assert asyncio.run(_all()) == [1, 1, 1, 1]
