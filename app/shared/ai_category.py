"""Подсказка категории от ИИ — третий уровень после выученного и словаря (app/shared/autocategory.py).

Наружу уходит только название траты, дерево категорий пространства и несколько выученных
примеров «название → категория». Провайдер — любой OpenAI-совместимый API (Grok, YandexGPT,
посредники, локальная Ollama): AI_BASE_URL + AI_API_KEY + AI_MODEL в .env. Нет ключа, ошибка,
таймаут, выключено в пространстве — молча ничего не подсказываем, форма работает как раньше.

Ответ кэшируется по (чат, название), пока дерево категорий не поменялось, и считается в
дневной лимит AI_DAILY_LIMIT на пространство. ИИ может предложить и новую категорию или
подкатегорию — её создаём только с согласия пользователя (пока предложения не показываются).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared import custom_categories as cc
from app.shared.autocategory import Suggestion, normalize, suggest_category
from app.shared.categories import OTHER
from app.shared.config import settings
from app.shared.models import AiGuess, CategoryRule, Chat

logger = logging.getLogger(__name__)

ACTIONS = ("existing", "new_subcategory", "new_category", "unknown")
MIN_TITLE = 3
EXAMPLES = 30

SYSTEM_PROMPT = """Ты подбираешь категорию для траты в приложении семейного бюджета. Тебе дают \
название траты, список категорий пользователя (категория: подкатегории) и примеры того, как \
пользователь раньше раскладывал траты.

Ответь одним из вариантов:
- existing — подходит существующая категория. category и subcategory пиши точно как в списке. \
subcategory = null, если у категории нет подходящей подкатегории.
- new_subcategory — категория подходит, но не хватает подкатегории, которая пригодится и для \
следующих таких трат. category — из списка, subcategory — новая.
- new_category — ни одна категория не подходит по смыслу. Придумай category, одну \
subcategory и icon (один эмодзи).
- unknown — по названию нельзя понять, на что трата (имя человека, «перевод», «долг», \
бессмыслица). category и subcategory = null.

Правила: сначала всегда пытайся уложиться в существующие категории; «Другое» не выбирай. \
Название может быть магазином, сервисом, брендом или товаром — определи, что это. Новые \
названия — короткие, по-русски, с большой буквы, в стиле списка. icon — только для \
new_category, иначе null."""

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "category_choice",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(ACTIONS)},
                "category": {"type": ["string", "null"]},
                "subcategory": {"type": ["string", "null"]},
                "icon": {"type": ["string", "null"]},
            },
            "required": ["action", "category", "subcategory", "icon"],
            "additionalProperties": False,
        },
    },
}


@dataclass
class AiAnswer:
    action: str  # existing | new_subcategory | new_category | unknown
    category: str | None = None
    subcategory: str | None = None
    icon: str | None = None


def enabled(chat: Chat) -> bool:
    return bool(settings.AI_API_KEY) and chat.ai_enabled


def _visible(nodes: list[cc.Node]) -> list[tuple[str, str, list[str]]]:
    """(категория, иконка, подкатегории), которые пользователь видит в форме, без «Другого»."""
    return [
        (n.name, n.icon, n.visible_subs + n.family_subs)
        for n in nodes
        if not n.hidden and n.name != OTHER
    ]


def tree_text(tree: list[tuple[str, str, list[str]]]) -> str:
    return "\n".join(f"- {icon} {name}: {', '.join(subs) or '—'}" for name, icon, subs in tree)


def _find(names, value: str | None) -> str | None:
    """Имя из списка без учёта регистра и «ё»: ИИ может написать «здоровье» вместо «Здоровье»."""
    if not value:
        return None
    key = normalize(value)
    return next((n for n in names if normalize(n) == key), None)


def _new_name(value: str) -> str:
    name = cc.clean_name(value)
    return name[:1].upper() + name[1:]


def resolve(answer: AiAnswer, tree: list[tuple[str, str, list[str]]]) -> AiAnswer:
    """Сверить ответ ИИ с деревом: «новое», которое на самом деле уже есть, — это existing;
    existing с несуществующей категорией — unknown."""
    subs_of = {name: subs for name, _, subs in tree}
    category = _find(subs_of, answer.category)
    if answer.action == "unknown" or (category is None and answer.action != "new_category"):
        return AiAnswer("unknown")
    if category is None:  # new_category
        if not answer.category or not answer.category.strip() or normalize(answer.category) == normalize(OTHER):
            return AiAnswer("unknown")
        return AiAnswer(
            "new_category",
            _new_name(answer.category),
            _new_name(answer.subcategory) if answer.subcategory and answer.subcategory.strip() else None,
            (answer.icon or cc.DEFAULT_ICON).strip()[:16],
        )
    subcategory = _find(subs_of[category], answer.subcategory)
    if subcategory is not None or not answer.subcategory or not answer.subcategory.strip():
        return AiAnswer("existing", category, subcategory)
    if answer.action == "existing":  # подкатегории, которую ИИ назвал «существующей», нет
        return AiAnswer("existing", category, None)
    return AiAnswer("new_subcategory", category, _new_name(answer.subcategory))


def _parse(content: str) -> AiAnswer:
    content = content.strip()
    if content.startswith("```"):  # на случай провайдера без structured outputs
        content = content.strip("`").removeprefix("json").strip()
    data = json.loads(content)
    action = data.get("action")
    if action not in ACTIONS:
        raise ValueError(f"unexpected action {action!r}")
    return AiAnswer(action, data.get("category"), data.get("subcategory"), data.get("icon"))


async def ask_provider(title: str, tree: str, examples: list[str]) -> AiAnswer:
    """Один запрос к OpenAI-совместимому /chat/completions. Исключения — наверх."""
    user = f"Категории:\n{tree}\n\n"
    if examples:
        user += "Как пользователь раскладывал раньше:\n" + "\n".join(examples) + "\n\n"
    user += f"Трата: «{title}»"
    async with httpx.AsyncClient(timeout=settings.AI_TIMEOUT) as client:
        response = await client.post(
            settings.AI_BASE_URL.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {settings.AI_API_KEY}"},
            json={
                "model": settings.AI_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                "response_format": RESPONSE_FORMAT,
                "max_tokens": 200,
            },
        )
    response.raise_for_status()
    return _parse(response.json()["choices"][0]["message"]["content"])


async def _examples(session: AsyncSession, chat_id: int) -> list[str]:
    result = await session.execute(
        select(CategoryRule)
        .where(CategoryRule.chat_id == chat_id)
        .order_by(CategoryRule.updated_at.desc())
        .limit(EXAMPLES)
    )
    return [
        f"{r.keyword} → {r.category}" + (f" › {r.subcategory}" if r.subcategory else "")
        for r in result.scalars().all()
    ]


async def _calls_today(session: AsyncSession, chat_id: int) -> int:
    since = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=1)
    result = await session.execute(
        select(func.count(AiGuess.id)).where(AiGuess.chat_id == chat_id, AiGuess.created_at >= since)
    )
    return result.scalar_one()


async def ai_guess(session: AsyncSession, chat: Chat, title: str) -> AiAnswer | None:
    """Спросить ИИ (или кэш), куда отнести трату. None — ИИ выключен, не ответил или лимит."""
    if not enabled(chat):
        return None
    keyword = normalize(title)[:255]
    if len(keyword) < MIN_TITLE:
        return None
    tree = _visible(await cc.tree_for(session, chat))
    text = tree_text(tree)
    tree_hash = hashlib.sha1(text.encode()).hexdigest()[:16]

    result = await session.execute(
        select(AiGuess).where(AiGuess.chat_id == chat.id, AiGuess.keyword == keyword)
    )
    cached = result.scalar_one_or_none()
    if cached is not None and cached.tree_hash == tree_hash:
        return resolve(AiAnswer(cached.action, cached.category, cached.subcategory, cached.icon), tree)
    if await _calls_today(session, chat.id) >= settings.AI_DAILY_LIMIT:
        return None

    try:
        answer = resolve(await ask_provider(title.strip()[:255], text, await _examples(session, chat.id)), tree)
    except Exception as exc:  # сеть, таймаут, 4xx/5xx, кривой JSON — без подсказки, но не падаем
        logger.warning("AI category request failed: %s", exc)
        return None

    if cached is None:
        cached = AiGuess(chat_id=chat.id, keyword=keyword)
        session.add(cached)
    cached.tree_hash = tree_hash
    cached.action = answer.action
    cached.category = answer.category
    cached.subcategory = answer.subcategory
    cached.icon = answer.icon
    cached.created_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    await session.flush()
    return answer


async def suggest_with_ai(session: AsyncSession, chat: Chat, title: str) -> Suggestion | None:
    """Выученное → словарь → ИИ. От ИИ пока берём только существующую категорию."""
    suggestion = await suggest_category(session, chat.id, title)
    if suggestion is not None:
        return suggestion
    answer = await ai_guess(session, chat, title)
    if answer is None or answer.action != "existing":
        return None
    return Suggestion(answer.category, answer.subcategory, "ai")


SAMPLE_TITLES = [
    "вейп", "стоматолог", "озон", "wildberries", "подарок маме", "барбершоп", "яндекс плюс",
    "корм для кота", "шиномонтаж", "икеа", "госуслуги штраф", "репетитор по английскому",
    "перевод саше", "зоомагазин", "кальянная", "налог на имущество", "цветы", "химчистка",
]


async def _check(titles: list[str]) -> None:
    """Прогнать названия через провайдера на базовом дереве — проверить ключ и качество."""
    from app.shared.categories import CATEGORY_TREE

    tree = [(name, icon, subs) for name, icon, subs in CATEGORY_TREE if name != OTHER]
    text = tree_text(tree)
    for title in titles:
        try:
            answer = resolve(await ask_provider(title, text, []), tree)
        except Exception as exc:
            print(f"{title:28} ОШИБКА: {exc}")
            continue
        where = " › ".join(x for x in (answer.category, answer.subcategory) if x)
        print(f"{title:28} {answer.action:16} {answer.icon or ''} {where}")


if __name__ == "__main__":
    # Внутри контейнера: python -m app.shared.ai_category [название ...]
    import asyncio
    import sys

    asyncio.run(_check(sys.argv[1:] or SAMPLE_TITLES))
