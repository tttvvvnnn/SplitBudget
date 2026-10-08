"""Автоопределение категории: словарь магазинов и обучение на сохранённых тратах."""
from __future__ import annotations

import json

from app.shared.autocategory import normalize, suggest_from_dictionary


def test_normalize():
    assert normalize("Пятёрочка, у дома!") == "пятерочка у дома"


def test_dictionary():
    assert suggest_from_dictionary(normalize("Пятёрочка")).subcategory == "Супермаркет"
    s = suggest_from_dictionary(normalize("Винлаб на Ленина"))
    assert (s.category, s.subcategory) == ("Продукты", "Алкоголь")
    # самый длинный ключ важнее: «метрополитен» — транспорт, а не супермаркет «Метро»
    assert suggest_from_dictionary(normalize("Метрополитен")).category == "Транспорт"
    assert suggest_from_dictionary(normalize("что-то непонятное")) is None


def _suggest(client, seeded_chat, headers, title):
    r = client.get(
        f"/api/chats/{seeded_chat.chat_id}/category-suggestion",
        params={"title": title},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _create(client, seeded_chat, headers, title, category, subcategory=""):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/expenses",
        headers=headers,
        data={
            "title": title,
            "amount": "100",
            "category": category,
            "subcategory": subcategory,
            "expense_date": "2026-08-05",
            "payer_member_id": str(seeded_chat.alice_member_id),
            "split_type": "equal",
            "participant_ids": json.dumps([seeded_chat.alice_member_id]),
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_suggestion_learns_from_saved_expenses(client, seeded_chat, auth_header):
    headers = auth_header(seeded_chat.alice_init_data)

    assert _suggest(client, seeded_chat, headers, "Пятёрочка") == {
        "category": "Продукты", "subcategory": "Супермаркет", "source": "dictionary",
    }
    assert _suggest(client, seeded_chat, headers, "Шуршик") is None

    # Сохранили «Шуршик» как корм — теперь угадывается, в т.ч. по первому слову
    _create(client, seeded_chat, headers, "Шуршик", "Питомцы", "Корм")
    assert _suggest(client, seeded_chat, headers, "шуршик")["subcategory"] == "Корм"
    assert _suggest(client, seeded_chat, headers, "Шуршик на углу")["source"] == "learned"

    # Поправили категорию у словарной Пятёрочки — выученное важнее словаря
    e = _create(client, seeded_chat, headers, "Пятёрочка", "Продукты", "Супермаркет")
    r = client.patch(
        f"/api/chats/{seeded_chat.chat_id}/expenses/{e['id']}",
        headers=headers,
        data={"category": "Дом", "subcategory": "Бытовая химия"},
    )
    assert r.status_code == 200, r.text
    s = _suggest(client, seeded_chat, headers, "пятерочка")
    assert (s["category"], s["subcategory"], s["source"]) == ("Дом", "Бытовая химия", "learned")


def test_learning_is_per_chat(client, seeded_chat, auth_header):
    headers = auth_header(seeded_chat.alice_init_data)
    _create(client, seeded_chat, headers, "Мурзик", "Питомцы", "Корм")
    personal_id = client.get("/api/personal", headers=headers).json()["id"]
    r = client.get(
        f"/api/chats/{personal_id}/category-suggestion", params={"title": "Мурзик"}, headers=headers
    )
    assert r.json() is None
