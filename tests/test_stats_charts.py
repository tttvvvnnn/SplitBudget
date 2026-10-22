"""Статистика: свой период, среднее за 3 месяца, категории по месяцам, дни недели,
крупнейшие траты, частые места, регулярные платежи и доходы по месяцам."""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from app.shared.stats import Row, build_stats, fetch_range, prev_period, stats_period

D = dt.date


def _add(client, headers, seeded_chat, title, amount, date, category="Еда"):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/expenses",
        headers=headers,
        data={
            "title": title, "amount": str(amount), "category": category, "expense_date": date,
            "payer_member_id": str(seeded_chat.alice_member_id), "split_type": "equal",
            "participant_ids": json.dumps([seeded_chat.alice_member_id]),
        },
    )
    assert r.status_code == 201, r.text


def test_prev_period_and_fetch_range():
    # месяц, квартал, год — прошлые календарные; свой диапазон — столько же дней перед ним
    assert prev_period(*stats_period("2026-03")) == (D(2026, 2, 1), D(2026, 3, 1))
    assert prev_period(D(2026, 7, 1), D(2026, 10, 1)) == (D(2026, 4, 1), D(2026, 7, 1))
    assert prev_period(D(2026, 1, 1), D(2027, 1, 1)) == (D(2025, 1, 1), D(2026, 1, 1))
    start, end = stats_period(date_from=D(2026, 10, 5), date_to=D(2026, 10, 14))
    assert (start, end) == (D(2026, 10, 5), D(2026, 10, 15))
    assert prev_period(start, end) == (D(2026, 9, 25), D(2026, 10, 5))
    # для года нужен весь прошлый год, для месяца хватает 12 месяцев тренда
    assert fetch_range(D(2026, 1, 1), D(2027, 1, 1)) == (D(2025, 1, 1), D(2027, 1, 1))
    assert fetch_range(*stats_period("2026-10")) == (D(2025, 11, 1), D(2026, 11, 1))


def test_build_stats_extra_fields():
    rows = [
        Row(D(2026, 7, 10), "Еда", None, Decimal("300"), "Пятёрочка"),
        Row(D(2026, 8, 10), "Еда", None, Decimal("100"), "Пятёрочка"),
        Row(D(2026, 8, 11), "Дом", None, Decimal("500"), "Свет", True),
        # сентябрь пустой — в среднее не идёт
        Row(D(2026, 10, 5), "Еда", None, Decimal("200"), "Пятёрочка"),  # понедельник
        Row(D(2026, 10, 6), "Еда", None, Decimal("50"), "пятерочка!"),
        Row(D(2026, 10, 11), "Дом", None, Decimal("700"), "Интернет", True),  # воскресенье
        Row(D(2026, 10, 12), "Кафе", None, Decimal("90"), "Кофе"),
    ]
    s = build_stats(*stats_period("2026-10"), rows)
    assert s.is_month and s.period == "2026-10" and s.count == 4
    assert s.avg_total == Decimal("450.00")  # (300 + 600) / 2 месяца с тратами
    cats = {c.category: c for c in s.by_category}
    assert cats["Еда"].avg_total == Decimal("200.00") and cats["Кафе"].avg_total == Decimal("0.00")
    assert s.weekdays[0] == Decimal("290") and s.weekdays[1] == Decimal("50") and s.weekdays[6] == Decimal("700")
    assert [t.title for t in s.top_expenses] == ["Интернет", "Пятёрочка", "Кофе", "пятерочка!"]
    assert [(p.title, p.count, p.total) for p in s.places] == [("Пятёрочка", 2, Decimal("250"))]
    assert (s.recurring_total, s.recurring_count) == (Decimal("700"), 1)
    trend = {c.category: c.totals for c in s.category_trend}
    assert s.category_trend[0].category == "Дом"
    assert trend["Еда"][-4:] == [Decimal("300"), Decimal("100"), Decimal("0"), Decimal("250")]


def test_quarter_and_custom_period_api(client, seeded_chat, auth_header):
    headers = auth_header(seeded_chat.alice_init_data)
    _add(client, headers, seeded_chat, "Апрель", 100, "2026-04-15")
    _add(client, headers, seeded_chat, "Июль", 40, "2026-07-01")
    _add(client, headers, seeded_chat, "Сентябрь", 60, "2026-09-30")
    _add(client, headers, seeded_chat, "Октябрь", 999, "2026-10-01")

    url = f"/api/chats/{seeded_chat.chat_id}/stats"
    body = client.get(f"{url}?date_from=2026-07-01&date_to=2026-09-30", headers=headers).json()
    assert body["is_month"] is False and body["period"] == "2026-07-01/2026-09-30"
    assert float(body["total"]) == 100.0 and float(body["prev_total"]) == 100.0
    assert body["daily"] == [] and body["avg_total"] is None
    assert body["trend"][-1]["month"] == "2026-09"

    body = client.get(f"{url}?date_from=2026-09-30&date_to=2026-10-01", headers=headers).json()
    assert float(body["total"]) == 1059.0

    for bad in ("date_from=2026-10-01&date_to=2026-09-01", "date_from=2026-10-01", "", "date_from=2020-01-01&date_to=2026-01-01"):
        assert client.get(f"{url}?{bad}", headers=headers).status_code == 400


def test_all_stats_income_trend(client, seeded_chat, auth_header):
    alice = auth_header(seeded_chat.alice_init_data)
    personal_id = client.get("/api/personal", headers=alice).json()["id"]
    for amount, date in [(1000, "2026-09-05"), (2000, "2026-10-05"), (500, "2026-10-20")]:
        r = client.post(f"/api/chats/{personal_id}/income", headers=alice,
                        json={"title": "ЗП", "amount": str(amount), "income_date": date})
        assert r.status_code == 201, r.text

    body = client.get("/api/all/stats?month=2026-10", headers=alice).json()
    assert [float(t["total"]) for t in body["income_trend"][-2:]] == [1000.0, 2500.0]
    assert float(body["income_total"]) == 2500.0
    body = client.get("/api/all/stats?date_from=2026-09-01&date_to=2026-10-31", headers=alice).json()
    assert float(body["income_total"]) == 3500.0
    # у Боба своих доходов нет
    bob = client.get("/api/all/stats?month=2026-10", headers=auth_header(seeded_chat.bob_init_data)).json()
    assert float(bob["income_total"]) == 0.0
