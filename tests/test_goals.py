"""Цели накоплений."""
from __future__ import annotations

import datetime as dt

from app.shared.goals import months_left
from app.shared.obligations import month_of

TODAY = dt.date.today()


def test_months_left():
    assert months_left(dt.date(2026, 10, 9), dt.date(2026, 10, 31)) == 1
    assert months_left(dt.date(2026, 10, 9), dt.date(2027, 3, 31)) == 6
    assert months_left(dt.date(2026, 10, 9), dt.date(2026, 1, 31)) == 1  # срок прошёл


def test_goal_progress_and_free_money(client, seeded_chat, auth_header):
    h = auth_header(seeded_chat.alice_init_data)
    pid = client.get("/api/personal", headers=h).json()["id"]
    deadline = dt.date(TODAY.year + 1, TODAY.month, 28)  # через 12 месяцев → 13 месяцев с текущим
    r = client.post(f"/api/chats/{pid}/goals", headers=h, json={"title": "Отпуск", "target": 130000, "deadline": deadline.isoformat()})
    assert r.status_code == 201, r.text
    goal = r.json()
    assert goal["months_left"] == 13 and float(goal["monthly_needed"]) == 10000.0

    r = client.post(f"/api/chats/{pid}/goals/{goal['id']}/deposits", headers=h, json={"amount": 10000})
    goal = r.json()
    # Отложенное в этом месяце засчитывается в текущий месяц — план не меняется
    assert float(goal["saved"]) == 10000.0 and float(goal["monthly_needed"]) == 10000.0
    r = client.post(f"/api/chats/{pid}/goals/{goal['id']}/deposits", headers=h, json={"amount": -2500})
    assert float(r.json()["saved"]) == 7500.0

    client.post(f"/api/chats/{pid}/income", headers=h, json={"title": "Зарплата", "amount": 50000})
    data = client.get(f"/api/chats/{pid}/income?month={month_of(TODAY)}", headers=h).json()
    assert float(data["saved"]) == 7500.0 and float(data["free"]) == 50000.0 - 7500.0

    deposit_id = r.json()["deposits"][0]["id"]
    r = client.delete(f"/api/chats/{pid}/goals/{goal['id']}/deposits/{deposit_id}", headers=h)
    assert float(r.json()["saved"]) == 10000.0


def test_goals_private(client, seeded_chat, auth_header):
    h = auth_header(seeded_chat.alice_init_data)
    assert client.get(f"/api/chats/{seeded_chat.chat_id}/goals", headers=h).status_code == 400
    pid = client.get("/api/personal", headers=h).json()["id"]
    goal = client.post(f"/api/chats/{pid}/goals", headers=h, json={"title": "Машина", "target": 1000000}).json()
    assert goal["monthly_needed"] is None
    bob = auth_header(seeded_chat.bob_init_data)
    assert client.get(f"/api/chats/{pid}/goals", headers=bob).status_code == 403
    bob_pid = client.get("/api/personal", headers=bob).json()["id"]
    r = client.post(f"/api/chats/{bob_pid}/goals/{goal['id']}/deposits", headers=bob, json={"amount": 5})
    assert r.status_code == 404
