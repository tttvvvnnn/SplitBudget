"""Вкладка «Кредиты»: кредитные карты с операциями, кредиты, напоминания, перенос карт из платежей."""
from __future__ import annotations

import asyncio
import datetime as dt
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.shared.credits import due_card_notices
from app.shared.database import async_session_maker

ROOT = Path(__file__).resolve().parent.parent
TODAY = dt.date.today()


def _personal(client, seeded_chat, auth_header):
    h = auth_header(seeded_chat.alice_init_data)
    return client.get("/api/personal", headers=h).json()["id"], h


def _card(client, pid, h, **extra):
    body = {"title": "Т-Банк", "debt": 84000, "credit_limit": 150000, "statement_day": 10,
            "grace_until": (TODAY + dt.timedelta(days=23)).isoformat(),
            "min_payment": 4200, "payment_date": (TODAY + dt.timedelta(days=14)).isoformat()}
    body.update(extra)
    r = client.post(f"/api/chats/{pid}/credit-cards", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _expenses(client, pid, h):
    month = TODAY.strftime("%Y-%m")
    return client.get(f"/api/chats/{pid}/expenses?month={month}", headers=h).json()


def test_card_operations(client, seeded_chat, auth_header):
    pid, h = _personal(client, seeded_chat, auth_header)
    card = _card(client, pid, h)
    assert card["next_deadline"] == (TODAY + dt.timedelta(days=14)).isoformat()
    url = f"/api/chats/{pid}/credit-cards/{card['id']}"

    # Внести — долг меньше, трат нет
    card = client.post(f"{url}/pay", headers=h, json={"amount": 4000}).json()
    assert float(card["debt"]) == 80000.0
    assert _expenses(client, pid, h) == []
    r = client.post(f"{url}/pay", headers=h, json={"amount": 90000})
    assert r.status_code == 400 and "больше долга" in r.json()["detail"]

    # Продлить срок — долг тот же, даты новые
    new_grace = (TODAY + dt.timedelta(days=55)).isoformat()
    card = client.post(f"{url}/extend", headers=h, json={"grace_until": new_grace}).json()
    assert float(card["debt"]) == 80000.0
    assert card["grace_until"] == new_grace and card["payment_date"] is None and card["min_payment"] is None
    assert client.post(f"{url}/extend", headers=h, json={}).status_code == 400

    # Начислили проценты — трата в категории карты, долг вырос
    card = client.post(f"{url}/interest", headers=h, json={"amount": 1500}).json()
    assert float(card["debt"]) == 81500.0
    [expense] = _expenses(client, pid, h)
    assert expense["title"] == "Проценты · Т-Банк" and expense["category"] == "Кредиты"

    # Сверить с банком
    card = client.post(f"{url}/adjust", headers=h, json={"debt": 82000}).json()
    assert float(card["debt"]) == 82000.0
    assert [o["kind"] for o in card["operations"]] == ["adjust", "interest", "extend", "payment"]

    # Отмена операций в обратном порядке возвращает всё как было
    for op in card["operations"]:
        card = client.delete(f"{url}/operations/{op['id']}", headers=h).json()
    assert float(card["debt"]) == 84000.0
    assert card["payment_date"] == (TODAY + dt.timedelta(days=14)).isoformat()
    assert float(card["min_payment"]) == 4200.0
    assert _expenses(client, pid, h) == []

    # Правка карты долг не трогает
    card = client.patch(url, headers=h, json={"title": "Т-Банк Платинум", "debt": 1}).json()
    assert card["title"] == "Т-Банк Платинум" and float(card["debt"]) == 84000.0
    assert client.delete(url, headers=h).status_code == 204
    assert client.get(f"/api/chats/{pid}/credits", headers=h).json()["cards"] == []


def test_credits_overview_with_loans(client, seeded_chat, auth_header):
    pid, h = _personal(client, seeded_chat, auth_header)
    me = client.get(f"/api/chats/{pid}/me", headers=h).json()["member"]["id"]
    _card(client, pid, h, debt=10000)
    r = client.post(f"/api/chats/{pid}/recurring", headers=h, json={
        "title": "Ипотека", "amount": 32500, "payer_member_id": me, "day_of_month": 15, "kind": "loan",
        "debt": 2000000, "participants": [{"member_id": me}], "category": "Кредиты"})
    assert r.status_code == 201, r.text

    data = client.get(f"/api/chats/{pid}/credits", headers=h).json()
    assert float(data["total_debt"]) == 2010000.0
    [loan] = data["loans"]
    assert loan["title"] == "Ипотека" and float(loan["debt"]) == 2000000.0 and loan["next_due"]
    assert data["next_deadline"] == min(loan["next_due"], data["cards"][0]["next_deadline"])


def test_credits_private(client, seeded_chat, auth_header):
    pid, h = _personal(client, seeded_chat, auth_header)
    assert client.get(f"/api/chats/{seeded_chat.chat_id}/credits", headers=h).status_code == 400
    card = _card(client, pid, h)
    bob = auth_header(seeded_chat.bob_init_data)
    assert client.get(f"/api/chats/{pid}/credits", headers=bob).status_code == 403
    bob_pid = client.get("/api/personal", headers=bob).json()["id"]
    r = client.post(f"/api/chats/{bob_pid}/credit-cards/{card['id']}/pay", headers=bob, json={"amount": 5})
    assert r.status_code == 404


def test_card_reminders(client, seeded_chat, auth_header):
    pid, h = _personal(client, seeded_chat, auth_header)
    card = _card(client, pid, h, title="Напоминалка", payment_date=(TODAY + dt.timedelta(days=3)).isoformat(),
                 grace_until=(TODAY + dt.timedelta(days=30)).isoformat())

    async def run(day):
        async with async_session_maker() as session:
            notices = await due_card_notices(session, day)
            await session.commit()
            return [n for n in notices if "Напоминалка" in n.text]

    [notice] = asyncio.run(run(TODAY))
    assert notice.chat_id == pid and "минимальный платёж" in notice.text and "4 200" in notice.text
    assert notice.app_param == f"{pid}_credits"
    assert asyncio.run(run(TODAY)) == []  # второй раз не шлём
    [notice] = asyncio.run(run(TODAY + dt.timedelta(days=3)))
    assert notice.text.startswith("💳 Сегодня")

    # Срок продлили — напоминания по новым датам начинаются заново
    url = f"/api/chats/{pid}/credit-cards/{card['id']}"
    client.post(f"{url}/extend", headers=h, json={"grace_until": (TODAY + dt.timedelta(days=1)).isoformat()})
    [notice] = asyncio.run(run(TODAY))
    assert "без процентов" in notice.text

    # Без долга не напоминаем
    client.post(f"{url}/adjust", headers=h, json={"debt": 0})
    client.post(f"{url}/extend", headers=h, json={"grace_until": TODAY.isoformat()})
    assert asyncio.run(run(TODAY)) == []


def test_cards_move_out_of_recurring_payments(tmp_path, monkeypatch):
    db = tmp_path / "old.sqlite3"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    monkeypatch.setattr("app.shared.config.settings.DATABASE_PATH", str(db))

    command.upgrade(cfg, "e2a6c8f0b154")
    con = sqlite3.connect(db)
    con.execute("insert into chats(id, title, currency, created_at, is_personal, plan_start_day)"
                " values (-1, 'Семья', 'RUB', '2026-01-01', 0, 1)")
    con.execute("insert into chats(id, title, currency, created_at, is_personal, plan_start_day)"
                " values (7, 'Мои финансы', 'RUB', '2026-01-01', 1, 1)")
    con.execute("insert into members(id, chat_id, tg_user_id, full_name, is_active, is_hidden, added_at)"
                " values (1, -1, 7, 'A', 1, 0, '2026-01-01')")
    con.execute("insert into members(id, chat_id, tg_user_id, full_name, is_active, is_hidden, added_at)"
                " values (2, 7, 7, 'A', 1, 0, '2026-01-01')")
    for rid, chat, payer, kind, debt in [(1, -1, 1, "card", 50000), (2, 7, 2, "card", None), (3, 7, 2, "loan", None)]:
        con.execute(
            "insert into recurring_expenses(id, chat_id, title, amount, category, subcategory, payer_member_id,"
            " split_type, day_of_month, kind, debt, is_active, created_by_member_id, created_at)"
            " values (?, ?, ?, 3000, 'Кредиты', 'Кредитная карта', ?, 'equal', 5, ?, ?, 1, ?, '2026-01-01')",
            (rid, chat, f"r{rid}", payer, kind, debt, payer),
        )
        con.execute("insert into recurring_participants(recurring_id, member_id) values (?, ?)", (rid, payer))
    con.execute("insert into recurring_payments(recurring_id, month, due_date, status, amount, reminders_sent,"
                " asked, paid_at) values (1, '2026-09', '2026-09-05', 'paid', 7000, '', 1, '2026-09-04 10:00:00')")
    con.commit()
    con.close()

    command.upgrade(cfg, "f4c8e2a6b910")
    con = sqlite3.connect(db)
    cards = con.execute("select id, chat_id, title, debt, min_payment, payment_date from credit_cards order by title").fetchall()
    assert [(c[1], c[2], c[3], c[4]) for c in cards] == [(7, "r1", 50000, 3000), (7, "r2", 0, 3000)]
    assert all(dt.date.fromisoformat(c[5]).day == 5 and dt.date.fromisoformat(c[5]) >= TODAY for c in cards)
    ops = con.execute("select card_id, kind, amount, op_date from credit_operations").fetchall()
    assert ops == [(cards[0][0], "payment", 7000, "2026-09-04")]
    assert con.execute("select id, kind from recurring_expenses").fetchall() == [(3, "loan")]
    assert con.execute("select count(*) from recurring_payments").fetchone() == (0,)
    assert con.execute("select recurring_id from recurring_participants").fetchall() == [(3,)]
