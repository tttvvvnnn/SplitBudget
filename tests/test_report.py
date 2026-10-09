"""Итоги месяца."""
from __future__ import annotations

import asyncio
import datetime as dt
import json

from app.shared.database import async_session_maker
from app.shared.models import Chat
from app.shared.obligations import month_of, next_month
from app.shared.report import build_report, due_reports, prev_month

TODAY = dt.date.today()
MONTH = month_of(TODAY)
PREV_DAY = dt.date.fromisoformat(f"{prev_month(MONTH)}-15")


def _expense(client, chat_id, h, payer, participants, amount, category, date=TODAY):
    r = client.post(
        f"/api/chats/{chat_id}/expenses",
        headers=h,
        data={"title": "Покупка", "amount": str(amount), "category": category, "expense_date": date.isoformat(),
              "payer_member_id": str(payer), "split_type": "equal", "participant_ids": json.dumps(participants)},
    )
    assert r.status_code == 201, r.text


def _build(chat_id, month, partial=False):
    async def run():
        async with async_session_maker() as session:
            return await build_report(session, await session.get(Chat, chat_id), month, partial)

    return asyncio.run(run())


def test_family_report(client, seeded_chat, auth_header):
    h = auth_header(seeded_chat.alice_init_data)
    cid, alice, bob = seeded_chat.chat_id, seeded_chat.alice_member_id, seeded_chat.bob_member_id
    _expense(client, cid, h, alice, [alice, bob], 1000, "Кафе и рестораны", PREV_DAY)
    _expense(client, cid, h, alice, [alice, bob], 3000, "Кафе и рестораны")
    _expense(client, cid, h, bob, [alice, bob], 1000, "Продукты")
    client.post(f"/api/chats/{cid}/budgets", headers=h, json={"category": "Кафе и рестораны", "amount": 2000})

    text = _build(cid, MONTH)
    assert "Потрачено: <b>4 000 RUB</b> (↑300% к прошлому месяцу)" in text
    assert text.index("Кафе и рестораны — 3 000") < text.index("Продукты — 1 000")
    assert "Сильнее всего выросло: 🍽️ Кафе и рестораны +2 000" in text
    assert "Превышены лимиты: «Кафе и рестораны» (150%)" in text
    assert "Алиса — 3 000 RUB (75%)" in text and "Боб — 1 000 RUB (25%)" in text

    partial = _build(cid, MONTH, partial=True)
    assert "Пока за" in partial and "к прошлому месяцу" not in partial
    assert _build(cid, next_month(MONTH)) is None


def test_personal_report_with_income_and_once_per_month(client, seeded_chat, auth_header):
    h = auth_header(seeded_chat.alice_init_data)
    pid = client.get("/api/personal", headers=h).json()["id"]
    me = client.get(f"/api/chats/{pid}/me", headers=h).json()["member"]["id"]
    _expense(client, pid, h, me, [me], 20000, "Продукты")
    client.post(f"/api/chats/{pid}/income", headers=h, json={"title": "Зарплата", "amount": 100000})

    first_of_next = dt.date.fromisoformat(f"{next_month(MONTH)}-01")

    async def run(day):
        async with async_session_maker() as session:
            out = await due_reports(session, day)
            await session.commit()
            return dict(out)

    assert asyncio.run(run(first_of_next - dt.timedelta(days=1))) == {}  # не 1-е число
    reports = asyncio.run(run(first_of_next))
    assert "Мои финансы" in reports[pid] and "Доходы: 100 000 RUB · осталось 80 000 RUB" in reports[pid]
    assert pid not in asyncio.run(run(first_of_next))  # повторно не шлём
