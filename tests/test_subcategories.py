"""Подкатегории трат: сохранение, смена категории, статистика с разбивкой, дерево в /me."""
from __future__ import annotations

import json


def _create(client, seeded_chat, headers, **extra):
    data = {
        "title": "Покупка",
        "amount": "100",
        "category": "Продукты",
        "expense_date": "2026-08-05",
        "payer_member_id": str(seeded_chat.alice_member_id),
        "split_type": "equal",
        "participant_ids": json.dumps([seeded_chat.alice_member_id]),
    }
    data.update(extra)
    r = client.post(f"/api/chats/{seeded_chat.chat_id}/expenses", headers=headers, data=data)
    assert r.status_code == 201, r.text
    return r.json()


def test_category_tree(client, seeded_chat, auth_header):
    r = client.get("/api/categories", headers=auth_header(seeded_chat.alice_init_data))
    assert r.status_code == 200, r.text
    tree = {c["name"]: c for c in r.json()}
    assert "Алкоголь" in tree["Продукты"]["subcategories"]
    assert tree["Продукты"]["icon"]


def test_subcategory_saved_and_reset_on_category_change(client, seeded_chat, auth_header):
    headers = auth_header(seeded_chat.alice_init_data)
    e = _create(client, seeded_chat, headers, title="Винлаб", subcategory="Алкоголь")
    assert e["subcategory"] == "Алкоголь"

    url = f"/api/chats/{seeded_chat.chat_id}/expenses/{e['id']}"
    r = client.patch(url, headers=headers, data={"subcategory": "Супермаркет"})
    assert r.json()["subcategory"] == "Супермаркет"

    r = client.patch(url, headers=headers, data={"category": "Кафе и рестораны"})
    assert r.json()["category"] == "Кафе и рестораны"
    assert r.json()["subcategory"] is None

    r = client.patch(url, headers=headers, data={"subcategory": "Кофе"})
    assert r.json()["subcategory"] == "Кофе"
    r = client.patch(url, headers=headers, data={"subcategory": ""})
    assert r.json()["subcategory"] is None


def test_stats_breakdown_by_subcategory(client, seeded_chat, auth_header):
    headers = auth_header(seeded_chat.alice_init_data)
    _create(client, seeded_chat, headers, amount="300", subcategory="Супермаркет")
    _create(client, seeded_chat, headers, amount="200", subcategory="Алкоголь")
    _create(client, seeded_chat, headers, amount="50")

    r = client.get(f"/api/chats/{seeded_chat.chat_id}/stats?month=2026-08", headers=headers)
    assert r.status_code == 200, r.text
    (products,) = r.json()["by_category"]
    assert float(products["total"]) == 550.0
    assert [(s["subcategory"], float(s["total"])) for s in products["subcategories"]] == [
        ("Супермаркет", 300.0),
        ("Алкоголь", 200.0),
        ("", 50.0),
    ]


def test_recurring_keeps_subcategory(client, seeded_chat, auth_header):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/recurring",
        headers=auth_header(seeded_chat.alice_init_data),
        json={
            "title": "Интернет",
            "amount": "700",
            "category": "Дом",
            "subcategory": "Интернет и связь",
            "payer_member_id": seeded_chat.alice_member_id,
            "day_of_month": 5,
            "participants": [{"member_id": seeded_chat.alice_member_id}],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["subcategory"] == "Интернет и связь"
