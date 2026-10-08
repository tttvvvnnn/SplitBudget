"""«Все траты» — GET /all/expenses и /all/stats: личные траты + доля в семейных."""
from __future__ import annotations

import json


def _add_expense(client, headers, chat_id, payer_id, participant_ids, amount, title, category="Еда"):
    r = client.post(
        f"/api/chats/{chat_id}/expenses",
        headers=headers,
        data={
            "title": title,
            "amount": str(amount),
            "category": category,
            "expense_date": "2026-09-10",
            "payer_member_id": str(payer_id),
            "split_type": "equal",
            "participant_ids": json.dumps(participant_ids),
        },
    )
    assert r.status_code == 201, r.text


def test_all_expenses_counts_my_share(client, seeded_chat, auth_header):
    alice = auth_header(seeded_chat.alice_init_data)
    both = [seeded_chat.alice_member_id, seeded_chat.bob_member_id]
    # Семейная трата: платила Алиса, делят поровну — доля Алисы 1500.
    _add_expense(client, alice, seeded_chat.chat_id, seeded_chat.alice_member_id, both, 3000, "Продукты")
    # Семейная трата только на Боба — у Алисы доли нет, в её «Все траты» не попадает.
    _add_expense(client, alice, seeded_chat.chat_id, seeded_chat.alice_member_id,
                 [seeded_chat.bob_member_id], 500, "Бобу")

    personal_id = client.get("/api/personal", headers=alice).json()["id"]
    me = client.get(f"/api/chats/{personal_id}/me", headers=alice).json()["member"]["id"]
    _add_expense(client, alice, personal_id, me, [me], 350, "Кофе", category="Кафе")

    r = client.get("/api/all/expenses?month=2026-09", headers=alice)
    assert r.status_code == 200, r.text
    items = {e["title"]: e for e in r.json()}
    assert set(items) == {"Продукты", "Кофе"}
    assert float(items["Продукты"]["my_share"]) == 1500.0
    assert float(items["Продукты"]["amount"]) == 3000.0
    assert items["Продукты"]["i_paid"] is True
    assert items["Продукты"]["is_personal"] is False
    assert items["Кофе"]["is_personal"] is True

    r = client.get("/api/all/stats?month=2026-09", headers=alice)
    assert r.status_code == 200, r.text
    stats = r.json()
    assert float(stats["total"]) == 1850.0
    assert {c["category"]: float(c["total"]) for c in stats["by_category"]} == {"Еда": 1500.0, "Кафе": 350.0}

    # Боб видит свою долю 1500 + 500, личных трат Алисы не видит.
    r = client.get("/api/all/stats?month=2026-09", headers=auth_header(seeded_chat.bob_init_data))
    assert float(r.json()["total"]) == 2000.0


def test_all_expenses_bad_month(client, seeded_chat, auth_header):
    r = client.get("/api/all/expenses?month=oops", headers=auth_header(seeded_chat.alice_init_data))
    assert r.status_code == 400
