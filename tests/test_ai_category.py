"""Подсказки категорий от ИИ (app/shared/ai_category.py) — провайдер подменён заглушкой."""
from __future__ import annotations

import json

import httpx
import pytest

from app.shared import ai_category
from app.shared.ai_category import AiAnswer, _parse, resolve
from app.shared.config import settings

TREE = [
    ("Продукты", "🛒", ["Супермаркет", "Алкоголь"]),
    ("Здоровье", "💊", ["Аптека", "Врачи"]),
    ("Подарки", "🎁", []),
]


@pytest.mark.parametrize(
    "answer, expected",
    [
        (AiAnswer("existing", "здоровье", "аптека"), AiAnswer("existing", "Здоровье", "Аптека")),
        (AiAnswer("existing", "Здоровье", "Стоматолог"), AiAnswer("existing", "Здоровье", None)),
        (AiAnswer("existing", "Табак", None), AiAnswer("unknown")),
        (AiAnswer("new_subcategory", "Здоровье", "стоматолог"), AiAnswer("new_subcategory", "Здоровье", "Стоматолог")),
        (AiAnswer("new_subcategory", "Здоровье", "Аптека"), AiAnswer("existing", "Здоровье", "Аптека")),
        (AiAnswer("new_category", "Табак", "Вейпы", "🚬"), AiAnswer("new_category", "Табак", "Вейпы", "🚬")),
        (AiAnswer("new_category", "Подарки", None, "🎁"), AiAnswer("existing", "Подарки", None)),
        (AiAnswer("new_category", "Другое", None, None), AiAnswer("unknown")),
        (AiAnswer("unknown", "Подарки"), AiAnswer("unknown")),
    ],
)
def test_resolve_against_tree(answer, expected):
    assert resolve(answer, TREE) == expected


def test_parse_plain_and_fenced_json():
    raw = '{"action": "existing", "category": "Подарки", "subcategory": null, "icon": null}'
    assert _parse(raw) == AiAnswer("existing", "Подарки")
    assert _parse(f"```json\n{raw}\n```") == AiAnswer("existing", "Подарки")
    with pytest.raises(ValueError):
        _parse('{"action": "delete_everything"}')


@pytest.fixture()
def fake_ai(monkeypatch):
    """Включает ИИ и подменяет провайдера: ответы по названию траты, счётчик вызовов."""
    calls: list[tuple[str, str, list[str]]] = []
    answers: dict[str, AiAnswer | Exception] = {}

    async def _ask(title, tree, examples):
        calls.append((title, tree, examples))
        answer = answers.get(title.lower(), AiAnswer("unknown"))
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(settings, "AI_API_KEY", "test-key")
    monkeypatch.setattr(ai_category, "ask_provider", _ask)
    return calls, answers


def _suggest(client, seeded_chat, headers, title, ai=True):
    params = {"title": title}
    if ai:
        params["ai"] = "1"
    r = client.get(f"/api/chats/{seeded_chat.chat_id}/category-suggestion", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_ai_is_asked_only_when_learned_and_dictionary_are_silent(client, seeded_chat, auth_header, fake_ai):
    calls, answers = fake_ai
    headers = auth_header(seeded_chat.alice_init_data)
    answers["зубной"] = AiAnswer("existing", "Здоровье", "Врачи")

    # Словарь знает — ИИ не нужен
    assert _suggest(client, seeded_chat, headers, "Пятёрочка")["source"] == "dictionary"
    # Без ai=1 ИИ не спрашиваем (это быстрые запросы на каждую букву)
    assert _suggest(client, seeded_chat, headers, "Зубной", ai=False) is None
    assert calls == []

    s = _suggest(client, seeded_chat, headers, "Зубной")
    assert s == {"category": "Здоровье", "subcategory": "Врачи", "source": "ai", "proposal": None}
    title, tree, _ = calls[0]
    assert title == "Зубной" and "Здоровье: Аптека, Врачи" in tree and "Другое" not in tree

    # Повтор — из кэша, провайдера второй раз не спрашиваем
    assert _suggest(client, seeded_chat, headers, "зубной")["source"] == "ai"
    assert len(calls) == 1

    # Короткое название не отправляем
    assert _suggest(client, seeded_chat, headers, "зу") is None
    assert len(calls) == 1


def _tree(client, seeded_chat, headers):
    r = client.get(f"/api/chats/{seeded_chat.chat_id}/categories", headers=headers)
    return {n["name"]: n for n in r.json()}


def test_ai_proposes_new_category_and_creates_on_accept(client, seeded_chat, auth_header, fake_ai):
    calls, answers = fake_ai
    headers = auth_header(seeded_chat.alice_init_data)
    answers["вейп"] = AiAnswer("new_category", "Табак", "Вейпы", "🚬")
    s = _suggest(client, seeded_chat, headers, "Вейп")
    assert s["category"] is None
    assert s["proposal"] == {"action": "new_category", "category": "Табак", "subcategory": "Вейпы", "icon": "🚬"}

    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/category-suggestion/accept", params={"title": "вейп"}, headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["category"], body["subcategory"]) == ("Табак", "Вейпы")
    tree = _tree(client, seeded_chat, headers)
    assert tree["Табак"]["icon"] == "🚬" and tree["Табак"]["subcategories"] == ["Вейпы"]

    # Дерево поменялось → кэш устарел, ИИ спросят снова; теперь «Табак › Вейпы» существует
    answers["вейп"] = AiAnswer("existing", "Табак", "Вейпы")
    assert _suggest(client, seeded_chat, headers, "Вейп")["subcategory"] == "Вейпы"
    assert len(calls) == 2


def test_ai_new_subcategory_sets_category_and_unhides_existing(client, seeded_chat, auth_header, fake_ai):
    _, answers = fake_ai
    headers = auth_header(seeded_chat.alice_init_data)
    answers["ортодонт"] = AiAnswer("new_subcategory", "Здоровье", "Ортодонт")
    s = _suggest(client, seeded_chat, headers, "Ортодонт")
    assert (s["category"], s["subcategory"], s["proposal"]["subcategory"]) == ("Здоровье", None, "Ортодонт")

    # ИИ «изобрёл» скрытую базовую категорию — при согласии её просто возвращаем, а не дублируем
    r = client.put(
        f"/api/chats/{seeded_chat.chat_id}/categories/hidden",
        headers=headers,
        json={"name": "Питомцы", "parent": None, "hidden": True},
    )
    assert r.status_code == 200, r.text
    answers["шуршик"] = AiAnswer("new_category", "питомцы", "Корм", "🐈")
    assert _suggest(client, seeded_chat, headers, "Шуршик")["proposal"]["category"] == "Питомцы"
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/category-suggestion/accept",
        params={"title": "Шуршик"},
        headers=headers,
    )
    assert (r.json()["category"], r.json()["subcategory"]) == ("Питомцы", "Корм")
    assert not _tree(client, seeded_chat, headers)["Питомцы"]["hidden"]


def test_ai_declined_proposal_is_not_offered_again(client, seeded_chat, auth_header, fake_ai):
    calls, answers = fake_ai
    headers = auth_header(seeded_chat.alice_init_data)
    answers["вейп"] = AiAnswer("new_category", "Табак", "Вейпы", "🚬")
    assert _suggest(client, seeded_chat, headers, "Вейп")["proposal"] is not None
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/category-suggestion/decline", params={"title": "Вейп"}, headers=headers
    )
    assert r.status_code == 204, r.text
    client.post(f"/api/chats/{seeded_chat.chat_id}/categories", headers=headers, json={"name": "Хобби2", "parent": None})
    assert _suggest(client, seeded_chat, headers, "Вейп") is None  # даже после смены дерева
    assert len(calls) == 1
    r = client.post(
        f"/api/chats/{seeded_chat.chat_id}/category-suggestion/accept", params={"title": "Вейп"}, headers=headers
    )
    assert r.status_code == 404


def test_ai_can_be_turned_off_per_space(client, seeded_chat, auth_header, fake_ai):
    calls, answers = fake_ai
    headers = auth_header(seeded_chat.alice_init_data)
    me = client.get(f"/api/chats/{seeded_chat.chat_id}/me", headers=headers).json()
    assert me["ai_available"] is True and me["chat"]["ai_enabled"] is True
    r = client.put(f"/api/chats/{seeded_chat.chat_id}/ai", headers=headers, json={"ai_enabled": False})
    assert r.status_code == 200 and r.json()["ai_enabled"] is False
    answers["зубной"] = AiAnswer("existing", "Здоровье", "Врачи")
    assert _suggest(client, seeded_chat, headers, "Зубной") is None
    assert calls == []


def test_ai_failure_and_limit(client, seeded_chat, auth_header, fake_ai, monkeypatch):
    calls, answers = fake_ai
    headers = auth_header(seeded_chat.alice_init_data)
    answers["ломается"] = httpx.ConnectTimeout("timeout")
    assert _suggest(client, seeded_chat, headers, "Ломается") is None
    answers["ломается"] = AiAnswer("existing", "Подарки")
    assert _suggest(client, seeded_chat, headers, "Ломается")["category"] == "Подарки"  # ошибку не кэшируем

    monkeypatch.setattr(settings, "AI_DAILY_LIMIT", 1)
    answers["цветы"] = AiAnswer("existing", "Подарки")
    assert _suggest(client, seeded_chat, headers, "Цветы") is None
    assert [c[0] for c in calls] == ["Ломается", "Ломается"]


def test_ai_off_without_key(client, seeded_chat, auth_header, monkeypatch):
    async def _boom(*args):
        raise AssertionError("провайдер не должен вызываться")

    monkeypatch.setattr(ai_category, "ask_provider", _boom)
    headers = auth_header(seeded_chat.alice_init_data)
    assert _suggest(client, seeded_chat, headers, "Зубной") is None


def test_ask_provider_request_shape(monkeypatch):
    """Запрос в формате OpenAI /chat/completions со строгой JSON-схемой."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        content = json.dumps({"action": "existing", "category": "Подарки", "subcategory": None, "icon": None})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        ai_category.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    monkeypatch.setattr(settings, "AI_API_KEY", "k")
    monkeypatch.setattr(settings, "AI_BASE_URL", "https://api.example.com/v1/")

    import asyncio

    answer = asyncio.run(ai_category.ask_provider("Цветы", "- 🎁 Подарки: —", ["роза → Подарки"]))
    assert answer == AiAnswer("existing", "Подарки")
    assert seen["url"] == "https://api.example.com/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    body = seen["body"]
    assert body["model"] == settings.AI_MODEL
    assert body["response_format"]["json_schema"]["strict"] is True
    assert "роза → Подарки" in body["messages"][1]["content"] and "«Цветы»" in body["messages"][1]["content"]
