"""CRUD трат: создание, равный/ручной сплит, редактирование, удаление, округление."""
from __future__ import annotations

import asyncio
import json

from app.shared.database import async_session_maker
from app.shared.models import Member


def test_create_equal_split(client, seeded_chat, auth_header):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/expenses",
        headers=auth_header(seeded_chat.alice_init_data),
        data={
            "title": "Продукты",
            "amount": "1000",
            "category": "Еда",
            "expense_date": "2026-09-01",
            "payer_member_id": str(seeded_chat.alice_member_id),
            "split_type": "equal",
            "participant_ids": json.dumps([seeded_chat.alice_member_id, seeded_chat.bob_member_id]),
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["shares"]) == 2
    assert sum(float(s["amount"]) for s in body["shares"]) == 1000.0
    assert all(float(s["amount"]) == 500.0 for s in body["shares"])


def test_equal_split_rounding_remainder(client, seeded_chat, auth_header):
    """100 / 3 участника не делится ровно (33.33...) — сумма долей всё равно должна давать 100.00
    ровно, без потери копейки на округлении."""

    async def _add_charlie():
        async with async_session_maker() as session:
            charlie = Member(
                chat_id=seeded_chat.chat_id, tg_user_id=seeded_chat.bob_tg_id + 1,
                username="charlie", full_name="Чарли",
            )
            session.add(charlie)
            await session.commit()
            return charlie.id

    charlie_id = asyncio.run(_add_charlie())
    member_ids = [seeded_chat.alice_member_id, seeded_chat.bob_member_id, charlie_id]

    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/expenses",
        headers=auth_header(seeded_chat.alice_init_data),
        data={
            "title": "Такси",
            "amount": "100",
            "category": "Транспорт",
            "expense_date": "2026-09-02",
            "payer_member_id": str(seeded_chat.alice_member_id),
            "split_type": "equal",
            "participant_ids": json.dumps(member_ids),
        },
    )
    assert r.status_code == 201, r.text
    shares = r.json()["shares"]
    total = sum(float(s["amount"]) for s in shares)
    assert total == 100.0


def test_create_custom_split(client, seeded_chat, auth_header):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/expenses",
        headers=auth_header(seeded_chat.alice_init_data),
        data={
            "title": "Такси",
            "amount": "300",
            "category": "Транспорт",
            "expense_date": "2026-09-01",
            "payer_member_id": str(seeded_chat.bob_member_id),
            "split_type": "custom",
            "custom_shares": json.dumps(
                [
                    {"member_id": seeded_chat.alice_member_id, "amount": 100},
                    {"member_id": seeded_chat.bob_member_id, "amount": 200},
                ]
            ),
        },
    )
    assert r.status_code == 201, r.text
    shares = {s["member_id"]: float(s["amount"]) for s in r.json()["shares"]}
    assert shares[seeded_chat.alice_member_id] == 100.0
    assert shares[seeded_chat.bob_member_id] == 200.0


def test_custom_split_amount_mismatch_rejected(client, seeded_chat, auth_header):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/expenses",
        headers=auth_header(seeded_chat.alice_init_data),
        data={
            "title": "Ошибка",
            "amount": "300",
            "category": "Транспорт",
            "expense_date": "2026-09-01",
            "payer_member_id": str(seeded_chat.bob_member_id),
            "split_type": "custom",
            "custom_shares": json.dumps([{"member_id": seeded_chat.alice_member_id, "amount": 100}]),
        },
    )
    assert r.status_code == 400


def test_update_and_delete_expense(client, seeded_chat, auth_header):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/expenses",
        headers=auth_header(seeded_chat.alice_init_data),
        data={
            "title": "Продукты",
            "amount": "1000",
            "category": "Еда",
            "expense_date": "2026-09-01",
            "payer_member_id": str(seeded_chat.alice_member_id),
            "split_type": "equal",
            "participant_ids": json.dumps([seeded_chat.alice_member_id, seeded_chat.bob_member_id]),
        },
    )
    expense_id = r.json()["id"]

    r = client.patch(
        f"/api/chats/{seeded_chat.chat_id}/expenses/{expense_id}",
        headers=auth_header(seeded_chat.alice_init_data),
        data={"amount": "900", "participant_ids": json.dumps([seeded_chat.alice_member_id, seeded_chat.bob_member_id])},
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert float(updated["amount"]) == 900.0
    assert sum(float(s["amount"]) for s in updated["shares"]) == 900.0

    r = client.delete(
        f"/api/chats/{seeded_chat.chat_id}/expenses/{expense_id}",
        headers=auth_header(seeded_chat.alice_init_data),
    )
    assert r.status_code == 204

    r = client.get(
        f"/api/chats/{seeded_chat.chat_id}/expenses?month=2026-09",
        headers=auth_header(seeded_chat.alice_init_data),
    )
    assert all(e["id"] != expense_id for e in r.json())


def test_month_filter(client, seeded_chat, auth_header):
    for month_date in ("2026-08-15", "2026-09-15"):
        client.post(
            f"/api/chats/{seeded_chat.chat_id}/expenses",
            headers=auth_header(seeded_chat.alice_init_data),
            data={
                "title": f"Трата {month_date}",
                "amount": "10",
                "category": "Другое",
                "expense_date": month_date,
                "payer_member_id": str(seeded_chat.alice_member_id),
                "split_type": "equal",
                "participant_ids": json.dumps([seeded_chat.alice_member_id]),
            },
        )

    r = client.get(
        f"/api/chats/{seeded_chat.chat_id}/expenses?month=2026-09",
        headers=auth_header(seeded_chat.alice_init_data),
    )
    titles = [e["title"] for e in r.json()]
    assert "Трата 2026-09-15" in titles
    assert "Трата 2026-08-15" not in titles
