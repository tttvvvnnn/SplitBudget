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
from app.shared.bank_import import preview
from app.shared.bank_import.preview import EXACT, LIKELY, MAYBE, SAME_SUM, match_level, purchases_of

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
    # Поступления теперь идут в доходы, а не в пропущенное
    assert {s.kind: s.count for s in skipped} == {base.SAVINGS: 1, base.TRANSFER: 1, base.CASH: 1}


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


def _expense(client, chat_id, member_id, h, title, amount, date, category=None):
    if category is None:
        category = "Продукты" if title == "продукты" else "Транспорт"
    r = client.post(
        f"/api/chats/{chat_id}/expenses",
        headers=h,
        data={
            "title": title, "amount": amount, "category": category, "subcategory": "",
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


def test_preview_groups_categories_and_matches(client, seeded_chat, personal, fake_parse):
    pid, me, h = personal
    # Одна электричка уже внесена вручную днём раньше списания; такси в семейном чате на
    # почти ту же сумму в тот же день — «может быть»
    _expense(client, pid, me, h, "электричка", "178", "2026-08-01")
    _expense(client, seeded_chat.chat_id, seeded_chat.alice_member_id, h, "такси", "396.50", "2026-08-06")
    body = _preview(client, pid, h)
    assert (body["period_from"], body["period_to"]) == ("2026-08-01", "2026-08-31")
    groups = {g["title"]: g for g in body["groups"]}
    assert (groups["Пятерочка"]["category"], groups["Пятерочка"]["subcategory"]) == ("Продукты", "Супермаркет")
    assert groups["ЦППК"]["category"] == "Транспорт"
    matches = [p["match"] for p in groups["ЦППК"]["purchases"]]
    found = [m for m in matches if m]
    assert len(found) == 1  # одна трата — одно совпадение
    assert (found[0]["title"], found[0]["level"], found[0]["default"], found[0]["editable"]) == (
        "электричка", "exact", "mine", True)  # та же сумма и категория
    unknown = groups["Ч50540"]
    assert unknown["category"] is None  # не опознали — по умолчанию не импортируем
    m = unknown["purchases"][0]["match"]
    assert (m["level"], m["default"], m["space"], m["editable"]) == ("maybe", "both", seeded_chat_title(client, seeded_chat, h), False)
    assert "ivi.ru" not in groups
    assert body["groups"][0]["title"] == "Пятерочка"  # по убыванию суммы
    incomes = body["incomes"]
    assert [(i["title"], i["checked"]) for i in incomes] == [("Перевод из банка ВТБ", True)]


def seeded_chat_title(client, seeded_chat, h):
    return client.get(f"/api/chats/{seeded_chat.chat_id}/me", headers=h).json()["chat"]["title"]


def test_family_expense_paid_by_me_is_matched(client, seeded_chat, personal, fake_parse):
    pid, _, h = personal
    _expense(client, seeded_chat.chat_id, seeded_chat.alice_member_id, h, "продукты", "1000", "2026-08-02")
    groups = {g["title"]: g for g in _preview(client, pid, h)["groups"]}
    m = groups["Пятерочка"]["purchases"][0]["match"]
    # 1 044,90 в выписке, 1 000 руками на следующий день, категория та же — «скорее всего»
    assert (m["title"], m["level"], m["default"], m["editable"]) == ("продукты", "likely", "mine", False)


def test_import_creates_expenses_and_learns(client, personal):
    pid, _, h = personal
    rows = [
        {"date": "2026-08-06", "amount": "396", "title": "Ч50540", "category": "Кафе и рестораны", "subcategory": "Кофе"},
        {"date": "2026-08-02", "amount": "178", "title": "ЦППК", "category": "Транспорт", "subcategory": "Общественный транспорт"},
    ]
    r = client.post(f"/api/chats/{pid}/bank-import", headers=h, json={"rows": rows})
    assert r.status_code == 200, r.text
    assert r.json() == {"created": 2, "updated": 0, "incomes": 0, "payments": 0}
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


@pytest.mark.parametrize(
    "p, e, level",
    [
        (("1625.31", 31, "Пятерочка", "Продукты"), ("1625.31", 31, "что-то", "Дом"), SAME_SUM),
        (("1625.31", 31, "Пятерочка", "Продукты"), ("1625.31", 30, "пятёрочка у дома", "Дом"), EXACT),
        (("1625.31", 31, "Пятерочка", "Продукты"), ("1600", 30, "продукты", "Продукты"), LIKELY),
        (("1625.31", 31, "Пятерочка", "Продукты"), ("1400", 30, "продукты", "Продукты"), None),  # больше 10%
        (("178", 31, "ЦППК", "Транспорт"), ("200", 31, "цппк электричка", "Транспорт"), LIKELY),  # разброс от 50 ₽
        (("396", 6, "Ч50540", None), ("396.5", 6, "такси", "Транспорт"), MAYBE),
        (("396", 6, "Ч50540", None), ("396.5", 9, "такси", "Транспорт"), None),
        (("1625.31", 27, "Пятерочка", "Продукты"), ("1625.31", 31, "пятерочка", "Продукты"), None),  # 4 дня
    ],
)
def test_match_level(p, e, level):
    pa, pd, pt, pc = p
    ea, ed, et, ec = e
    assert match_level(Decimal(pa), D(2026, 8, pd), pt, pc, Decimal(ea), D(2026, 8, ed), et, ec) == level


def test_take_from_statement_updates_my_expense(client, personal):
    pid, me, h = personal
    _expense(client, pid, me, h, "продукты", "1600", "2026-08-01")
    expense_id = client.get(f"/api/chats/{pid}/expenses?month=2026-08", headers=h).json()[0]["id"]
    r = client.post(f"/api/chats/{pid}/bank-import", headers=h, json={"updates": [
        {"expense_id": expense_id, "date": "2026-08-01", "amount": "1044.90", "title": "Пятерочка",
         "category": "Продукты", "subcategory": "Супермаркет"},
    ]})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1
    e = client.get(f"/api/chats/{pid}/expenses?month=2026-08", headers=h).json()[0]
    assert (e["title"], e["amount"], e["subcategory"]) == ("Пятерочка", "1044.90", "Супермаркет")
    assert [s["amount"] for s in e["shares"]] == ["1044.90"]


def test_cannot_update_family_expense(client, seeded_chat, personal):
    pid, _, h = personal
    _expense(client, seeded_chat.chat_id, seeded_chat.alice_member_id, h, "продукты", "1000", "2026-08-02")
    family_id = client.get(f"/api/chats/{seeded_chat.chat_id}/expenses?month=2026-08", headers=h).json()[0]["id"]
    r = client.post(f"/api/chats/{pid}/bank-import", headers=h, json={"updates": [
        {"expense_id": family_id, "date": "2026-08-01", "amount": "1044.90", "title": "Пятерочка"},
    ]})
    assert r.status_code == 400


def test_incomes_match_existing_and_link_source(client, personal, fake_parse):
    pid, _, h = personal
    fake_parse(
        ("2026-08-07", "+29000.33", "Банковский перевод. Филиал № 7701 Банка ВТБ (ПАО)"),
        ("2026-08-21", "+33896.81", "Банковский перевод. Филиал № 7701 Банка ВТБ (ПАО)"),
        ("2026-08-08", "+5852.36", 'Пополнение. САО "РЕСО-ГАРАНТИЯ". Заработная плата'),
        ("2026-08-10", "+500.00", "Пополнение. Система быстрых платежей"),
    )
    # Зарплата 7-го уже записана руками; «аванс» — регулярный доход на 20-е
    r = client.post(f"/api/chats/{pid}/income", headers=h, json={"title": "Зарплата ВТБ", "amount": "29000.33", "income_date": "2026-08-08"})
    assert r.status_code == 201, r.text
    r = client.post(f"/api/chats/{pid}/income-sources", headers=h, json={"title": "Аванс", "amount": "34000", "day_of_month": 20})
    assert r.status_code == 201, r.text
    source_id = r.json()["id"]
    incomes = {i["date"]: i for i in _preview(client, pid, h)["incomes"]}
    assert incomes["2026-08-07"]["match"]["title"] == "Зарплата ВТБ"
    assert incomes["2026-08-07"]["match"]["default"] == "mine"
    assert (incomes["2026-08-21"]["title"], incomes["2026-08-21"]["source_id"]) == ("Аванс", source_id)
    assert incomes["2026-08-08"]["title"] == "Зарплата" and incomes["2026-08-08"]["checked"]
    assert incomes["2026-08-10"]["checked"] is False  # входящий СБП — может быть и свой перевод
    r = client.post(f"/api/chats/{pid}/bank-import", headers=h, json={"incomes": [
        {"date": "2026-08-21", "amount": "33896.81", "title": "Аванс", "source_id": source_id},
    ]})
    assert r.json()["incomes"] == 1
    summary = client.get(f"/api/chats/{pid}/income?month=2026-08", headers=h).json()
    assert summary["expected"] in ("0", "0.00")  # аванс больше не «ожидается»


def test_import_closes_pending_payment(client, personal):
    pid, me, h = personal
    r = client.post(f"/api/chats/{pid}/recurring", headers=h, json={
        "title": "Яндекс Плюс", "amount": "249", "category": "Подписки", "day_of_month": 25, "kind": "subscription",
        "payer_member_id": me, "split_type": "equal", "participants": [{"member_id": me}],
    })
    assert r.status_code in (200, 201), r.text
    rid = r.json()["id"]
    # Следующий месяц: платёж шаблона, заведённого сегодня, там точно есть (тест не зависит от числа)
    today = dt.date.today()
    month = (today.replace(day=1) + dt.timedelta(days=32)).strftime("%Y-%m")
    day = 25
    r = client.post(f"/api/chats/{pid}/bank-import", headers=h, json={"rows": [
        {"date": f"{month}-{day:02d}", "amount": "249", "title": "Сервисы Яндекса", "category": "Подписки"},
    ]})
    assert r.status_code == 200, r.text
    assert r.json()["payments"] == 1
    expenses = client.get(f"/api/chats/{pid}/expenses?month={month}", headers=h).json()
    assert [e["is_recurring"] for e in expenses if e["title"] == "Сервисы Яндекса"] == [True]
    assert rid
