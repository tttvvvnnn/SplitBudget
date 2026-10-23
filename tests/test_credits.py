"""Вкладка «Кредиты»: кредитные карты с операциями, кредиты, напоминания, перенос карт из платежей."""
from __future__ import annotations

import asyncio
import json
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


def _post_expense(client, chat_id, h, payer, amount, card_id=None, participants=None, title="Покупка"):
    data = {"title": title, "amount": str(amount), "category": "Продукты", "expense_date": TODAY.isoformat(),
            "payer_member_id": str(payer), "split_type": "equal",
            "participant_ids": json.dumps(participants or [payer])}
    if card_id is not None:
        data["card_id"] = str(card_id)
    return client.post(f"/api/chats/{chat_id}/expenses", headers=h, data=data)


def _debt(client, pid, h, card_id):
    card = next(c for c in client.get(f"/api/chats/{pid}/credits", headers=h).json()["cards"] if c["id"] == card_id)
    return float(card["debt"]), card


def test_purchases_by_card_grow_debt(client, seeded_chat, auth_header):
    pid, h = _personal(client, seeded_chat, auth_header)
    me = client.get(f"/api/chats/{pid}/me", headers=h).json()["member"]["id"]
    card = _card(client, pid, h, debt=1000)
    url = f"/api/chats/{pid}/credit-cards/{card['id']}"

    # Личная покупка картой: трата как обычно, долг растёт
    r = _post_expense(client, pid, h, me, 500, card["id"])
    assert r.status_code == 201, r.text
    expense = r.json()
    assert expense["card_id"] == card["id"]
    debt, data = _debt(client, pid, h, card["id"])
    assert debt == 1500.0 and [p["title"] for p in data["purchases"]] == ["Покупка"]

    # Семейная трата моей картой: долг растёт на всю сумму, Боб карту не видит
    alice, bob = seeded_chat.alice_member_id, seeded_chat.bob_member_id
    fam = _post_expense(client, seeded_chat.chat_id, h, alice, 3000, card["id"], [alice, bob]).json()
    assert _debt(client, pid, h, card["id"])[0] == 4500.0
    bob_h = auth_header(seeded_chat.bob_init_data)
    bob_view = client.get(f"/api/chats/{seeded_chat.chat_id}/expenses", headers=bob_h).json()
    assert next(e for e in bob_view if e["id"] == fam["id"])["card_id"] is None
    # Боб правит трату — карта остаётся
    client.patch(f"/api/chats/{seeded_chat.chat_id}/expenses/{fam['id']}", headers=bob_h,
                 data={"amount": "2000", "payer_member_id": str(alice)})
    assert _debt(client, pid, h, card["id"])[0] == 3500.0
    # Плательщиком стал Боб — моя карта к трате больше не относится
    client.patch(f"/api/chats/{seeded_chat.chat_id}/expenses/{fam['id']}", headers=bob_h,
                 data={"payer_member_id": str(bob)})
    assert _debt(client, pid, h, card["id"])[0] == 1500.0

    # Внести всё, потом трату удалили — долг уходит ниже нуля не раньше, чем карта переплачена
    client.post(f"{url}/pay", headers=h, json={"amount": 1500})
    assert _debt(client, pid, h, card["id"])[0] == 0.0
    assert client.post(f"{url}/pay", headers=h, json={"amount": 1}).status_code == 400
    client.patch(f"/api/chats/{pid}/expenses/{expense['id']}", headers=h, data={"card_id": ""})
    assert _debt(client, pid, h, card["id"])[0] == -500.0  # переплата: покупку оплатили своими

    # Сверка учитывает покупки
    _post_expense(client, pid, h, me, 700, card["id"])
    client.post(f"{url}/adjust", headers=h, json={"debt": 1000})
    assert _debt(client, pid, h, card["id"])[0] == 1000.0

    # Удалили карту — траты остались, просто без карты
    assert client.delete(url, headers=h).status_code == 204
    titles = [e["title"] for e in _expenses(client, pid, h) if e["card_id"] is None]
    assert titles.count("Покупка") == 2


def test_card_must_be_own(client, seeded_chat, auth_header):
    pid, h = _personal(client, seeded_chat, auth_header)
    card = _card(client, pid, h)
    alice, bob = seeded_chat.alice_member_id, seeded_chat.bob_member_id
    bob_h = auth_header(seeded_chat.bob_init_data)
    # Чужой картой нельзя
    r = _post_expense(client, seeded_chat.chat_id, bob_h, bob, 100, card["id"])
    assert r.status_code == 400 and "Карта не найдена" in r.text
    # Своей картой — только свою трату
    r = _post_expense(client, seeded_chat.chat_id, h, bob, 100, card["id"], [alice, bob])
    assert r.status_code == 400
    client.patch(f"/api/chats/{pid}/credit-cards/{card['id']}", headers=h, json={"title": "Т-Банк", "is_archived": True})
    me = client.get(f"/api/chats/{pid}/me", headers=h).json()["member"]["id"]
    assert _post_expense(client, pid, h, me, 100, card["id"]).status_code == 400


def _callback(user_id, data, log):
    from types import SimpleNamespace

    async def edit_text(text, reply_markup=None):
        log.append((text, reply_markup))

    async def answer(text=None, show_alert=False):
        if text:
            log.append(("answer", text))

    return SimpleNamespace(data=data, from_user=SimpleNamespace(id=user_id),
                           message=SimpleNamespace(edit_text=edit_text, edit_reply_markup=edit_text), answer=answer)


def test_quick_entry_by_card(client, seeded_chat, auth_header, monkeypatch):
    from app.bot.handlers import quick

    async def silent(*args, **kwargs):
        return None

    monkeypatch.setattr(quick, "notify_budget_alerts", silent)
    pid, h = _personal(client, seeded_chat, auth_header)
    card = _card(client, pid, h, title="Альфа", debt=0)
    log = []

    async def answer(text, reply_markup=None):
        log.append((text, reply_markup))

    from types import SimpleNamespace
    msg = SimpleNamespace(from_user=SimpleNamespace(id=pid, username="alice", full_name="Алиса"),
                          text="такси 350", answer=answer)
    asyncio.run(quick.quick_expense(msg))
    buttons = [b.callback_data for row in log[-1][1].inline_keyboard for b in row]
    eid = _expenses(client, pid, h)[0]["id"]
    assert f"qe:card:{eid}:{card['id']}" in buttons

    asyncio.run(quick.on_quick_button(_callback(pid, f"qe:card:{eid}:{card['id']}", log)))
    assert "💳 Альфа" in log[-1][0] and _debt(client, pid, h, card["id"])[0] == 350.0
    # Чужой не может, «Своими» снимает карту
    asyncio.run(quick.on_quick_button(_callback(seeded_chat.bob_tg_id, f"qe:card:{eid}:{card['id']}", log)))
    asyncio.run(quick.on_quick_button(_callback(pid, f"qe:card:{eid}:0", log)))
    assert _debt(client, pid, h, card["id"])[0] == 0.0


def test_bot_card_buttons_and_statement(client, seeded_chat, auth_header):
    from app.bot.handlers import credits as handlers
    from app.shared.credits import plus_month

    assert plus_month(dt.date(2026, 1, 31)) == dt.date(2026, 2, 28)
    assert plus_month(dt.date(2026, 12, 5)) == dt.date(2027, 1, 5)
    pid, h = _personal(client, seeded_chat, auth_header)
    grace = TODAY + dt.timedelta(days=3)
    card = _card(client, pid, h, title="Выписочная", debt=10000, statement_day=TODAY.day,
                 grace_until=grace.isoformat(), payment_date=(TODAY + dt.timedelta(days=1)).isoformat())

    async def run(day):
        async with async_session_maker() as session:
            notices = await due_card_notices(session, day)
            await session.commit()
            return [n for n in notices if "Выписочная" in n.text]

    statement, minimum, grace_notice = asyncio.run(run(TODAY))
    assert "выписка" in statement.text and [b for row in statement.buttons for _, b in row] == [f"cc:ext:{card['id']}"]
    assert [b for row in minimum.buttons for _, b in row] == [
        f"cc:min:{card['id']}", f"cc:full:{card['id']}", f"cc:ext:{card['id']}"]
    assert "без процентов" in grace_notice.text

    log = []
    asyncio.run(handlers.on_card_button(_callback(pid, f"cc:min:{card['id']}", log)))
    assert "внесено 4 200" in log[-1][0] and _debt(client, pid, h, card["id"])[0] == 5800.0
    asyncio.run(handlers.on_card_button(_callback(seeded_chat.bob_tg_id, f"cc:full:{card['id']}", log)))
    assert log[-1] == ("answer", "Карта не найдена")
    asyncio.run(handlers.on_card_button(_callback(pid, f"cc:ext:{card['id']}", log)))
    _, data = _debt(client, pid, h, card["id"])
    assert data["grace_until"] == plus_month(grace).isoformat() and float(data["min_payment"]) == 4200.0
    # Про выписку в этом месяце уже спросили, новые сроки ещё далеко
    assert asyncio.run(run(TODAY)) == []
    asyncio.run(handlers.on_card_button(_callback(pid, f"cc:full:{card['id']}", log)))
    assert "погашен" in log[-1][0] and _debt(client, pid, h, card["id"])[0] == 0.0


def test_debts_in_monthly_report(client, seeded_chat, auth_header):
    from app.shared.models import Chat
    from app.shared.report import build_report

    pid, h = _personal(client, seeded_chat, auth_header)
    me = client.get(f"/api/chats/{pid}/me", headers=h).json()["member"]["id"]
    card = _card(client, pid, h, debt=10000)
    _post_expense(client, pid, h, me, 2000, card["id"])
    client.post(f"/api/chats/{pid}/credit-cards/{card['id']}/pay", headers=h, json={"amount": 5000})
    client.post(f"/api/chats/{pid}/recurring", headers=h, json={
        "title": "Кредит", "amount": 1000, "payer_member_id": me, "day_of_month": 28, "kind": "loan",
        "debt": 50000, "participants": [{"member_id": me}]})

    async def run():
        async with async_session_maker() as session:
            return await build_report(session, await session.get(Chat, pid), TODAY.strftime("%Y-%m"), True)

    text = asyncio.run(run())
    assert "💳 Кредитки: долг 10 000 RUB → 7 000 RUB (покупки +2 000 RUB, внесено −5 000 RUB)" in text
    assert "🏦 Остаток по кредитам: 50 000 RUB" in text


def test_expense_repays_card(client, seeded_chat, auth_header, monkeypatch):
    """Трата в «Кредитах» с repays_card_id — платёж по карте: долг уменьшается, «Внести» не нужно."""
    pid, h = _personal(client, seeded_chat, auth_header)
    me = client.get(f"/api/chats/{pid}/me", headers=h).json()["member"]["id"]
    card = _card(client, pid, h, title="Сплит", debt=10000)
    data = {"title": "Сплит", "amount": "3888", "category": "Кредиты", "expense_date": TODAY.isoformat(),
            "payer_member_id": str(me), "split_type": "equal", "participant_ids": json.dumps([me]),
            "repays_card_id": str(card["id"])}
    r = client.post(f"/api/chats/{pid}/expenses", headers=h, data=data)
    assert r.status_code == 201, r.text
    expense = r.json()
    assert expense["repays_card_id"] == card["id"] and expense["card_id"] is None
    debt, out = _debt(client, pid, h, card["id"])
    assert debt == 6112.0
    assert [(p["title"], p["is_payment"]) for p in out["purchases"]] == [("Сплит", True)]

    # Поправили сумму — долг пересчитался; сняли отметку — вернулся
    client.patch(f"/api/chats/{pid}/expenses/{expense['id']}", headers=h, data={"amount": "4000"})
    assert _debt(client, pid, h, card["id"])[0] == 6000.0
    client.patch(f"/api/chats/{pid}/expenses/{expense['id']}", headers=h, data={"repays_card_id": ""})
    assert _debt(client, pid, h, card["id"])[0] == 10000.0

    # Чужой картой нельзя
    bob_h = auth_header(seeded_chat.bob_init_data)
    bad = {**data, "payer_member_id": str(seeded_chat.bob_member_id),
           "participant_ids": json.dumps([seeded_chat.bob_member_id])}
    assert client.post(f"/api/chats/{seeded_chat.chat_id}/expenses", headers=bob_h, data=bad).status_code == 400

    # Бот: трата в «Кредитах» — кнопки «Погасить», а не «оплачено картой»
    from app.bot.handlers import quick

    async def silent(*args, **kwargs):
        return None

    monkeypatch.setattr(quick, "notify_budget_alerts", silent)
    log = []
    eid = expense["id"]
    asyncio.run(quick.on_quick_button(_callback(pid, f"qe:repay:{eid}:{card['id']}", log)))
    assert "Долг карты «Сплит» уменьшился" in log[-1][0]
    assert _debt(client, pid, h, card["id"])[0] == 6000.0
    buttons = [b.callback_data for row in log[-1][1].inline_keyboard for b in row]
    assert f"qe:repay:{eid}:0" in buttons and not any(b.startswith("qe:card:") for b in buttons)
    asyncio.run(quick.on_quick_button(_callback(pid, f"qe:repay:{eid}:0", log)))
    assert _debt(client, pid, h, card["id"])[0] == 10000.0


def test_card_money_counted_on_payment(client, seeded_chat, auth_header):
    """Деньги (План, «Свободно», итоги) — по оплате: покупка с кредитки станет тратой, когда
    внесёшь платёж на карту. Статистика — по покупке: покупка в своей категории, а погашение
    второй раз не считается."""
    pid, h = _personal(client, seeded_chat, auth_header)
    me = client.get(f"/api/chats/{pid}/me", headers=h).json()["member"]["id"]
    month = TODAY.strftime("%Y-%m")
    for title, categories in (("Еда", ["Продукты"]), ("Кредитки", ["Кредиты"])):
        r = client.post(f"/api/chats/{pid}/plan/items", headers=h,
                        json={"title": title, "kind": "spend", "percent": 20, "categories": categories})
        assert r.status_code == 201, r.text
    card = _card(client, pid, h, debt=0)
    assert client.post(f"/api/chats/{pid}/income", headers=h, json={"title": "Зарплата", "amount": 150000}).status_code == 201
    assert _post_expense(client, pid, h, me, 1000, title="Кофе").status_code == 201
    assert _post_expense(client, pid, h, me, 5000, card_id=card["id"], title="Продукты").status_code == 201
    repay = {"title": "Т-Банк", "amount": "3000", "category": "Кредиты", "expense_date": TODAY.isoformat(),
             "payer_member_id": str(me), "split_type": "equal", "participant_ids": json.dumps([me]),
             "repays_card_id": str(card["id"])}
    assert client.post(f"/api/chats/{pid}/expenses", headers=h, data=repay).status_code == 201
    assert client.post(f"/api/chats/{pid}/credit-cards/{card['id']}/pay", headers=h, json={"amount": 1000}).status_code == 200
    assert _debt(client, pid, h, card["id"])[0] == 1000.0

    data = client.get(f"/api/chats/{pid}/income?month={month}", headers=h).json()
    assert data["has_cards"] is True
    assert float(data["spent"]) == 1000.0 + 3000.0 + 1000.0  # кофе + погашения, без покупки с карты
    assert float(data["card_repaid"]) == 4000.0
    assert float(data["spent_card"]) == 5000.0
    assert float(data["free"]) == 145000.0

    items = {i["title"]: i for i in client.get(f"/api/chats/{pid}/plan", headers=h).json()["items"]}
    assert float(items["Еда"]["spent"]) == 1000.0
    assert float(items["Кредитки"]["spent"]) == 4000.0
    assert float(items["Кредитки"]["card_debt"]) == 1000.0
    assert items["Еда"]["card_debt"] is None

    stats = client.get(f"/api/chats/{pid}/stats?month={month}", headers=h).json()
    assert float(stats["total"]) == 6000.0
    assert "Кредиты" not in [c["category"] for c in stats["by_category"]]
    all_stats = client.get(f"/api/all/stats?month={month}", headers=h).json()
    assert float(all_stats["total"]) == 6000.0

    from app.shared.models import Chat
    from app.shared.report import build_report

    async def run():
        async with async_session_maker() as session:
            return await build_report(session, await session.get(Chat, pid), month, True)

    text = asyncio.run(run())
    assert "Потрачено: <b>6 000 RUB</b>" in text
    assert "💳 Из них в кредит: 5 000 RUB" in text
    assert "💵 Своих денег ушло: 5 000 RUB" in text
    assert "Доходы: 150 000 RUB · осталось 145 000 RUB" in text
