"""Личное пространство «Мои финансы» — GET /personal и работа с ним через /chats/{id}/..."""
from __future__ import annotations

import json

from tests.conftest import make_init_data

PERSONAL_TG_ID = 777000001


def _init(tg_id: int = PERSONAL_TG_ID) -> str:
    return make_init_data({"id": tg_id, "username": f"user{tg_id}", "first_name": "Тихон"})


def test_personal_space_is_created_once(client, auth_header):
    r1 = client.get("/api/personal", headers=auth_header(_init()))
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["id"] == PERSONAL_TG_ID
    assert body["is_personal"] is True
    assert body["title"] == "Мои финансы"

    r2 = client.get("/api/personal", headers=auth_header(_init()))
    assert r2.json() == body

    r = client.get(f"/api/chats/{PERSONAL_TG_ID}/me", headers=auth_header(_init()))
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["chat"]["is_personal"] is True
    assert [m["id"] for m in me["members"]] == [me["member"]["id"]]


def test_personal_expense_crud_and_stats(client, auth_header):
    tg_id = PERSONAL_TG_ID + 1
    headers = auth_header(_init(tg_id))
    chat_id = client.get("/api/personal", headers=headers).json()["id"]
    me = client.get(f"/api/chats/{chat_id}/me", headers=headers).json()["member"]

    r = client.post(
        f"/api/chats/{chat_id}/expenses",
        headers=headers,
        data={
            "title": "Кофе",
            "amount": "350",
            "category": "Кафе",
            "expense_date": "2026-10-05",
            "payer_member_id": str(me["id"]),
            "split_type": "equal",
            "participant_ids": json.dumps([me["id"]]),
        },
    )
    assert r.status_code == 201, r.text

    r = client.get(f"/api/chats/{chat_id}/stats?month=2026-10", headers=headers)
    assert r.status_code == 200, r.text
    assert float(r.json()["total"]) == 350.0


def test_personal_space_is_private(client, seeded_chat, auth_header):
    owner = PERSONAL_TG_ID + 2
    chat_id = client.get("/api/personal", headers=auth_header(_init(owner))).json()["id"]

    r = client.get(f"/api/chats/{chat_id}/me", headers=auth_header(seeded_chat.alice_init_data))
    assert r.status_code == 403

    r = client.get(f"/api/chats/{chat_id}/expenses", headers=auth_header(seeded_chat.alice_init_data))
    assert r.status_code == 403


def test_personal_space_not_in_my_chats(client, seeded_chat, auth_header):
    headers = auth_header(seeded_chat.alice_init_data)
    client.get("/api/personal", headers=headers)
    r = client.get("/api/my-chats", headers=headers)
    assert r.status_code == 200, r.text
    ids = [c["id"] for c in r.json()]
    assert ids == [seeded_chat.chat_id]


def test_no_manual_members_in_personal_space(client, auth_header):
    tg_id = PERSONAL_TG_ID + 3
    headers = auth_header(_init(tg_id))
    chat_id = client.get("/api/personal", headers=headers).json()["id"]
    r = client.post(f"/api/chats/{chat_id}/members", headers=headers, json={"full_name": "Бабушка"})
    assert r.status_code == 400


def test_hide_and_unhide_family_chat(client, seeded_chat, auth_header):
    alice = auth_header(seeded_chat.alice_init_data)
    bob = auth_header(seeded_chat.bob_init_data)
    chat_id = seeded_chat.chat_id
    client.get("/api/personal", headers=alice)

    r = client.post(f"/api/chats/{chat_id}/hide", headers=alice)
    assert r.status_code == 204, r.text
    assert client.get("/api/my-chats", headers=alice).json() == []
    assert [c["id"] for c in client.get("/api/my-chats?hidden=true", headers=alice).json()] == [chat_id]
    # Только для Алисы: у Боба чат на месте, сам чат по-прежнему открывается
    assert [c["id"] for c in client.get("/api/my-chats", headers=bob).json()] == [chat_id]
    assert client.get(f"/api/chats/{chat_id}/me", headers=alice).status_code == 200

    r = client.post(f"/api/chats/{chat_id}/unhide", headers=alice)
    assert r.status_code == 204, r.text
    assert [c["id"] for c in client.get("/api/my-chats", headers=alice).json()] == [chat_id]
    assert client.get("/api/my-chats?hidden=true", headers=alice).json() == []


def test_personal_space_cannot_be_hidden(client, auth_header):
    tg_id = PERSONAL_TG_ID + 4
    headers = auth_header(_init(tg_id))
    chat_id = client.get("/api/personal", headers=headers).json()["id"]
    assert client.post(f"/api/chats/{chat_id}/hide", headers=headers).status_code == 400
