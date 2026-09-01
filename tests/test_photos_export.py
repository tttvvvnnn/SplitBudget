"""Загрузка/авторизованная отдача фото чеков, статистика по категориям, экспорт в Excel."""
from __future__ import annotations

import json

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000155ff1de0000000"
    "0049454e44ae426082"
)


def test_expense_photo_upload_and_authed_fetch(client, seeded_chat, auth_header):
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/expenses",
        headers=auth_header(seeded_chat.alice_init_data),
        data={
            "title": "С чеком",
            "amount": "50",
            "category": "Другое",
            "expense_date": "2026-09-06",
            "payer_member_id": str(seeded_chat.alice_member_id),
            "split_type": "equal",
            "participant_ids": json.dumps([seeded_chat.alice_member_id]),
        },
        files={"photo": ("receipt.png", TINY_PNG, "image/png")},
    )
    assert r.status_code == 201, r.text
    photo_url = r.json()["photo_url"]
    assert photo_url

    r = client.get(f"/api/chats/{seeded_chat.chat_id}/{photo_url}", headers=auth_header(seeded_chat.alice_init_data))
    assert r.status_code == 200
    assert r.content == TINY_PNG

    # у второго участника того же чата тоже должен быть доступ к фото
    r = client.get(f"/api/chats/{seeded_chat.chat_id}/{photo_url}", headers=auth_header(seeded_chat.bob_init_data))
    assert r.status_code == 200

    r = client.get(f"/api/chats/{seeded_chat.chat_id}/{photo_url}")
    assert r.status_code in (401, 422)


def test_photo_from_other_chat_not_accessible(client, seeded_chat, auth_header):
    """Простая защита от перебора: имя файла всегда начинается с id своего чата, и обработчик
    это проверяет — так чужой участник не подставит произвольное имя файла из другого чата."""
    r = client.get(
        f"/api/chats/{seeded_chat.chat_id}/photos/999999999_deadbeef.png",
        headers=auth_header(seeded_chat.alice_init_data),
    )
    assert r.status_code == 404


def test_stats_by_category(client, seeded_chat, auth_header):
    for title, amount, category in [("Еда 1", "100", "Еда"), ("Еда 2", "50", "Еда"), ("Такси", "30", "Транспорт")]:
        client.post(
            f"/api/chats/{seeded_chat.chat_id}/expenses",
            headers=auth_header(seeded_chat.alice_init_data),
            data={
                "title": title,
                "amount": amount,
                "category": category,
                "expense_date": "2026-09-10",
                "payer_member_id": str(seeded_chat.alice_member_id),
                "split_type": "equal",
                "participant_ids": json.dumps([seeded_chat.alice_member_id]),
            },
        )

    r = client.get(
        f"/api/chats/{seeded_chat.chat_id}/stats?month=2026-09",
        headers=auth_header(seeded_chat.alice_init_data),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert float(body["total"]) == 180.0
    by_category = {c["category"]: float(c["total"]) for c in body["by_category"]}
    assert by_category["Еда"] == 150.0
    assert by_category["Транспорт"] == 30.0


def test_export_xlsx(client, seeded_chat, auth_header):
    client.post(
        f"/api/chats/{seeded_chat.chat_id}/expenses",
        headers=auth_header(seeded_chat.alice_init_data),
        data={
            "title": "Продукты",
            "amount": "100",
            "category": "Еда",
            "expense_date": "2026-09-01",
            "payer_member_id": str(seeded_chat.alice_member_id),
            "split_type": "equal",
            "participant_ids": json.dumps([seeded_chat.alice_member_id, seeded_chat.bob_member_id]),
        },
    )

    r = client.get(
        f"/api/chats/{seeded_chat.chat_id}/export?month=2026-09",
        headers=auth_header(seeded_chat.alice_init_data),
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert len(r.content) > 1000
