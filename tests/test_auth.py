"""Проверка аутентификации по Telegram.WebApp.initData."""
from __future__ import annotations


def test_missing_init_data_rejected(client, seeded_chat):
    r = client.get(f"/api/chats/{seeded_chat.chat_id}/me")
    assert r.status_code in (401, 422)


def test_tampered_init_data_rejected(client, seeded_chat, auth_header):
    bad = seeded_chat.alice_init_data[:-4] + "0000"
    r = client.get(f"/api/chats/{seeded_chat.chat_id}/me", headers=auth_header(bad))
    assert r.status_code == 401


def test_valid_init_data_accepted(client, seeded_chat, auth_header):
    r = client.get(f"/api/chats/{seeded_chat.chat_id}/me", headers=auth_header(seeded_chat.alice_init_data))
    assert r.status_code == 200
    body = r.json()
    assert body["member"]["username"] == "alice"
    assert len(body["members"]) == 2


def test_unknown_chat_rejected(client, seeded_chat, auth_header):
    r = client.get("/api/chats/-1/me", headers=auth_header(seeded_chat.alice_init_data))
    assert r.status_code == 404


def test_static_webapp_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Семейные траты" in r.text


def test_healthz_reports_version(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "version" in r.json()
