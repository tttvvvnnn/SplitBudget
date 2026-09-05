"""Участники без Telegram-аккаунта (добавляются вручную) — POST /chats/{id}/members."""
from __future__ import annotations

import json


def test_add_manual_member(client, seeded_chat, auth_header):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/members",
        headers=auth_header(seeded_chat.alice_init_data),
        json={"full_name": "Бабушка"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["full_name"] == "Бабушка"
    assert body["tg_user_id"] is None
    assert body["username"] is None
    assert body["is_active"] is True
    assert body["is_manual"] is True
    assert body["avatar_url"] is None


def test_manual_member_appears_in_me_and_members(client, seeded_chat, auth_header):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/members",
        headers=auth_header(seeded_chat.alice_init_data),
        json={"full_name": "Дедушка"},
    )
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]

    r = client.get(f"/api/chats/{seeded_chat.chat_id}/me", headers=auth_header(seeded_chat.alice_init_data))
    assert r.status_code == 200, r.text
    ids = {m["id"] for m in r.json()["members"]}
    assert new_id in ids

    r = client.get(f"/api/chats/{seeded_chat.chat_id}/members", headers=auth_header(seeded_chat.alice_init_data))
    assert r.status_code == 200, r.text
    manual = [m for m in r.json() if m["id"] == new_id]
    assert len(manual) == 1
    assert manual[0]["is_manual"] is True

    # существующие Telegram-участники не считаются "ручными"
    existing = [m for m in r.json() if m["id"] == seeded_chat.alice_member_id]
    assert existing[0]["is_manual"] is False


def test_manual_member_requires_nonempty_name(client, seeded_chat, auth_header):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/members",
        headers=auth_header(seeded_chat.alice_init_data),
        json={"full_name": ""},
    )
    assert r.status_code == 422


def test_manual_member_can_be_used_in_expense(client, seeded_chat, auth_header):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/members",
        headers=auth_header(seeded_chat.alice_init_data),
        json={"full_name": "Гость"},
    )
    assert r.status_code == 201, r.text
    guest_id = r.json()["id"]

    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/expenses",
        headers=auth_header(seeded_chat.alice_init_data),
        data={
            "title": "Ужин",
            "amount": "900",
            "category": "Еда",
            "expense_date": "2026-09-04",
            "payer_member_id": str(seeded_chat.alice_member_id),
            "split_type": "equal",
            "participant_ids": json.dumps([seeded_chat.alice_member_id, guest_id]),
        },
    )
    assert r.status_code == 201, r.text

    r = client.get(f"/api/chats/{seeded_chat.chat_id}/balances", headers=auth_header(seeded_chat.alice_init_data))
    net = {b["member_id"]: float(b["net"]) for b in r.json()["balances"]}
    assert net[guest_id] == -450.0
    assert net[seeded_chat.alice_member_id] == 450.0


def test_rename_manual_member(client, seeded_chat, auth_header):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/members",
        headers=auth_header(seeded_chat.alice_init_data),
        json={"full_name": "Бабуля"},
    )
    member_id = r.json()["id"]

    r = client.patch(
        f"/api/chats/{seeded_chat.chat_id}/members/{member_id}",
        headers=auth_header(seeded_chat.alice_init_data),
        json={"full_name": "Бабушка Валя"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["full_name"] == "Бабушка Валя"

    r = client.get(f"/api/chats/{seeded_chat.chat_id}/members", headers=auth_header(seeded_chat.alice_init_data))
    renamed = [m for m in r.json() if m["id"] == member_id][0]
    assert renamed["full_name"] == "Бабушка Валя"


def test_cannot_rename_telegram_member(client, seeded_chat, auth_header):
    r = client.patch(
        f"/api/chats/{seeded_chat.chat_id}/members/{seeded_chat.alice_member_id}",
        headers=auth_header(seeded_chat.alice_init_data),
        json={"full_name": "Кто-то другой"},
    )
    assert r.status_code == 400


def test_delete_unused_manual_member(client, seeded_chat, auth_header):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/members",
        headers=auth_header(seeded_chat.alice_init_data),
        json={"full_name": "Лишний"},
    )
    member_id = r.json()["id"]

    r = client.delete(
        f"/api/chats/{seeded_chat.chat_id}/members/{member_id}",
        headers=auth_header(seeded_chat.alice_init_data),
    )
    assert r.status_code == 204, r.text

    r = client.get(f"/api/chats/{seeded_chat.chat_id}/members", headers=auth_header(seeded_chat.alice_init_data))
    assert member_id not in {m["id"] for m in r.json()}


def test_cannot_delete_manual_member_with_expenses(client, seeded_chat, auth_header):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/members",
        headers=auth_header(seeded_chat.alice_init_data),
        json={"full_name": "Гость2"},
    )
    guest_id = r.json()["id"]

    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/expenses",
        headers=auth_header(seeded_chat.alice_init_data),
        data={
            "title": "Такси",
            "amount": "300",
            "category": "Транспорт",
            "expense_date": "2026-09-04",
            "payer_member_id": str(seeded_chat.alice_member_id),
            "split_type": "equal",
            "participant_ids": json.dumps([seeded_chat.alice_member_id, guest_id]),
        },
    )
    assert r.status_code == 201, r.text

    r = client.delete(
        f"/api/chats/{seeded_chat.chat_id}/members/{guest_id}",
        headers=auth_header(seeded_chat.alice_init_data),
    )
    assert r.status_code == 400

    r = client.get(f"/api/chats/{seeded_chat.chat_id}/members", headers=auth_header(seeded_chat.alice_init_data))
    assert guest_id in {m["id"] for m in r.json()}


def test_cannot_delete_telegram_member(client, seeded_chat, auth_header):
    r = client.delete(
        f"/api/chats/{seeded_chat.chat_id}/members/{seeded_chat.bob_member_id}",
        headers=auth_header(seeded_chat.alice_init_data),
    )
    assert r.status_code == 400
