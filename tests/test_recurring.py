"""Шаблоны повторяющихся трат (обязательных платежей): CRUD. Платежи — tests/test_obligations.py."""
from __future__ import annotations


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
