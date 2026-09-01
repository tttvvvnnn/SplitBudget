"""Расчёт баланса, упрощение долгов и погашение (settle up)."""
from __future__ import annotations

import json


def _add_expense(client, seeded_chat, auth_header, *, amount, payer_id, participant_ids):
    return client.post(
        f"/api/chats/{seeded_chat.chat_id}/expenses",
        headers=auth_header(seeded_chat.alice_init_data),
        data={
            "title": "Трата",
            "amount": amount,
            "category": "Другое",
            "expense_date": "2026-09-01",
            "payer_member_id": str(payer_id),
            "split_type": "equal",
            "participant_ids": json.dumps(participant_ids),
        },
    )


def test_balance_after_single_expense(client, seeded_chat, auth_header):
    r = _add_expense(
        client, seeded_chat, auth_header,
        amount="1000", payer_id=seeded_chat.alice_member_id,
        participant_ids=[seeded_chat.alice_member_id, seeded_chat.bob_member_id],
    )
    assert r.status_code == 201, r.text

    r = client.get(f"/api/chats/{seeded_chat.chat_id}/balances", headers=auth_header(seeded_chat.alice_init_data))
    assert r.status_code == 200, r.text
    body = r.json()
    net = {b["member_id"]: float(b["net"]) for b in body["balances"]}
    assert net[seeded_chat.alice_member_id] == 500.0
    assert net[seeded_chat.bob_member_id] == -500.0

    debts = body["simplified_debts"]
    assert len(debts) == 1
    assert debts[0]["from_member_id"] == seeded_chat.bob_member_id
    assert debts[0]["to_member_id"] == seeded_chat.alice_member_id
    assert float(debts[0]["amount"]) == 500.0


def test_settlement_zeroes_balance(client, seeded_chat, auth_header):
    _add_expense(
        client, seeded_chat, auth_header,
        amount="1000", payer_id=seeded_chat.alice_member_id,
        participant_ids=[seeded_chat.alice_member_id, seeded_chat.bob_member_id],
    )

    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/settlements",
        headers=auth_header(seeded_chat.alice_init_data),
        json={
            "from_member_id": seeded_chat.bob_member_id,
            "to_member_id": seeded_chat.alice_member_id,
            "amount": 500.0,
        },
    )
    assert r.status_code == 201, r.text

    r = client.get(f"/api/chats/{seeded_chat.chat_id}/balances", headers=auth_header(seeded_chat.alice_init_data))
    net = {b["member_id"]: float(b["net"]) for b in r.json()["balances"]}
    assert net[seeded_chat.alice_member_id] == 0.0
    assert net[seeded_chat.bob_member_id] == 0.0
    assert r.json()["simplified_debts"] == []


def test_settlement_to_self_rejected(client, seeded_chat, auth_header):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/settlements",
        headers=auth_header(seeded_chat.alice_init_data),
        json={
            "from_member_id": seeded_chat.alice_member_id,
            "to_member_id": seeded_chat.alice_member_id,
            "amount": 10,
        },
    )
    assert r.status_code == 400


def test_balances_conserve_total(client, seeded_chat, auth_header):
    """Сумма всех net-балансов всегда должна быть равна нулю — деньги никуда не пропадают
    и не берутся из ниоткуда, независимо от того, кто и сколько раз платил."""
    _add_expense(
        client, seeded_chat, auth_header,
        amount="750", payer_id=seeded_chat.bob_member_id,
        participant_ids=[seeded_chat.alice_member_id, seeded_chat.bob_member_id],
    )
    _add_expense(
        client, seeded_chat, auth_header,
        amount="333", payer_id=seeded_chat.alice_member_id,
        participant_ids=[seeded_chat.alice_member_id, seeded_chat.bob_member_id],
    )

    r = client.get(f"/api/chats/{seeded_chat.chat_id}/balances", headers=auth_header(seeded_chat.alice_init_data))
    total = sum(float(b["net"]) for b in r.json()["balances"])
    assert abs(total) < 0.01
