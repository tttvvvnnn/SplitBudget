"""PDF-отчёт «Моих финансов»: статистика, план со всеми тратами статей, покупки по картам."""
from __future__ import annotations

import asyncio
import datetime as dt

from app.shared import pdf_report
from app.shared.database import async_session_maker
from app.shared.models import Chat

TODAY = dt.date.today()


def _personal(client, seeded_chat, auth_header):
    h = auth_header(seeded_chat.alice_init_data)
    pid = client.get("/api/personal", headers=h).json()["id"]
    me = client.get(f"/api/chats/{pid}/me", headers=h).json()["member"]["id"]
    return pid, me, h


def _expense(client, pid, me, h, amount, category, title, card_id=None):
    data = {"title": title, "amount": str(amount), "category": category, "expense_date": TODAY.isoformat(),
            "payer_member_id": str(me), "split_type": "equal", "participant_ids": f"[{me}]"}
    if card_id is not None:
        data["card_id"] = str(card_id)
    r = client.post(f"/api/chats/{pid}/expenses", headers=h, data=data)
    assert r.status_code == 201, r.text


def _seed(client, seeded_chat, auth_header, monkeypatch):
    async def silent(*args, **kwargs):
        return None

    monkeypatch.setattr("app.api.routers.expenses.notify_budget_alerts", silent)
    pid, me, h = _personal(client, seeded_chat, auth_header)
    r = client.post(f"/api/chats/{pid}/plan/items", headers=h,
                    json={"title": "Еда", "kind": "spend", "percent": 30, "categories": ["Продукты"]})
    assert r.status_code == 201, r.text
    r = client.post(f"/api/chats/{pid}/income", headers=h,
                    json={"title": "Зарплата", "amount": 100000, "income_date": TODAY.isoformat()})
    assert r.status_code == 201, r.text
    _expense(client, pid, me, h, 1500, "Продукты", "Пятёрочка")
    _expense(client, pid, me, h, 700, "Развлечения", "Кино")
    card = client.post(f"/api/chats/{pid}/credit-cards", headers=h, json={"title": "Озон карта", "debt": 0}).json()
    _expense(client, pid, me, h, 5000, "Покупки", "Наушники", card_id=card["id"])
    return pid, h


def test_report_has_plan_entries_and_card_purchases(client, seeded_chat, auth_header, monkeypatch):
    pid, _ = _seed(client, seeded_chat, auth_header, monkeypatch)

    async def build():
        async with async_session_maker() as session:
            chat = await session.get(Chat, pid)
            return await pdf_report.collect(session, chat, TODAY)

    report = asyncio.run(build())
    assert float(report.stats.total) == 2200.0  # покупка кредиткой — не трата
    assert float(report.income_total) == 100000.0
    food = next(i for i in report.plan.items if i.title == "Еда")
    assert [e.title for e in report.entries[food.id]] == ["Пятёрочка"]
    assert [e.title for e in report.entries[None]] == ["Кино"]  # без статьи — «Свободно»
    assert [e.title for e, _ in report.cards[0].purchases] == ["Наушники"]

    for theme in pdf_report.THEMES:
        html = pdf_report.render_html(report, theme)
        for text in ("Пятёрочка", "Кино", "Наушники", "Еда", "Свободно", "<svg"):
            assert text in html


def test_download_and_send(client, seeded_chat, auth_header, monkeypatch):
    pid, h = _seed(client, seeded_chat, auth_header, monkeypatch)
    r = client.get(f"/api/chats/{pid}/report.pdf?theme=dark", headers=h)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert "attachment" in r.headers["content-disposition"]

    sent = []

    async def fake_send(chat_id, document, caption=None):
        sent.append((chat_id, document.filename))

    monkeypatch.setattr("app.api.routers.pdf.bot.send_document", fake_send)
    r = client.post(f"/api/chats/{pid}/report/send", headers=h, json={"theme": "light"})
    assert r.status_code == 200, r.text
    assert sent and sent[0][0] == seeded_chat.alice_tg_id and sent[0][1].endswith(".pdf")


def test_family_chat_has_no_report(client, seeded_chat, auth_header):
    h = auth_header(seeded_chat.alice_init_data)
    r = client.get(f"/api/chats/{seeded_chat.chat_id}/report.pdf", headers=h)
    assert r.status_code == 400
