"""Новая аналитика: год назад, обязательные и «на жизнь», «чаще или дороже», год по дням, цены,
наблюдения (insights), долги во времени, прогноз целей, план за полгода и «Куда ушли деньги»."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.shared.debt_history import card_payoff, loan_payoff
from app.shared.goals import forecast
from app.shared.report import insight_text
from app.shared.schemas import Insight
from app.shared.stats import Row, build_stats, prices, stats_period, trend_months

D = dt.date
M = Decimal
TODAY = dt.date.today()


@pytest.fixture(autouse=True)
def silent_alerts(monkeypatch):
    async def silent(*args, **kwargs):
        return None

    monkeypatch.setattr("app.api.routers.expenses.notify_budget_alerts", silent)


def _year_rows():
    """Год трат: кофе по 300 (с сентября по 500), аренда, Яндекс Плюс 299 → 399."""
    rows = []
    for m in range(1, 11):
        for d in (3, 10, 17):
            rows.append(Row(D(2026, m, d), "Кафе", None, M(300 if m < 9 else 500), "Кофе Хаус"))
        rows.append(Row(D(2026, m, 5), "Дом", "Аренда", M(40000), "Аренда"))
        rows.append(Row(D(2026, m, 6), "Подписки", None, M(299 if m < 8 else 399), "Яндекс Плюс", True))
    return rows


def test_year_ago_mandatory_days_and_counts():
    rows = _year_rows()
    # октябрь 2025: пять трат — уже есть с чем сравнить
    rows += [Row(D(2025, 10, d), "Кафе", None, M(200), "Кофе") for d in (1, 2, 3, 4, 5)]
    s = build_stats(*stats_period("2026-10"), rows, today=D(2026, 11, 2))
    assert s.year_ago_total == M(1000)
    cats = {c.category: c for c in s.by_category}
    assert cats["Кафе"].year_ago_total == M(1000) and cats["Дом"].year_ago_total == M(0)
    # «чаще или дороже»: в сентябре кофе тоже трижды
    assert cats["Кафе"].count == 3 and cats["Кафе"].prev_count == 3
    # обязательные по месяцам: аренда и подписка из «Платежей»
    assert s.mandatory_trend[-1] == M(40399) and s.mandatory_trend[-4] == M(40299)
    assert len(s.mandatory_trend) == len(s.trend) == 12
    # год по дням — с 1 ноября 2025 по 31 октября 2026, без обязательных
    assert s.days_from == D(2025, 11, 1) and len(s.day_totals) == 365
    assert s.day_totals[(D(2026, 10, 3) - s.days_from).days] == M(500)
    assert s.day_totals[(D(2026, 10, 5) - s.days_from).days] == M(0)  # аренда


def test_year_ago_needs_history():
    rows = _year_rows() + [Row(D(2025, 10, 6), "Кафе", None, M(100), "x")]
    s = build_stats(*stats_period("2026-10"), rows, today=D(2026, 11, 2))
    assert s.year_ago_total is None  # одна трата год назад — учёт только начинался
    # за год (12 месяцев) «год назад» — это и есть прошлый период
    assert build_stats(D(2026, 1, 1), D(2027, 1, 1), rows, today=D(2027, 1, 2)).year_ago_total is None


def test_prices_personal_inflation():
    rows = _year_rows() + [
        # «Пятёрочка» — разные корзины, это не подорожание
        Row(D(2026, 1, 8), "Продукты", None, M(300), "Пятёрочка"),
        Row(D(2026, 5, 8), "Продукты", None, M(4000), "Пятёрочка"),
        Row(D(2026, 10, 8), "Продукты", None, M(5000), "Пятёрочка"),
    ]
    changes, index = prices(rows, trend_months(D(2026, 11, 1)))
    by_title = {c.title: c for c in changes}
    assert set(by_title) == {"Кофе Хаус", "Яндекс Плюс"}
    assert (by_title["Кофе Хаус"].old, by_title["Кофе Хаус"].new) == (M(300), M(500))
    assert by_title["Яндекс Плюс"].percent == M("33.4")
    assert changes[0].title == "Кофе Хаус"  # сильнее всего
    assert index is not None and index > 0  # аренда не менялась, но кофе и подписка подорожали


def test_insights():
    rows = _year_rows()
    rows.append(Row(D(2026, 10, 20), "Техника", None, M(90000), "Телевизор"))
    for d in range(1, 7):
        rows.append(Row(D(2026, 10, d), "Такси", None, M(800), "Такси"))
    s = build_stats(*stats_period("2026-10"), rows, today=D(2026, 11, 2))
    kinds = {i.kind: i for i in s.insights}
    assert "total_up" in kinds and kinds["total_up"].tone == "bad"
    assert "price_up" in kinds and "«Кофе Хаус» подорожало" in kinds["price_up"].text
    assert "new_category" in kinds  # «Техника» — новое за 3 месяца
    assert kinds["no_spend"].tone == "good" and "Дней без трат" in kinds["no_spend"].text
    assert len(s.insights) <= 6
    text = insight_text(kinds["price_up"], "₽")
    assert "300 ₽ → 500 ₽" in text and "{0}" not in text


def test_insight_driver_frequency_vs_price():
    base = [Row(D(2026, 9, d), "Кафе", None, M(300), "Кофе") for d in (1, 2, 3)]
    oct_rows = [Row(D(2026, 10, d), "Кафе", None, M(300), "Кофе") for d in range(1, 10)]
    s = build_stats(*stats_period("2026-10"), base + oct_rows, today=D(2026, 11, 2))
    [driver] = [i for i in s.insights if i.kind == "driver"]
    assert "из-за частоты" in driver.text and "9 раз вместо 3" in driver.text
    pricey = [Row(D(2026, 10, d), "Кафе", None, M(900), "Кофе") for d in (1, 2, 3)]
    s = build_stats(*stats_period("2026-10"), base + pricey, today=D(2026, 11, 2))
    [driver] = [i for i in s.insights if i.kind == "driver"]
    assert "из-за цены" in driver.text and driver.amounts == [M(900), M(300)]


def test_insight_text_escapes():
    i = Insight(kind="x", icon="🔺", tone="bad", text="«<b>» — {0}", amounts=[M("1500")])
    assert insight_text(i, "RUB") == "🔺 «&lt;b&gt;» — 1 500 RUB"


def test_payoff_math():
    # 100 000 под 0% по 10 000 — 10 месяцев; со ставкой — дольше и с процентами
    assert loan_payoff(M(100000), None, M(10000)) == (10, M(0))
    months, interest = loan_payoff(M(100000), M(12), M(10000))
    assert months == 11 and M(5000) < interest < M(6000)
    assert loan_payoff(M(100000), M(240), M(1000)) == (None, M(0))  # платёж не покрывает проценты
    assert card_payoff(M(7000), M(1000)) == 7
    assert card_payoff(M(7000), M(0)) is None and card_payoff(M(0), M(0)) == 0


def test_goal_forecast():
    today = D(2026, 11, 5)
    goal = SimpleNamespace(target=M(100000), created_at=dt.datetime(2026, 1, 1))
    deposits = [SimpleNamespace(amount=M(10000), deposit_date=D(2026, m, 10)) for m in (8, 9, 10)]
    pace, eta = forecast(goal, deposits, M(40000), today)
    # 30 000 за август–5 ноября (~97 дней) ≈ 9 400 в месяц; осталось 60 000 → 7 месяцев
    assert M(9000) < pace < M(10000) and eta == "2027-06"
    # новая цель: одно пополнение в день создания — темп не больше самого пополнения
    fresh = SimpleNamespace(target=M(100000), created_at=dt.datetime(2026, 11, 1))
    pace, _ = forecast(fresh, [SimpleNamespace(amount=M(5000), deposit_date=D(2026, 11, 1))], M(5000), today)
    assert pace == M(5000)
    assert forecast(goal, [], M(0), today) == (None, None)


def _personal(client, seeded_chat, auth_header):
    h = auth_header(seeded_chat.alice_init_data)
    pid = client.get("/api/personal", headers=h).json()["id"]
    me = client.get(f"/api/chats/{pid}/me", headers=h).json()["member"]["id"]
    return pid, me, h


def test_goals_api_has_forecast(client, seeded_chat, auth_header):
    pid, _, h = _personal(client, seeded_chat, auth_header)
    goal = client.post(f"/api/chats/{pid}/goals", headers=h, json={"title": "Отпуск", "target": 100000}).json()
    out = client.post(f"/api/chats/{pid}/goals/{goal['id']}/deposits", headers=h,
                      json={"amount": 20000, "deposit_date": TODAY.isoformat()}).json()
    assert float(out["pace"]) == 20000.0 and out["eta_month"]


def test_debt_history_api(client, seeded_chat, auth_header):
    pid, me, h = _personal(client, seeded_chat, auth_header)
    card = client.post(f"/api/chats/{pid}/credit-cards", headers=h, json={"title": "Т-Банк", "debt": 10000}).json()
    paid_on = (TODAY.replace(day=1) - dt.timedelta(days=40)).replace(day=10)
    r = client.post(f"/api/chats/{pid}/credit-cards/{card['id']}/pay", headers=h,
                    json={"amount": 3000, "op_date": paid_on.isoformat()})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/chats/{pid}/recurring", headers=h, json={
        "title": "Ипотека", "amount": 50000, "payer_member_id": me, "day_of_month": 15, "kind": "loan",
        "debt": 1000000, "participants": [{"member_id": me}], "category": "Кредиты"})
    assert r.status_code == 201, r.text

    data = client.get(f"/api/chats/{pid}/credits/history", headers=h).json()
    assert len(data["months"]) == 12 and data["months"][-1] == TODAY.strftime("%Y-%m")
    assert float(data["cards"][-1]) == 7000.0 and float(data["loans"][-1]) == 1000000.0
    assert data["cards"][0] is None  # год назад ни карты, ни кредита ещё не было
    by_title = {p["title"]: p for p in data["payoff"]}
    # по карте вносили 3 000 за 3 месяца — 1 000 в месяц, 7 000 закроется за 7 месяцев
    assert float(by_title["Т-Банк"]["monthly"]) == 1000.0 and by_title["Т-Банк"]["months_left"] == 7
    assert by_title["Ипотека"]["months_left"] == 20
    assert data["free_month"] == by_title["Ипотека"]["payoff_month"]
    assert client.get(f"/api/chats/{seeded_chat.chat_id}/credits/history", headers=h).status_code == 400


def test_plan_history_and_flow(client, seeded_chat, auth_header):
    pid, me, h = _personal(client, seeded_chat, auth_header)
    r = client.post(f"/api/chats/{pid}/plan/items", headers=h,
                    json={"title": "Еда", "kind": "spend", "percent": 40, "categories": ["Продукты"]})
    assert r.status_code == 201, r.text
    item_id = r.json()["items"][0]["id"]
    client.post(f"/api/chats/{pid}/income", headers=h,
                json={"title": "Зарплата", "amount": 100000, "income_date": TODAY.isoformat()})
    for amount, category in ((30000, "Продукты"), (5000, "Кафе и рестораны")):
        r = client.post(f"/api/chats/{pid}/expenses", headers=h, data={
            "title": category, "amount": str(amount), "category": category, "expense_date": TODAY.isoformat(),
            "payer_member_id": str(me), "split_type": "equal", "participant_ids": f"[{me}]"})
        assert r.status_code == 201, r.text

    history = client.get(f"/api/chats/{pid}/plan/history", headers=h).json()
    assert len(history["periods"]) == 6 and history["periods"][-1]["active"]
    assert not history["periods"][0]["active"]  # плана тогда не было
    items = {i["title"]: i for i in history["items"]}
    assert float(items["Еда"]["allocated"][-1]) == 40000.0 and float(items["Еда"]["spent"][-1]) == 30000.0
    assert items["Еда"]["allocated"][0] is None

    flow = client.get(f"/api/chats/{pid}/plan/flow", headers=h).json()
    links = {(l["source"], l["target"]): float(l["value"]) for l in flow["links"]}
    assert links[("income", f"item:{item_id}")] == 40000.0
    assert links[(f"item:{item_id}", "cat:Продукты")] == 30000.0
    assert links[(f"item:{item_id}", "left")] == 10000.0
    assert links[("income", "item:free")] == 60000.0
    assert links[("item:free", "cat:Кафе и рестораны")] == 5000.0
    assert {n["id"] for n in flow["nodes"]} >= {"income", f"item:{item_id}", "item:free", "cat:Продукты", "left"}
