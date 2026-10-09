"""Прогноз трат на конец месяца и предупреждение «лимит будет превышен»."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from decimal import Decimal

from app.shared.database import async_session_maker
from app.shared.forecast import due_forecast_alerts, forecast
from app.shared.models import Chat


def _add_expense(client, headers, chat_id, payer_id, participant_ids, amount, date, category="Продукты"):
    r = client.post(
        f"/api/chats/{chat_id}/expenses",
        headers=headers,
        data={
            "title": "Трата", "amount": str(amount), "category": category, "expense_date": date,
            "payer_member_id": str(payer_id), "split_type": "equal", "participant_ids": json.dumps(participant_ids),
        },
    )
    assert r.status_code == 201, r.text


def _run(fn, chat_id, today):
    async def _go():
        async with async_session_maker() as session:
            result = await fn(session, await session.get(Chat, chat_id), today)
            await session.commit()
            return result

    return asyncio.run(_go())


def test_forecast_and_alert(client, seeded_chat, auth_header):
    h = auth_header(seeded_chat.alice_init_data)
    cid, a = seeded_chat.chat_id, seeded_chat.alice_member_id
    # Сентябрь: 30 дней. За 10 дней — 3 000 на продукты → темп 300/день
    for day in (2, 6, 9):
        _add_expense(client, h, cid, a, [a], 1000, f"2026-09-0{day}")
    today = dt.date(2026, 9, 10)

    fc = _run(forecast, cid, today)
    assert fc.days_left == 20
    assert fc.get(("", None)) == Decimal("9000.00")
    assert fc.get(("Продукты", None)) == Decimal("9000.00")
    # До 5-го числа прогноза нет
    assert _run(forecast, cid, dt.date(2026, 9, 4)) is None

    client.post(f"/api/chats/{cid}/budgets", headers=h, json={"category": "Продукты", "amount": "8000"})
    client.post(f"/api/chats/{cid}/budgets", headers=h, json={"category": "", "amount": "20000"})

    async def _alerts(day):
        async with async_session_maker() as session:
            out = await due_forecast_alerts(session, day)
            await session.commit()
            return [text for chat_id, text in out if chat_id == cid]

    alerts = asyncio.run(_alerts(today))
    assert len(alerts) == 1  # общий лимит 20 000 по прогнозу не превышен
    assert "«Продукты» будет превышен" in alerts[0] and "9 000" in alerts[0]
    assert "250 RUB в день" in alerts[0]  # (8 000 − 3 000) / 20 дней
    # Второй раз за месяц не предупреждаем
    assert asyncio.run(_alerts(today + dt.timedelta(days=1))) == []
