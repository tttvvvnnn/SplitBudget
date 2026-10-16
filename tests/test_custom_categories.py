"""Свои категории и подкатегории: у каждого пространства свой набор."""
from __future__ import annotations

import datetime as dt

import pytest

TODAY = dt.date.today()


@pytest.fixture(autouse=True)
def silent(monkeypatch):
    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr("app.api.routers.expenses.notify_budget_alerts", nothing)
    monkeypatch.setattr("app.api.routers.expenses.notify_new_expense", nothing, raising=False)


def _personal(client, init_data, auth_header):
    h = auth_header(init_data)
    pid = client.get("/api/personal", headers=h).json()["id"]
    me = client.get(f"/api/chats/{pid}/me", headers=h).json()["member"]["id"]
    return pid, me, h


def _node(tree, name):
    return next(n for n in tree if n["name"] == name)


def _expense(client, chat_id, h, payer, participants, amount, category, subcategory=None):
    data = {"title": category, "amount": str(amount), "category": category, "expense_date": TODAY.isoformat(),
            "payer_member_id": str(payer), "split_type": "equal", "participant_ids": str(participants)}
    if subcategory:
        data["subcategory"] = subcategory
    r = client.post(f"/api/chats/{chat_id}/expenses", headers=h, data=data)
    assert r.status_code == 201, r.text


def test_own_categories_only_in_own_space(client, seeded_chat, auth_header):
    pid, _, h = _personal(client, seeded_chat.alice_init_data, auth_header)
    r = client.post(f"/api/chats/{pid}/categories", headers=h, json={"name": "Хобби-мото", "icon": "🏍"})
    assert r.status_code == 201, r.text
    client.post(f"/api/chats/{pid}/categories", headers=h, json={"name": "Шлем", "parent": "Хобби-мото"})
    tree = client.post(f"/api/chats/{pid}/categories", headers=h, json={"name": "Мойка", "parent": "Транспорт"}).json()
    moto = _node(tree, "Хобби-мото")
    assert moto["custom"] and moto["icon"] == "🏍" and moto["subcategories"] == ["Шлем"]
    assert "Мойка" in _node(tree, "Транспорт")["subcategories"]
    assert tree[-1]["name"] == "Другое"
    assert "Хобби-мото" in client.get(f"/api/chats/{pid}/me", headers=h).json()["categories"]

    # У других только базовые
    bob_pid, _, bob = _personal(client, seeded_chat.bob_init_data, auth_header)
    bob_tree = client.get(f"/api/chats/{bob_pid}/categories", headers=bob).json()
    assert not any(n["custom"] for n in bob_tree)
    assert "Мойка" not in _node(bob_tree, "Транспорт")["subcategories"]
    assert client.get(f"/api/chats/{pid}/categories", headers=bob).status_code == 403

    assert client.post(f"/api/chats/{pid}/categories", headers=h, json={"name": "продукты"}).status_code == 400
    assert client.post(f"/api/chats/{pid}/categories", headers=h, json={"name": "А › Б"}).status_code == 400
    assert client.post(f"/api/chats/{pid}/categories", headers=h, json={"name": "X", "parent": "Нет"}).status_code == 400


def test_rename_moves_expenses_and_plan(client, seeded_chat, auth_header):
    pid, me, h = _personal(client, seeded_chat.alice_init_data, auth_header)
    tree = client.post(f"/api/chats/{pid}/categories", headers=h, json={"name": "Мото"}).json()
    moto_id = _node(tree, "Мото")["id"]
    tree = client.post(f"/api/chats/{pid}/categories", headers=h, json={"name": "Шлем", "parent": "Мото"}).json()
    helmet_id = _node(tree, "Мото")["all_subcategories"][0]["id"]
    _expense(client, pid, h, me, [me], 5000, "Мото", "Шлем")
    r = client.post(f"/api/chats/{pid}/plan/items", headers=h,
                    json={"title": "Мото", "kind": "spend", "percent": 10, "categories": ["Мото › Шлем"]})
    assert r.status_code == 201, r.text

    assert client.patch(f"/api/chats/{pid}/categories/{moto_id}", headers=h, json={"name": "Мотоцикл"}).status_code == 200
    client.patch(f"/api/chats/{pid}/categories/{helmet_id}", headers=h, json={"name": "Экипировка"})
    expenses = client.get(f"/api/chats/{pid}/expenses?month={TODAY.strftime('%Y-%m')}", headers=h).json()
    assert [(e["category"], e["subcategory"]) for e in expenses] == [("Мотоцикл", "Экипировка")]
    item = next(i for i in client.get(f"/api/chats/{pid}/plan", headers=h).json()["items"] if i["title"] == "Мото")
    assert item["categories"] == ["Мотоцикл › Экипировка"] and float(item["spent"]) == 5000.0

    # С тратами удалить нельзя — только скрыть
    assert client.delete(f"/api/chats/{pid}/categories/{moto_id}", headers=h).status_code == 400
    tree = client.put(f"/api/chats/{pid}/categories/hidden", headers=h,
                      json={"name": "Мотоцикл", "hidden": True}).json()
    assert _node(tree, "Мотоцикл")["hidden"] is True
    assert "Мотоцикл" not in client.get(f"/api/chats/{pid}/me", headers=h).json()["categories"]

    empty = _node(client.post(f"/api/chats/{pid}/categories", headers=h, json={"name": "Пусто"}).json(), "Пусто")
    tree = client.delete(f"/api/chats/{pid}/categories/{empty['id']}", headers=h).json()
    assert not any(n["name"] == "Пусто" for n in tree)


def test_hide_builtin(client, seeded_chat, auth_header):
    pid, _, h = _personal(client, seeded_chat.alice_init_data, auth_header)
    tree = client.put(f"/api/chats/{pid}/categories/hidden", headers=h, json={"name": "Питомцы", "hidden": True}).json()
    assert _node(tree, "Питомцы")["hidden"] is True
    tree = client.put(f"/api/chats/{pid}/categories/hidden", headers=h,
                      json={"name": "Такси", "parent": "Транспорт", "hidden": True}).json()
    assert "Такси" not in _node(tree, "Транспорт")["subcategories"]
    tree = client.put(f"/api/chats/{pid}/categories/hidden", headers=h, json={"name": "Питомцы", "hidden": False}).json()
    assert _node(tree, "Питомцы")["hidden"] is False
    assert client.put(f"/api/chats/{pid}/categories/hidden", headers=h,
                      json={"name": "Другое", "hidden": True}).status_code == 400


def test_family_category_in_personal_plan(client, seeded_chat, auth_header):
    """«Дача» из семейного чата видна в «Моих финансах» и считается в статье плана по доле."""
    h = auth_header(seeded_chat.alice_init_data)
    cid = seeded_chat.chat_id
    client.post(f"/api/chats/{cid}/categories", headers=h, json={"name": "Дача", "icon": "🏡"})
    client.post(f"/api/chats/{cid}/categories", headers=h, json={"name": "Ремонт крыши", "parent": "Дом"})
    pid, _, ph = _personal(client, seeded_chat.alice_init_data, auth_header)
    tree = client.get(f"/api/chats/{pid}/categories", headers=ph).json()
    dacha = _node(tree, "Дача")
    assert dacha["family"] == f"Тестовая семья #{cid * -1 - 100000000000}" and dacha["icon"] == "🏡"
    assert _node(tree, "Дом")["family_subcategories"] == ["Ремонт крыши"]

    r = client.post(f"/api/chats/{pid}/plan/items", headers=ph,
                    json={"title": "Дача", "kind": "spend", "percent": 10, "categories": ["Дача", "Дом › Ремонт крыши"]})
    assert r.status_code == 201, r.text
    a, b = seeded_chat.alice_member_id, seeded_chat.bob_member_id
    _expense(client, cid, h, a, [a, b], 3000, "Дача")
    _expense(client, cid, h, a, [a, b], 1000, "Дом", "Ремонт крыши")
    item = next(i for i in client.get(f"/api/chats/{pid}/plan", headers=ph).json()["items"] if i["title"] == "Дача")
    assert float(item["spent"]) == 2000.0  # доля Алисы
