"""Лимиты: расчёт потраченного и уведомления при 80% / 100%."""
from __future__ import annotations

import datetime as dt
import json

import pytest

TODAY = dt.date.today()
MONTH = TODAY.strftime("%Y-%m")


@pytest.fixture()
def sent(monkeypatch):
    messages: list[tuple[int, str]] = []

    async def fake(alerts):
        messages.extend(alerts)

    monkeypatch.setattr("app.api.routers.expenses.notify_budget_alerts", fake)
    return messages


def _expense(client, chat_id, headers, payer_id, participants, amount, category, subcategory="", date=TODAY):
    r = client.post(
        f"/api/chats/{chat_id}/expenses",
        headers=headers,
        data={
            "title": "Покупка",
            "amount": str(amount),
            "category": category,
            "subcategory": subcategory,
            "expense_date": date.isoformat(),
            "payer_member_id": str(payer_id),
            "split_type": "equal",
            "participant_ids": json.dumps(participants),
        },
    )
    assert r.status_code == 201, r.text


def _budgets(client, chat_id, headers):
    r = client.get(f"/api/chats/{chat_id}/budgets?month={MONTH}", headers=headers)
    assert r.status_code == 200, r.text
    return {b["label"]: b for b in r.json()}


def test_family_budget_spent_and_alerts(client, seeded_chat, auth_header, sent):
    h = auth_header(seeded_chat.alice_init_data)
    cid = seeded_chat.chat_id
    alice = seeded_chat.alice_member_id
    for payload in (
        {"category": "", "amount": "10000"},
        {"category": "Продукты", "amount": "1000"},
        {"category": "Продукты", "subcategory": "Алкоголь", "amount": "500"},
    ):
        r = client.post(f"/api/chats/{cid}/budgets", headers=h, json=payload)
        assert r.status_code == 201, r.text

    _expense(client, cid, h, alice, [alice], 300, "Продукты", "Супермаркет")
    assert sent == []
    _expense(client, cid, h, alice, [alice], 420, "Продукты", "Алкоголь")  # алкоголь 84%
    assert [chat for chat, _ in sent] == [cid]
    assert "Продукты › Алкоголь" in sent[0][1] and "84%" in sent[0][1]

    sent.clear()
    _expense(client, cid, h, alice, [alice], 400, "Продукты", "Алкоголь")  # алкоголь 164%, продукты 112%
    texts = " | ".join(t for _, t in sent)
    assert "Алкоголь» превышен" in texts and "«Продукты» превышен" in texts

    sent.clear()
    _expense(client, cid, h, alice, [alice], 10, "Продукты", "Алкоголь")  # уже уведомляли — тишина
    assert sent == []

    b = _budgets(client, cid, h)
    assert float(b["Весь месяц"]["spent"]) == 1130.0
    assert float(b["Продукты"]["spent"]) == 1130.0
    assert float(b["Продукты › Алкоголь"]["spent"]) == 830.0
    assert list(b)[0] == "Весь месяц"


def test_personal_budget_counts_family_share_and_dms_owner(client, seeded_chat, auth_header, sent):
    alice_h = auth_header(seeded_chat.alice_init_data)
    personal = client.get("/api/personal", headers=alice_h).json()["id"]
    assert personal == seeded_chat.alice_tg_id
    client.post(f"/api/chats/{personal}/budgets", headers=alice_h, json={"category": "Кафе и рестораны", "amount": "1000"})

    both = [seeded_chat.alice_member_id, seeded_chat.bob_member_id]
    # Семейная трата 1800 на двоих — доля Алисы 900 = 90% её личного лимита → в личку Алисе
    _expense(client, seeded_chat.chat_id, auth_header(seeded_chat.bob_init_data),
             seeded_chat.bob_member_id, both, 1800, "Кафе и рестораны")
    assert sent and sent[0][0] == seeded_chat.alice_tg_id
    assert "Мои финансы" in sent[0][1] and "90%" in sent[0][1]
    assert float(_budgets(client, personal, alice_h)["Кафе и рестораны"]["spent"]) == 900.0


def test_old_month_expense_does_not_alert(client, seeded_chat, auth_header, sent):
    h = auth_header(seeded_chat.alice_init_data)
    client.post(f"/api/chats/{seeded_chat.chat_id}/budgets", headers=h, json={"category": "Дом", "amount": "100"})
    _expense(client, seeded_chat.chat_id, h, seeded_chat.alice_member_id, [seeded_chat.alice_member_id],
             500, "Дом", date=TODAY - dt.timedelta(days=62))
    assert sent == []


def test_upsert_changes_amount_and_resets_alerts(client, seeded_chat, auth_header, sent):
    h = auth_header(seeded_chat.alice_init_data)
    cid = seeded_chat.chat_id
    me = seeded_chat.alice_member_id
    client.post(f"/api/chats/{cid}/budgets", headers=h, json={"category": "Дети", "amount": "100"})
    _expense(client, cid, h, me, [me], 90, "Дети")
    assert len(sent) == 1
    r = client.post(f"/api/chats/{cid}/budgets", headers=h, json={"category": "Дети", "amount": "200"})
    assert r.status_code == 201
    assert len(_budgets(client, cid, h)) == 1  # обновили, а не создали второй
    sent.clear()
    _expense(client, cid, h, me, [me], 80, "Дети")  # 170 из 200 = 85% — снова уведомляем
    assert len(sent) == 1

    budget_id = _budgets(client, cid, h)["Дети"]["id"]
    assert client.delete(f"/api/chats/{cid}/budgets/{budget_id}", headers=h).status_code == 204
    assert _budgets(client, cid, h) == {}


def test_budget_is_private_to_chat(client, seeded_chat, auth_header):
    owner_h = auth_header(seeded_chat.alice_init_data)
    personal = client.get("/api/personal", headers=owner_h).json()["id"]
    r = client.get(f"/api/chats/{personal}/budgets?month={MONTH}", headers=auth_header(seeded_chat.bob_init_data))
    assert r.status_code == 403
