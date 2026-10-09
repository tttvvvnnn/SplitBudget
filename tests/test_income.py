"""Доходы: поступления, регулярные доходы, «Пришла?», сводка «свободно» и «в день до зарплаты»."""
from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest

from app.bot import scheduler
from app.bot.handlers import payments, quick
from app.shared.database import async_session_maker
from app.shared.income import summary
from app.shared.models import Chat
from app.shared.obligations import month_of, next_month

TODAY = dt.date.today()
MONTH = month_of(TODAY)


@pytest.fixture()
def notices(monkeypatch):
    sent = []

    async def fake(items):
        sent.extend(items)

    monkeypatch.setattr(scheduler, "notify_obligations", fake)
    return sent


def _personal(client, seeded_chat, auth_header):
    h = auth_header(seeded_chat.alice_init_data)
    return client.get("/api/personal", headers=h).json()["id"], h


def _summary(client, pid, h, month=MONTH):
    r = client.get(f"/api/chats/{pid}/income?month={month}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_income_only_in_personal_space(client, seeded_chat, auth_header):
    h = auth_header(seeded_chat.alice_init_data)
    r = client.get(f"/api/chats/{seeded_chat.chat_id}/income?month={MONTH}", headers=h)
    assert r.status_code == 400
    pid, _ = _personal(client, seeded_chat, auth_header)
    r = client.get(f"/api/chats/{pid}/income?month={MONTH}", headers=auth_header(seeded_chat.bob_init_data))
    assert r.status_code == 403


def test_free_money_counts_income_spending_and_obligations(client, seeded_chat, auth_header):
    pid, h = _personal(client, seeded_chat, auth_header)
    me = client.get(f"/api/chats/{pid}/me", headers=h).json()["member"]["id"]
    assert client.post(f"/api/chats/{pid}/income", headers=h, json={"title": "Премия", "amount": 100000}).status_code == 201
    client.post(
        f"/api/chats/{pid}/expenses",
        headers=h,
        data={"title": "Кофе", "amount": "1000", "category": "Другое", "expense_date": TODAY.isoformat(),
              "payer_member_id": str(me), "split_type": "equal", "participant_ids": f"[{me}]"},
    )
    # Обязательный платёж в следующем месяце не влияет на этот
    client.post(
        f"/api/chats/{pid}/recurring",
        headers=h,
        json={"title": "Аренда", "amount": 30000, "payer_member_id": me, "day_of_month": 28,
              "participants": [{"member_id": me}], "kind": "rent"},
    )
    data = _summary(client, pid, h)
    pending = 30000.0 if TODAY.day <= 28 else 0.0
    assert float(data["received"]) == 100000.0 and float(data["spent"]) == 1000.0
    assert float(data["obligations_pending"]) == pending
    assert float(data["free"]) == 100000.0 - 1000.0 - pending


def test_per_day_until_next_salary(client, seeded_chat, auth_header):
    pid, h = _personal(client, seeded_chat, auth_header)
    nm = next_month(MONTH)
    client.post(f"/api/chats/{pid}/income-sources", headers=h, json={"title": "Зарплата", "amount": 120000, "day_of_month": 1})
    client.post(f"/api/chats/{pid}/income", headers=h, json={"title": "Аванс", "amount": 30000})

    async def run():
        async with async_session_maker() as session:
            chat = await session.get(Chat, pid)
            return await summary(session, chat, MONTH, TODAY)

    s = asyncio.run(run())
    next_date = dt.date.fromisoformat(f"{nm}-01")
    assert s.next_title == "Зарплата" and s.next_date == next_date
    assert s.days_to_next == max((next_date - TODAY).days, 1)
    assert float(s.per_day) == pytest.approx(30000 / s.days_to_next, abs=0.01)
    # Зарплата 1-го в этом месяце уже прошла до создания — в ожидаемых её нет
    assert float(s.expected) == 0.0


def test_salary_ask_and_confirm(client, seeded_chat, auth_header, notices):
    pid, h = _personal(client, seeded_chat, auth_header)
    r = client.post(
        f"/api/chats/{pid}/income-sources", headers=h, json={"title": "Зарплата", "amount": 120000, "day_of_month": 10}
    )
    source_id = r.json()["id"]
    day = dt.date.fromisoformat(f"{next_month(MONTH)}-10")

    def run(d):
        notices.clear()
        asyncio.run(scheduler.process_obligations(d))
        return [n for n in notices if n.chat_id == pid]

    assert run(day - dt.timedelta(days=1)) == []
    [ask] = run(day)
    assert "Пришла «Зарплата»" in ask.text and ask.app_param == f"{pid}_income"
    assert run(day) == []  # не спрашиваем дважды

    def press(data, user_id=seeded_chat.alice_tg_id):
        log = {}

        async def edit_text(text):
            log["text"] = text

        async def answer(text=None, show_alert=False):
            log["answer"] = text

        cb = SimpleNamespace(data=data, from_user=SimpleNamespace(id=user_id),
                             message=SimpleNamespace(edit_text=edit_text), answer=answer)
        asyncio.run(payments.on_income_button(cb))
        return log

    yes = ask.buttons[0][0][1]
    assert press(yes, seeded_chat.bob_tg_id)["answer"] == "Доход не найден"
    assert "Записал доход" in press(yes)["text"]
    assert "уже записан" in press(yes)["text"]
    data = _summary(client, pid, h, next_month(MONTH))
    assert [(i["title"], float(i["amount"]), i["source_id"]) for i in data["incomes"]] == [("Зарплата", 120000.0, source_id)]
    assert data["sources"][0]["received"] is True


def test_quick_entry_income(client, seeded_chat, monkeypatch):
    sent = []

    async def answer(text, reply_markup=None):
        sent.append(text)

    msg = SimpleNamespace(
        from_user=SimpleNamespace(id=seeded_chat.alice_tg_id, username="alice", full_name="Алиса"),
        text="+120000 зарплата\nкофе 250",
        answer=answer,
    )
    asyncio.run(quick.quick_expense(msg))
    assert "Доход: Зарплата" in sent[-1] and "1 трат" in sent[-1]
