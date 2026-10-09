"""Обязательные платежи: напоминания, «Оплачено?», карта с долгом, резерв в лимитах."""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from sqlalchemy import select

from app.bot import scheduler
from app.shared.database import async_session_maker
from app.shared.models import Expense, ExpenseShare
from app.shared.obligations import month_of, next_month

TODAY = dt.date.today()
MONTH = month_of(TODAY)
NEXT = next_month(MONTH)


@pytest.fixture()
def notices(monkeypatch):
    sent = []

    async def fake(items):
        sent.extend(items)

    async def silent(*args, **kwargs):
        return None

    monkeypatch.setattr(scheduler, "notify_obligations", fake)
    monkeypatch.setattr("app.api.routers.obligations.notify_payment_marked", silent)
    monkeypatch.setattr("app.api.routers.obligations.notify_budget_alerts", silent)
    return sent


def _template(client, seeded_chat, h, **extra):
    body = {
        "title": "Аренда",
        "amount": 40000,
        "category": "Дом",
        "subcategory": "Аренда",
        "payer_member_id": seeded_chat.alice_member_id,
        "split_type": "equal",
        "day_of_month": 15,
        "participants": [
            {"member_id": seeded_chat.alice_member_id},
            {"member_id": seeded_chat.bob_member_id},
        ],
        "kind": "rent",
    }
    body.update(extra)
    r = client.post(f"/api/chats/{seeded_chat.chat_id}/recurring", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _obligations(client, chat_id, h, month):
    r = client.get(f"/api/chats/{chat_id}/obligations?month={month}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _expenses_of(recurring_id):
    async def _run():
        async with async_session_maker() as session:
            result = await session.execute(select(Expense).where(Expense.recurring_id == recurring_id))
            expenses = result.scalars().all()
            out = []
            for e in expenses:
                shares = await session.execute(select(ExpenseShare.amount).where(ExpenseShare.expense_id == e.id))
                out.append((float(e.amount), sorted(float(a) for a in shares.scalars().all())))
            return out

    return asyncio.run(_run())



def test_reminders_then_ask_and_pay(client, seeded_chat, auth_header, notices):
    h = auth_header(seeded_chat.alice_init_data)
    t = _template(client, seeded_chat, h, title="Аренда-напоминания")
    due = dt.date.fromisoformat(f"{NEXT}-15")

    def run(day):
        notices.clear()
        asyncio.run(scheduler.process_obligations(day))
        return [n for n in notices if "Аренда-напоминания" in n.text]

    assert run(due - dt.timedelta(days=8)) == []
    week = run(due - dt.timedelta(days=7))
    assert len(week) == 1 and "Через 7 дней" in week[0].text and week[0].ask_payment_id is None
    assert run(due - dt.timedelta(days=7)) == []  # повторный запуск не дублирует
    assert "Через 3 дня" in run(due - dt.timedelta(days=3))[0].text
    assert "Завтра" in run(due - dt.timedelta(days=1))[0].text
    ask = run(due)
    assert len(ask) == 1 and "Оплачено?" in ask[0].text and ask[0].chat_id == seeded_chat.chat_id
    assert run(due) == []
    assert _expenses_of(t["id"]) == []  # без подтверждения трата не записывается

    payment_id = ask[0].ask_payment_id
    r = client.post(f"/api/chats/{seeded_chat.chat_id}/payments/{payment_id}/pay", headers=h, json={})
    assert r.status_code == 204, r.text
    assert _expenses_of(t["id"]) == [(40000.0, [20000.0, 20000.0])]
    r = client.post(f"/api/chats/{seeded_chat.chat_id}/payments/{payment_id}/pay", headers=h, json={})
    assert r.status_code == 400

    item = _obligations(client, seeded_chat.chat_id, h, NEXT)["items"]
    assert [(i["title"], i["status"]) for i in item] == [("Аренда-напоминания", "paid")]

    r = client.post(f"/api/chats/{seeded_chat.chat_id}/payments/{payment_id}/reset", headers=h)
    assert r.status_code == 204
    assert _expenses_of(t["id"]) == []
    assert _obligations(client, seeded_chat.chat_id, h, NEXT)["items"][0]["status"] == "pending"


def test_pay_other_amount_scales_custom_split(client, seeded_chat, auth_header, notices):
    h = auth_header(seeded_chat.alice_init_data)
    t = _template(
        client, seeded_chat, h, title="Коммуналка", amount=300, split_type="custom", kind="other",
        participants=[
            {"member_id": seeded_chat.alice_member_id, "custom_amount": 100},
            {"member_id": seeded_chat.bob_member_id, "custom_amount": 200},
        ],
    )
    payment_id = _obligations(client, seeded_chat.chat_id, h, NEXT)["items"][0]["payment_id"]
    r = client.post(f"/api/chats/{seeded_chat.chat_id}/payments/{payment_id}/pay", headers=h, json={"amount": 600})
    assert r.status_code == 204, r.text
    assert _expenses_of(t["id"]) == [(600.0, [200.0, 400.0])]


def test_credit_card_reduces_debt_without_expense(client, seeded_chat, auth_header, notices):
    h = auth_header(seeded_chat.alice_init_data)
    t = _template(
        client, seeded_chat, h, title="Карта", amount=5000, kind="card", debt=100000,
        participants=[{"member_id": seeded_chat.alice_member_id}],
    )
    data = _obligations(client, seeded_chat.chat_id, h, NEXT)
    item = data["items"][0]
    assert item["counts"] is False and float(item["debt"]) == 100000.0
    assert float(data["total"]) == 0.0  # платёж по карте не трата

    cid = seeded_chat.chat_id
    assert client.post(f"/api/chats/{cid}/payments/{item['payment_id']}/pay", headers=h, json={}).status_code == 204
    assert _expenses_of(t["id"]) == []
    assert float(_obligations(client, cid, h, NEXT)["items"][0]["debt"]) == 95000.0
    assert client.post(f"/api/chats/{cid}/payments/{item['payment_id']}/reset", headers=h).status_code == 204
    assert float(_obligations(client, cid, h, NEXT)["items"][0]["debt"]) == 100000.0


def test_skip_and_loan_end(client, seeded_chat, auth_header, notices):
    h = auth_header(seeded_chat.alice_init_data)
    cid = seeded_chat.chat_id
    _template(client, seeded_chat, h, title="Кредит", kind="loan", amount=15000, end_month=NEXT)
    after = next_month(NEXT)
    assert [i["title"] for i in _obligations(client, cid, h, NEXT)["items"]] == ["Кредит"]
    assert _obligations(client, cid, h, after)["items"] == []  # кредит закончился

    item = _obligations(client, cid, h, NEXT)["items"][0]
    assert client.post(f"/api/chats/{cid}/payments/{item['payment_id']}/skip", headers=h).status_code == 204
    data = _obligations(client, cid, h, NEXT)
    assert data["items"][0]["status"] == "skipped" and float(data["total"]) == 0.0


def test_personal_view_shows_family_share(client, seeded_chat, auth_header, notices):
    alice_h = auth_header(seeded_chat.alice_init_data)
    personal = client.get("/api/personal", headers=alice_h).json()["id"]
    _template(client, seeded_chat, alice_h, title="Семейная аренда")
    data = _obligations(client, personal, alice_h, NEXT)
    item = next(i for i in data["items"] if i["title"] == "Семейная аренда")
    assert float(item["amount"]) == 40000.0 and float(item["share"]) == 20000.0
    assert item["chat_id"] == seeded_chat.chat_id and item["chat_title"]
    assert float(data["pending"]) >= 20000.0


@pytest.mark.skipif(TODAY.day > 28, reason="платёж в этом месяце можно завести только до 28-го")
def test_pending_payment_reserved_in_budget(client, seeded_chat, auth_header, notices):
    h = auth_header(seeded_chat.alice_init_data)
    cid = seeded_chat.chat_id
    _template(client, seeded_chat, h, title="Аренда-резерв", day_of_month=max(TODAY.day, 1))
    client.post(f"/api/chats/{cid}/budgets", headers=h, json={"category": "", "amount": 100000})
    client.post(f"/api/chats/{cid}/budgets", headers=h, json={"category": "Дом", "amount": 50000})

    budgets = {b["label"]: b for b in client.get(f"/api/chats/{cid}/budgets?month={MONTH}", headers=h).json()}
    assert float(budgets["Весь месяц"]["reserved"]) == 40000.0
    assert float(budgets["Дом"]["reserved"]) == 40000.0

    item = _obligations(client, cid, h, MONTH)["items"][0]
    client.post(f"/api/chats/{cid}/payments/{item['payment_id']}/pay", headers=h, json={})
    budgets = {b["label"]: b for b in client.get(f"/api/chats/{cid}/budgets?month={MONTH}", headers=h).json()}
    assert float(budgets["Весь месяц"]["reserved"]) == 0.0
    assert float(budgets["Весь месяц"]["spent"]) == 40000.0


def test_payment_of_other_chat_is_404(client, seeded_chat, auth_header, notices):
    alice_h = auth_header(seeded_chat.alice_init_data)
    _template(client, seeded_chat, alice_h, title="Чужая")
    item = _obligations(client, seeded_chat.chat_id, alice_h, NEXT)["items"][0]
    personal = client.get("/api/personal", headers=alice_h).json()["id"]
    r = client.post(f"/api/chats/{personal}/payments/{item['payment_id']}/pay", headers=alice_h, json={})
    assert r.status_code == 404


def test_bot_buttons_pay_and_check_chat(client, seeded_chat, auth_header, notices, monkeypatch):
    from types import SimpleNamespace

    from app.bot.handlers import payments

    async def silent(*args, **kwargs):
        return None

    monkeypatch.setattr(payments, "notify_budget_alerts", silent)
    h = auth_header(seeded_chat.alice_init_data)
    t = _template(client, seeded_chat, h, title="Аренда-кнопка")
    payment_id = _obligations(client, seeded_chat.chat_id, h, NEXT)["items"][0]["payment_id"]

    def press(action, chat_id):
        log = {}

        async def answer(text=None, show_alert=False):
            log["answer"] = text

        async def edit_text(text):
            log["edited"] = text

        cb = SimpleNamespace(
            data=f"rp:{action}:{payment_id}",
            from_user=SimpleNamespace(id=seeded_chat.bob_tg_id),
            message=SimpleNamespace(chat=SimpleNamespace(id=chat_id), edit_text=edit_text),
            answer=answer,
        )
        asyncio.run(payments.on_payment_button(cb))
        return log

    assert press("pay", -1)["answer"] == "Это не ваш платёж"  # кнопка из чужого чата
    assert _expenses_of(t["id"]) == []
    log = press("pay", seeded_chat.chat_id)
    assert "Оплачено" in log["edited"] and "Боб" in log["edited"]
    assert _expenses_of(t["id"]) == [(40000.0, [20000.0, 20000.0])]
    assert press("pay", seeded_chat.chat_id)["answer"] == "Платёж уже отмечен оплаченным"
