"""Шаблоны повторяющихся трат: CRUD + автогенерация планировщиком + идемпотентность."""
from __future__ import annotations

import asyncio
import datetime as dt

from sqlalchemy import select

from app.bot.scheduler import generate_due_recurring_expenses
from app.shared.database import async_session_maker
from app.shared.models import Expense, RecurringExpense


def test_create_recurring(client, seeded_chat, auth_header):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/recurring",
        headers=auth_header(seeded_chat.alice_init_data),
        json={
            "title": "Аренда",
            "amount": 40000,
            "category": "ЖКХ",
            "payer_member_id": seeded_chat.alice_member_id,
            "split_type": "equal",
            "day_of_month": 1,
            "participants": [
                {"member_id": seeded_chat.alice_member_id},
                {"member_id": seeded_chat.bob_member_id},
            ],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_active"] is True
    assert len(body["participants"]) == 2


def test_update_and_deactivate_recurring(client, seeded_chat, auth_header):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/recurring",
        headers=auth_header(seeded_chat.alice_init_data),
        json={
            "title": "Подписка",
            "amount": 500,
            "category": "Подписки",
            "payer_member_id": seeded_chat.alice_member_id,
            "split_type": "equal",
            "day_of_month": 5,
            "participants": [{"member_id": seeded_chat.alice_member_id}],
        },
    )
    recurring_id = r.json()["id"]

    r = client.patch(
        f"/api/chats/{seeded_chat.chat_id}/recurring/{recurring_id}",
        headers=auth_header(seeded_chat.alice_init_data),
        json={"is_active": False, "amount": 600},
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False
    assert float(r.json()["amount"]) == 600.0


def test_delete_recurring(client, seeded_chat, auth_header):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/recurring",
        headers=auth_header(seeded_chat.alice_init_data),
        json={
            "title": "Разовое",
            "amount": 100,
            "category": "Другое",
            "payer_member_id": seeded_chat.alice_member_id,
            "split_type": "equal",
            "day_of_month": 10,
            "participants": [{"member_id": seeded_chat.alice_member_id}],
        },
    )
    recurring_id = r.json()["id"]

    r = client.delete(
        f"/api/chats/{seeded_chat.chat_id}/recurring/{recurring_id}",
        headers=auth_header(seeded_chat.alice_init_data),
    )
    assert r.status_code == 204

    r = client.get(f"/api/chats/{seeded_chat.chat_id}/recurring", headers=auth_header(seeded_chat.alice_init_data))
    assert all(item["id"] != recurring_id for item in r.json())


def test_recurring_generation_and_idempotency(client, seeded_chat, auth_header):
    today = dt.date.today()

    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/recurring",
        headers=auth_header(seeded_chat.alice_init_data),
        json={
            "title": "Аренда",
            "amount": 40000,
            "category": "ЖКХ",
            "payer_member_id": seeded_chat.alice_member_id,
            "split_type": "equal",
            "day_of_month": today.day,
            "participants": [
                {"member_id": seeded_chat.alice_member_id},
                {"member_id": seeded_chat.bob_member_id},
            ],
        },
    )
    recurring_id = r.json()["id"]

    asyncio.run(generate_due_recurring_expenses())

    async def _count_generated():
        async with async_session_maker() as session:
            result = await session.execute(select(Expense).where(Expense.recurring_id == recurring_id))
            return len(result.scalars().all())

    assert asyncio.run(_count_generated()) == 1

    # повторный прогон в тот же день не должен создать дубликат
    asyncio.run(generate_due_recurring_expenses())
    assert asyncio.run(_count_generated()) == 1


def test_recurring_custom_split_generation(client, seeded_chat, auth_header):
    today = dt.date.today()

    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/recurring",
        headers=auth_header(seeded_chat.alice_init_data),
        json={
            "title": "Коммуналка",
            "amount": 300,
            "category": "ЖКХ",
            "payer_member_id": seeded_chat.bob_member_id,
            "split_type": "custom",
            "day_of_month": today.day,
            "participants": [
                {"member_id": seeded_chat.alice_member_id, "custom_amount": 100},
                {"member_id": seeded_chat.bob_member_id, "custom_amount": 200},
            ],
        },
    )
    assert r.status_code == 201, r.text
    recurring_id = r.json()["id"]

    asyncio.run(generate_due_recurring_expenses())

    async def _get_generated():
        async with async_session_maker() as session:
            result = await session.execute(select(Expense).where(Expense.recurring_id == recurring_id))
            return result.scalars().one()

    generated = asyncio.run(_get_generated())
    assert float(generated.amount) == 300.0


def test_recurring_wrong_day_not_generated_today(client, seeded_chat, auth_header):
    today = dt.date.today()
    other_day = (today.day % 28) + 1  # гарантированно другой день, но всё ещё валидный (1..28)
    if other_day == today.day:
        other_day = other_day % 28 + 1

    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/recurring",
        headers=auth_header(seeded_chat.alice_init_data),
        json={
            "title": "Не сегодня",
            "amount": 100,
            "category": "Другое",
            "payer_member_id": seeded_chat.alice_member_id,
            "split_type": "equal",
            "day_of_month": other_day,
            "participants": [{"member_id": seeded_chat.alice_member_id}],
        },
    )
    recurring_id = r.json()["id"]

    asyncio.run(generate_due_recurring_expenses())

    async def _count_generated():
        async with async_session_maker() as session:
            result = await session.execute(select(Expense).where(Expense.recurring_id == recurring_id))
            return len(result.scalars().all())

    assert asyncio.run(_count_generated()) == 0
