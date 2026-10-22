"""«Все траты» — GET /all/expenses и /all/stats: личные траты + сколько потрачено в семейных."""
from __future__ import annotations

import json


def _add_expense(client, headers, chat_id, payer_id, participant_ids, amount, title, category="Еда",
                 date="2026-09-10"):
    r = client.post(
        f"/api/chats/{chat_id}/expenses",
        headers=headers,
        data={
            "title": title,
            "amount": str(amount),
            "category": category,
            "expense_date": date,
            "payer_member_id": str(payer_id),
            "split_type": "equal",
            "participant_ids": json.dumps(participant_ids),
        },
    )
    assert r.status_code == 201, r.text


def _totals(client, headers, month="2026-09"):
    r = client.get(f"/api/all/stats?month={month}", headers=headers)
    assert r.status_code == 200, r.text
    stats = r.json()
    return float(stats["total"]), {c["category"]: float(c["total"]) for c in stats["by_category"]}


def _settle(client, headers, chat_id, from_id, to_id, amount):
    r = client.post(f"/api/chats/{chat_id}/settlements", headers=headers,
                    json={"from_member_id": from_id, "to_member_id": to_id, "amount": str(amount)})
    assert r.status_code == 201, r.text


def test_all_expenses_counts_what_i_paid(client, seeded_chat, auth_header):
    alice = auth_header(seeded_chat.alice_init_data)
    bob = auth_header(seeded_chat.bob_init_data)
    a, b = seeded_chat.alice_member_id, seeded_chat.bob_member_id
    # Семейная трата: платила Алиса, делят поровну — пока Боб не вернул, все 3000 на Алисе.
    _add_expense(client, alice, seeded_chat.chat_id, a, [a, b], 3000, "Продукты")
    # Трата только на Боба, но платила Алиса — тоже её деньги, пока Боб не вернул.
    _add_expense(client, alice, seeded_chat.chat_id, a, [b], 500, "Бобу")

    personal_id = client.get("/api/personal", headers=alice).json()["id"]
    me = client.get(f"/api/chats/{personal_id}/me", headers=alice).json()["member"]["id"]
    _add_expense(client, alice, personal_id, me, [me], 350, "Кофе", category="Кафе")

    r = client.get("/api/all/expenses?month=2026-09", headers=alice)
    assert r.status_code == 200, r.text
    items = {e["title"]: e for e in r.json()}
    assert set(items) == {"Продукты", "Бобу", "Кофе"}
    assert float(items["Продукты"]["my_share"]) == 3000.0
    assert float(items["Продукты"]["amount"]) == 3000.0
    assert items["Продукты"]["i_paid"] is True
    assert items["Продукты"]["is_personal"] is False
    assert items["Кофе"]["is_personal"] is True

    assert _totals(client, alice) == (3850.0, {"Еда": 3500.0, "Кафе": 350.0})
    # «Всего» — полные суммы трат, которые Алиса оплатила или в которых участвует
    r = client.get("/api/all/stats?month=2026-09&full=true", headers=alice)
    assert float(r.json()["total"]) == 3850.0

    # Боб пока ничего не потратил: за него заплатила Алиса. Трата в списке есть, но с нулём.
    assert _totals(client, bob) == (0.0, {})
    bob_items = {e["title"]: e for e in client.get("/api/all/expenses?month=2026-09", headers=bob).json()}
    assert float(bob_items["Продукты"]["my_share"]) == 0.0 and bob_items["Продукты"]["i_paid"] is False

    # Боб вернул 1500 — закрылась самая старая трата: «Продукты» поделились пополам.
    _settle(client, bob, seeded_chat.chat_id, b, a, 1500)
    assert _totals(client, alice)[0] == 2350.0
    assert _totals(client, bob)[0] == 1500.0
    # Вернул остальное — у каждого ровно его доля.
    _settle(client, bob, seeded_chat.chat_id, b, a, 500)
    assert _totals(client, alice) == (1850.0, {"Еда": 1500.0, "Кафе": 350.0})
    assert _totals(client, bob) == (2000.0, {"Еда": 2000.0})


def test_mutual_debts_offset(client, seeded_chat, auth_header):
    """Алиса заплатила за Боба 250, Боб за Алису 150: 150 взаимно гасятся, итог у каждого —
    сколько он заплатил сам."""
    alice = auth_header(seeded_chat.alice_init_data)
    bob = auth_header(seeded_chat.bob_init_data)
    a, b = seeded_chat.alice_member_id, seeded_chat.bob_member_id
    _add_expense(client, alice, seeded_chat.chat_id, a, [a, b], 500, "Продукты", category="Еда")
    _add_expense(client, bob, seeded_chat.chat_id, b, [a, b], 300, "Такси", category="Транспорт")
    assert _totals(client, alice) == (500.0, {"Еда": 350.0, "Транспорт": 150.0})
    assert _totals(client, bob) == (300.0, {"Еда": 150.0, "Транспорт": 150.0})
    # Боб вернул остаток долга 100 — всё по долям
    _settle(client, bob, seeded_chat.chat_id, b, a, 100)
    assert _totals(client, alice) == (400.0, {"Еда": 250.0, "Транспорт": 150.0})
    assert _totals(client, bob) == (400.0, {"Еда": 250.0, "Транспорт": 150.0})


def test_repayment_recalculates_past_month(client, seeded_chat, auth_header):
    alice = auth_header(seeded_chat.alice_init_data)
    bob = auth_header(seeded_chat.bob_init_data)
    a, b = seeded_chat.alice_member_id, seeded_chat.bob_member_id
    _add_expense(client, alice, seeded_chat.chat_id, a, [a, b], 500, "Продукты", date="2026-08-20")
    assert _totals(client, alice, "2026-08")[0] == 500.0
    _settle(client, bob, seeded_chat.chat_id, b, a, 250)
    assert _totals(client, alice, "2026-08")[0] == 250.0
    assert _totals(client, bob, "2026-08")[0] == 250.0


def test_all_expenses_bad_month(client, seeded_chat, auth_header):
    r = client.get("/api/all/expenses?month=oops", headers=auth_header(seeded_chat.alice_init_data))
    assert r.status_code == 400
