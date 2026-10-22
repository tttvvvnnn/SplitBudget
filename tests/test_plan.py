"""План распределения дохода: статьи в процентах, «Свободно», перенос остатков и «Отложил»."""
from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from app.bot.handlers import payments
from app.shared import plan
from app.shared.database import async_session_maker
from app.shared.models import Chat, GoalDeposit, Income, PlanItem

TODAY = dt.date.today()
PREV = (TODAY.replace(day=1) - dt.timedelta(days=1)).replace(day=10)


@pytest.fixture(autouse=True)
def silent_alerts(monkeypatch):
    async def silent(*args, **kwargs):
        return None

    monkeypatch.setattr("app.api.routers.expenses.notify_budget_alerts", silent)


def _personal(client, seeded_chat, auth_header):
    h = auth_header(seeded_chat.alice_init_data)
    pid = client.get("/api/personal", headers=h).json()["id"]
    me = client.get(f"/api/chats/{pid}/me", headers=h).json()["member"]["id"]
    return pid, me, h


def _expense(client, pid, me, h, amount, category, date=TODAY, subcategory=None):
    data = {"title": category, "amount": str(amount), "category": category, "expense_date": date.isoformat(),
            "payer_member_id": str(me), "split_type": "equal", "participant_ids": f"[{me}]"}
    if subcategory:
        data["subcategory"] = subcategory
    r = client.post(f"/api/chats/{pid}/expenses", headers=h, data=data)
    assert r.status_code == 201, r.text


def _income(client, pid, h, amount, date=TODAY):
    r = client.post(f"/api/chats/{pid}/income", headers=h, json={"title": "Зарплата", "amount": amount,
                                                                  "income_date": date.isoformat()})
    assert r.status_code == 201, r.text
    return r.json()


def _item(client, pid, h, **body):
    r = client.post(f"/api/chats/{pid}/plan/items", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _by_title(data):
    return {i["title"]: i for i in data["items"]}


def test_each_income_is_split_by_percent(client, seeded_chat, auth_header):
    pid, me, h = _personal(client, seeded_chat, auth_header)
    goal = client.post(f"/api/chats/{pid}/goals", headers=h, json={"title": "Отпуск", "target": 100000}).json()
    _item(client, pid, h, title="Еда", kind="spend", percent=20, categories=["Продукты", "Кафе и рестораны"])
    _item(client, pid, h, title="Отпуск", kind="goal", percent=10, goal_id=goal["id"])
    _income(client, pid, h, 10000)
    _income(client, pid, h, 1000)  # даже маленькое поступление делится по плану
    _expense(client, pid, me, h, 500, "Продукты")
    _expense(client, pid, me, h, 300, "Другое")  # без статьи — со «Свободно»

    data = client.get(f"/api/chats/{pid}/plan", headers=h).json()
    items = _by_title(data)
    assert float(data["income"]) == 11000.0 and float(data["percent_total"]) == 30.0
    assert float(items["Еда"]["allocated"]) == 2200.0 and float(items["Еда"]["spent"]) == 500.0
    assert float(items["Отпуск"]["allocated"]) == 1100.0 and float(items["Отпуск"]["remaining"]) == 1100.0
    free = items["Свободно"]
    assert free["kind"] == "free" and float(free["percent"]) == 70.0
    assert float(free["allocated"]) == 7700.0 and float(free["spent"]) == 300.0

    # «Отложил» — на цель уходит то, что по плану ещё не отложено
    goal_item = items["Отпуск"]["id"]
    data = client.post(f"/api/chats/{pid}/plan/items/{goal_item}/save", headers=h).json()
    assert float(_by_title(data)["Отпуск"]["remaining"]) == 0.0
    assert float(_by_title(data)["Отпуск"]["goal_saved"]) == 1100.0
    assert client.post(f"/api/chats/{pid}/plan/items/{goal_item}/save", headers=h).status_code == 400


def test_subcategory_wins_over_category(client, seeded_chat, auth_header):
    pid, me, h = _personal(client, seeded_chat, auth_header)
    _item(client, pid, h, title="Транспорт", kind="spend", percent=10, categories=["Транспорт"])
    _item(client, pid, h, title="Машина", kind="spend", percent=10, categories=["Транспорт › Топливо"])
    _item(client, pid, h, title="Кофе", kind="spend", percent=5, categories=["Кафе и рестораны › Кофе"])
    _expense(client, pid, me, h, 3000, "Транспорт", subcategory="Топливо")
    _expense(client, pid, me, h, 500, "Транспорт", subcategory="Такси")
    _expense(client, pid, me, h, 200, "Кафе и рестораны", subcategory="Кофе")
    _expense(client, pid, me, h, 900, "Кафе и рестораны", subcategory="Рестораны")  # не в статье
    items = _by_title(client.get(f"/api/chats/{pid}/plan", headers=h).json())
    assert float(items["Машина"]["spent"]) == 3000.0
    assert float(items["Транспорт"]["spent"]) == 500.0
    assert float(items["Кофе"]["spent"]) == 200.0
    assert float(items["Свободно"]["spent"]) == 900.0
    r = client.post(f"/api/chats/{pid}/plan/items", headers=h,
                    json={"title": "X", "kind": "spend", "percent": 1, "categories": ["Транспорт › Ракета"]})
    assert r.status_code == 400


def test_validation(client, seeded_chat, auth_header):
    pid, _, h = _personal(client, seeded_chat, auth_header)
    _item(client, pid, h, title="Еда", kind="spend", percent=60, categories=["Продукты"])
    r = client.post(f"/api/chats/{pid}/plan/items", headers=h,
                    json={"title": "Кафе", "kind": "spend", "percent": 50, "categories": ["Кафе и рестораны"]})
    assert r.status_code == 400 and "40%" in r.json()["detail"]
    r = client.post(f"/api/chats/{pid}/plan/items", headers=h,
                    json={"title": "Ещё еда", "kind": "spend", "percent": 10, "categories": ["Продукты"]})
    assert r.status_code == 400 and "уже в статье «Еда»" in r.json()["detail"]
    r = client.post(f"/api/chats/{pid}/plan/items", headers=h, json={"title": "Цель", "kind": "goal", "percent": 10})
    assert r.status_code == 400
    assert client.get(f"/api/chats/{seeded_chat.chat_id}/plan", headers=h).status_code == 400
    bob = auth_header(seeded_chat.bob_init_data)
    assert client.get(f"/api/chats/{pid}/plan", headers=bob).status_code == 403


def test_month_end_leftovers_go_to_free(client, seeded_chat, auth_header):
    """Статьи обнуляются, остатки и перерасходы прошлого месяца сходятся в «Свободно»."""
    pid, me, h = _personal(client, seeded_chat, auth_header)
    _item(client, pid, h, title="Еда", kind="spend", percent=20, categories=["Продукты"])

    async def backdate():
        async with async_session_maker() as session:
            await session.execute(
                update(PlanItem).where(PlanItem.chat_id == pid).values(created_at=dt.datetime.combine(PREV, dt.time()))
            )
            await session.commit()

    asyncio.run(backdate())
    _income(client, pid, h, 10000, PREV)
    _expense(client, pid, me, h, 3000, "Продукты", PREV)  # Еда: 2 000 выделено, −1 000
    _expense(client, pid, me, h, 1000, "Другое", PREV)  # Свободно: 8 000 − 1 000 = 7 000

    before = _by_title(client.get(f"/api/chats/{pid}/plan?date={PREV.isoformat()}", headers=h).json())
    assert float(before["Еда"]["remaining"]) == -1000.0 and float(before["Свободно"]["remaining"]) == 7000.0

    now = client.get(f"/api/chats/{pid}/plan", headers=h).json()
    items = _by_title(now)
    assert float(now["carried"]) == 6000.0
    assert float(items["Еда"]["allocated"]) == 0.0 and float(items["Еда"]["spent"]) == 0.0
    assert float(items["Свободно"]["allocated"]) == 6000.0 and float(items["Свободно"]["carried"]) == 6000.0


def test_start_day():
    assert plan.period_start(25, dt.date(2026, 10, 24)) == dt.date(2026, 9, 25)
    assert plan.period_start(25, dt.date(2026, 10, 25)) == dt.date(2026, 10, 25)
    assert plan.period_start(10, dt.date(2026, 1, 3)) == dt.date(2025, 12, 10)
    assert plan.period_end(dt.date(2026, 12, 25)) == dt.date(2027, 1, 25)
    assert plan.period_start(1, dt.date(2026, 10, 16)) == dt.date(2026, 10, 1)


def test_start_day_setting(client, seeded_chat, auth_header):
    pid, _, h = _personal(client, seeded_chat, auth_header)
    r = client.put(f"/api/chats/{pid}/plan/settings", headers=h, json={"start_day": 25})
    assert r.status_code == 200 and r.json()["start_day"] == 25
    assert r.json()["start"] == plan.period_start(25, TODAY).isoformat()
    assert client.put(f"/api/chats/{pid}/plan/settings", headers=h, json={"start_day": 31}).status_code == 422


def test_bot_saved_button(client, seeded_chat, auth_header):
    pid, _, h = _personal(client, seeded_chat, auth_header)
    goal = client.post(f"/api/chats/{pid}/goals", headers=h, json={"title": "Подушка", "target": 50000}).json()
    _item(client, pid, h, title="Подушка", kind="goal", percent=15, goal_id=goal["id"])
    income = _income(client, pid, h, 2000)

    def press(user_id=seeded_chat.alice_tg_id):
        log = {}

        async def edit_text(text, **kwargs):
            log["text"] = text

        async def answer(text=None, show_alert=False):
            log["answer"] = text

        message = SimpleNamespace(text="✅ Записал доход", html_text="✅ Записал доход", edit_text=edit_text)
        cb = SimpleNamespace(data=f"plan:save:{income['id']}", from_user=SimpleNamespace(id=user_id),
                             message=message, answer=answer)
        asyncio.run(payments.on_plan_save_button(cb))
        return log

    assert press(seeded_chat.bob_tg_id)["answer"] == "Доход не найден"
    assert "Подушка 300" in press()["text"]
    assert press()["answer"] == "Уже отложено"

    async def deposits():
        async with async_session_maker() as session:
            rows = await session.execute(select(GoalDeposit.amount, GoalDeposit.income_id).where(GoalDeposit.goal_id == goal["id"]))
            return [(float(a), i) for a, i in rows.all()]

    assert asyncio.run(deposits()) == [(300.0, income["id"])]


def test_alerts_by_plan_item(client, seeded_chat, auth_header):
    pid, me, h = _personal(client, seeded_chat, auth_header)
    _item(client, pid, h, title="Еда", kind="spend", percent=10, categories=["Продукты"])
    _income(client, pid, h, 10000)
    _expense(client, pid, me, h, 850, "Продукты")

    async def alerts():
        async with async_session_maker() as session:
            chat = await session.get(Chat, pid)
            out = await plan.collect_alerts(session, chat, TODAY)
            await session.commit()
            return out

    first = asyncio.run(alerts())
    # Трата уже могла разбудить уведомление в API — тогда второй раз оно не придёт
    assert first == [] or ("Статья «Еда» почти исчерпана" in first[0])
    assert asyncio.run(alerts()) == []


def test_savings_for_income_uses_percent(client, seeded_chat, auth_header):
    pid, _, h = _personal(client, seeded_chat, auth_header)
    goal = client.post(f"/api/chats/{pid}/goals", headers=h, json={"title": "Отпуск", "target": 1000}).json()
    _item(client, pid, h, title="Отпуск", kind="goal", percent=12.5, goal_id=goal["id"])
    income = _income(client, pid, h, 1000)

    async def run():
        async with async_session_maker() as session:
            obj = await session.get(Income, income["id"])
            return [(g.title, float(a)) for _, g, a in await plan.savings_for_income(session, obj)]

    assert asyncio.run(run()) == [("Отпуск", 125.0)]


def _card(client, pid, h, debt=50000):
    r = client.post(f"/api/chats/{pid}/credit-cards", headers=h, json={
        "title": "Кредитка Тбанк", "debt": debt, "category": "Кредиты", "subcategory": "Кредитная карта"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_card_payment_goes_to_item_by_category(client, seeded_chat, auth_header):
    """Кредит — обычная статья с категорией; деньги, внесённые на карту, списываются с неё же."""
    pid, me, h = _personal(client, seeded_chat, auth_header)
    _item(client, pid, h, title="Кредитки", kind="spend", percent=30, categories=["Кредиты"])
    _income(client, pid, h, 100000)
    card_id = _card(client, pid, h)
    assert client.post(f"/api/chats/{pid}/credit-cards/{card_id}/pay", headers=h, json={"amount": 12000}).status_code == 200
    _expense(client, pid, me, h, 3000, "Кредиты", subcategory="Кредит")
    items = _by_title(client.get(f"/api/chats/{pid}/plan", headers=h).json())
    assert float(items["Кредитки"]["spent"]) == 15000.0
    assert float(items["Свободно"]["spent"]) == 0.0

    entries = client.get(f"/api/chats/{pid}/plan/entries?item_id={items['Кредитки']['id']}", headers=h).json()
    assert sorted((e["title"], e["source"], float(e["amount"])) for e in entries) == [
        ("Кредитка Тбанк", "card", 12000.0), ("Кредиты", "expense", 3000.0)]
    assert client.post(f"/api/chats/{pid}/plan/items", headers=h, json={
        "title": "Долг", "kind": "debt", "percent": 1}).status_code == 422


def test_entries_of_free_and_goal(client, seeded_chat, auth_header):
    pid, me, h = _personal(client, seeded_chat, auth_header)
    goal = client.post(f"/api/chats/{pid}/goals", headers=h, json={"title": "Отпуск", "target": 100000}).json()
    item = _item(client, pid, h, title="Отпуск", kind="goal", percent=10, goal_id=goal["id"])
    goal_item = _by_title(item)["Отпуск"]["id"]
    _income(client, pid, h, 10000)
    _expense(client, pid, me, h, 300, "Другое")
    client.post(f"/api/chats/{pid}/plan/items/{goal_item}/save", headers=h)
    free = client.get(f"/api/chats/{pid}/plan/entries", headers=h).json()
    assert [(e["title"], float(e["amount"])) for e in free] == [("Другое", 300.0)]
    saved = client.get(f"/api/chats/{pid}/plan/entries?item_id={goal_item}", headers=h).json()
    assert [(e["source"], float(e["amount"])) for e in saved] == [("deposit", 1000.0)]
    assert client.get(f"/api/chats/{pid}/plan/entries?item_id=999999", headers=h).status_code == 404
