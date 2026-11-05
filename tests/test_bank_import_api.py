"""Импорт выписки, этап 2: предпросмотр (покупки, возвраты, дубли, категории) и импорт.

Разбор PDF подменён: выписку собираем из операций (сам парсер — в test_bank_import.py)."""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest

from app.api.routers import bank_import as router
from app.shared.bank_import import base
from app.shared.bank_import.base import Operation, Statement
from app.shared.bank_import.classify import classify
from app.shared.bank_import.preview import Purchase, purchases_of

D = dt.date


def _st(*ops: tuple[str, str, str]) -> Statement:
    return Statement(
        bank="tbank",
        period_from=D(2026, 8, 1),
        period_to=D(2026, 8, 31),
        account=None,
        operations=[classify(Operation(D.fromisoformat(d), Decimal(a), desc)) for d, a, desc in ops],
    )


STATEMENT = [
    ("2026-08-01", "-1044.90", "Оплата в 3D45 ТС Пятерочка"),
    ("2026-08-01", "-55.10", "Перевод для пополнения счета Инвесткопилка"),
    ("2026-08-02", "-178.00", "Оплата в ЦППК-2000012_SBP"),
    ("2026-08-02", "-178.00", "Оплата в ЦППК-2000012_SBP"),
    ("2026-08-03", "-19.00", "Оплата в ivi.ru"),
    ("2026-08-03", "+19.00", "Возврат покупки через СБП. ivi.ru"),
    ("2026-08-04", "-801.23", "Оплата в АЗК MR079"),
    ("2026-08-05", "+300.00", "Возврат покупки через СБП. АЗК MR079"),
    ("2026-08-06", "-396.00", "Оплата в Ч50540_SBP"),
    ("2026-08-07", "-5000.00", "Внешний перевод по номеру телефона"),
    ("2026-08-07", "+29000.33", "Банковский перевод. Филиал № 7701 Банка ВТБ"),
    ("2026-08-08", "-24000.00", "Снятие наличных. Т-Банк, 13997 Королёв Russia"),
]


def test_purchases_net_refunds_and_summarize_skipped():
    purchases, skipped = purchases_of(_st(*STATEMENT))
    by_title = {}
    for p in purchases:
        by_title.setdefault(p.title, []).append(p.amount)
    assert "ivi.ru" not in by_title  # вернули целиком
    assert by_title["АЗК"] == [Decimal("501.23")]  # частичный возврат вычтен
    assert by_title["ЦППК"] == [Decimal("178.00")] * 2
    assert {s.kind: s.count for s in skipped} == {
        base.SAVINGS: 1, base.TRANSFER: 1, base.INCOME: 1, base.CASH: 1,
    }


def test_refund_without_title_matches_same_amount():
    purchases, skipped = purchases_of(_st(
        ("2026-08-17", "-178.00", "Оплата в ЦППК-2000012_SBP"),
        ("2026-08-17", "-530.00", "Оплата в Viet Food_P_QR"),
        ("2026-08-17", "+178.00", "Возврат покупки через СБП. Возврат покупки"),
        ("2026-08-18", "+50.00", "Возврат покупки через СБП. Возврат покупки"),
    ))
    assert [p.title for p in purchases] == ["Viet Food"]
    assert [(s.kind, s.count) for s in skipped] == [(base.REFUND, 1)]  # 50 ₽ не к чему привязать


@pytest.fixture()
def personal(client, seeded_chat, auth_header):
    h = auth_header(seeded_chat.alice_init_data)
    pid = client.get("/api/personal", headers=h).json()["id"]
    me = client.get(f"/api/chats/{pid}/me", headers=h).json()["member"]["id"]
    return pid, me, h


@pytest.fixture()
def fake_parse(monkeypatch):
    def _set(*ops):
        monkeypatch.setattr(router, "parse", lambda data, name: _st(*ops))
    _set(*STATEMENT)
    return _set


def _expense(client, chat_id, member_id, h, title, amount, date):
    r = client.post(
        f"/api/chats/{chat_id}/expenses",
        headers=h,
        data={
            "title": title, "amount": amount, "category": "Транспорт", "subcategory": "",
            "expense_date": date, "payer_member_id": str(member_id), "split_type": "equal",
            "participant_ids": json.dumps([member_id]),
        },
    )
    assert r.status_code == 201, r.text


def _preview(client, pid, h):
    r = client.post(
        f"/api/chats/{pid}/bank-import/preview", headers=h, files={"file": ("st.pdf", b"%PDF-1.5", "application/pdf")}
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_preview_groups_categories_and_duplicates(client, seeded_chat, personal, fake_parse):
    pid, me, h = personal
    # Одна электричка уже внесена вручную днём раньше списания, ещё одна — в семейном чате
    # с другой суммой (не дубль)
    _expense(client, pid, me, h, "электричка", "178", "2026-08-01")
    _expense(client, seeded_chat.chat_id, seeded_chat.alice_member_id, h, "такси", "396.50", "2026-08-06")
    body = _preview(client, pid, h)
    assert (body["period_from"], body["period_to"]) == ("2026-08-01", "2026-08-31")
    groups = {g["title"]: g for g in body["groups"]}
    assert groups["Пятерочка"]["category"] == "Продукты"
    assert groups["Пятерочка"]["subcategory"] == "Супермаркет"
    assert groups["ЦППК"]["category"] == "Транспорт"
    dupes = [p["duplicate_title"] for p in groups["ЦППК"]["purchases"]]
    assert sorted(dupes, key=lambda x: x or "") == [None, "электричка"]  # одна трата — один дубль
    assert groups["Ч50540"]["category"] is None  # не опознали — по умолчанию не импортируем
    assert groups["Ч50540"]["purchases"][0]["duplicate_title"] is None
    assert "ivi.ru" not in groups
    # По убыванию суммы
    assert body["groups"][0]["title"] == "Пятерочка"


def test_family_expense_paid_by_me_is_a_duplicate(client, seeded_chat, personal, fake_parse):
    pid, _, h = personal
    _expense(client, seeded_chat.chat_id, seeded_chat.alice_member_id, h, "продукты", "1044.90", "2026-08-02")
    groups = {g["title"]: g for g in _preview(client, pid, h)["groups"]}
    assert groups["Пятерочка"]["purchases"][0]["duplicate_title"] == "продукты"


def test_import_creates_expenses_and_learns(client, personal):
    pid, _, h = personal
    rows = [
        {"date": "2026-08-06", "amount": "396", "title": "Ч50540", "category": "Кафе и рестораны", "subcategory": "Кофе"},
        {"date": "2026-08-02", "amount": "178", "title": "ЦППК", "category": "Транспорт", "subcategory": "Общественный транспорт"},
    ]
    r = client.post(f"/api/chats/{pid}/bank-import", headers=h, json={"rows": rows})
    assert r.status_code == 200, r.text
    assert r.json() == {"created": 2}
    expenses = client.get(f"/api/chats/{pid}/expenses?month=2026-08", headers=h).json()
    assert {(e["title"], e["category"], e["expense_date"]) for e in expenses} >= {
        ("Ч50540", "Кафе и рестораны", "2026-08-06"), ("ЦППК", "Транспорт", "2026-08-02"),
    }
    # Выбор запомнился: в следующий раз подставится сам
    s = client.get(f"/api/chats/{pid}/category-suggestion", params={"title": "Ч50540"}, headers=h).json()
    assert (s["category"], s["subcategory"]) == ("Кафе и рестораны", "Кофе")


def test_import_validates(client, seeded_chat, personal, fake_parse):
    pid, _, h = personal
    row = {"date": "2026-08-06", "amount": "10", "title": "x", "category": "Нет такой"}
    assert client.post(f"/api/chats/{pid}/bank-import", headers=h, json={"rows": [row]}).status_code == 400
    row["category"] = "Продукты"
    family = seeded_chat.chat_id
    assert client.post(f"/api/chats/{family}/bank-import", headers=h, json={"rows": [row]}).status_code == 400
    r = client.post(
        f"/api/chats/{family}/bank-import/preview", headers=h, files={"file": ("st.pdf", b"%PDF-1.5", "application/pdf")}
    )
    assert r.status_code == 400


def test_preview_reports_parse_errors(client, personal):
    pid, _, h = personal
    r = client.post(
        f"/api/chats/{pid}/bank-import/preview", headers=h, files={"file": ("st.csv", b"a;b", "text/csv")}
    )
    assert r.status_code == 400
    assert "PDF" in r.json()["detail"]


def test_duplicate_matching_is_one_to_one():
    import asyncio

    from app.shared.bank_import import preview

    class _E:
        def __init__(self, amount, date, title):
            self.amount, self.expense_date, self.title = Decimal(amount), date, title

    # Логику сопоставления проверяем без базы: подменяем выборку трат
    purchases = [Purchase(D(2026, 8, 2), Decimal("178"), "ЦППК", "") for _ in range(4)]
    existing = [_E("178", D(2026, 8, 2), "a"), _E("178", D(2026, 8, 3), "b")]

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return list(existing)

    class _Session:
        async def execute(self, _):
            return _Result()

    class _Member:
        tg_user_id = 1

    asyncio.run(preview.mark_duplicates(_Session(), _Member(), purchases))
    assert sorted(p.duplicate.title for p in purchases if p.duplicate) == ["a", "b"]
    assert sum(p.duplicate is None for p in purchases) == 2
