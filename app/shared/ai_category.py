"""Подсказка категории от ИИ — третий уровень после выученного и словаря (app/shared/autocategory.py).

Наружу уходит только название траты, дерево категорий пространства и несколько выученных
примеров «название → категория». Провайдер — любой OpenAI-совместимый API (Grok, YandexGPT,
посредники, локальная Ollama): AI_BASE_URL + AI_API_KEY + AI_MODEL в .env. Нет ключа, ошибка,
таймаут, выключено в пространстве — молча ничего не подсказываем, форма работает как раньше.

Ответ кэшируется по (чат, название), пока дерево категорий не поменялось, и считается в
дневной лимит AI_DAILY_LIMIT на пространство. ИИ может предложить и новую категорию или
подкатегорию — её создаём только с согласия пользователя (apply_proposal); отказ запоминается
(decline), и то же самое для этого названия больше не предлагается.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared import custom_categories as cc
from app.shared.autocategory import Suggestion, learn_category, normalize, suggest_category
from app.shared.categories import OTHER
from app.shared.config import settings
from app.shared.models import AiGuess, CategoryRule, Chat, Expense

logger = logging.getLogger(__name__)

ACTIONS = ("existing", "new_subcategory", "new_category", "unknown")
NEW_ACTIONS = ("new_subcategory", "new_category")
DECLINED = "declined"  # пользователь отказался от предложения — в кэше навсегда
MIN_TITLE = 3
EXAMPLES = 30
BATCH = 15  # трат в одном запросе к ИИ
MAX_RETRY_WAIT = 3.0  # секунд: дольше на 429 не ждём
UNKNOWN_TTL = dt.timedelta(days=1)  # «не знаю» переспрашиваем: ИИ отвечает не всегда одинаково

SYSTEM_PROMPT = """Ты подбираешь категории для трат в приложении семейного бюджета. Тебе дают \
список категорий пользователя (категория: подкатегории), примеры того, как пользователь раньше \
раскладывал траты, и пронумерованный список названий трат. Для каждой траты, в том же порядке, \
ответь одним из вариантов:
- existing — подходит существующая категория. category и subcategory пиши точно как в списке. \
subcategory = null, если у категории нет подходящей подкатегории.
- new_subcategory — категория подходит, но не хватает подкатегории, которая пригодится и для \
следующих таких трат. category — из списка, subcategory — новая.
- new_category — ни одна категория не подходит по смыслу. Придумай category, одну \
subcategory и icon (один эмодзи).
- unknown — по названию нельзя понять, на что трата (имя человека, «перевод», «долг», \
бессмыслица). category и subcategory = null.

Правила: сначала всегда пытайся уложиться в существующие категории, но только если они \
подходят по смыслу; «Другое» не выбирай. Название может быть магазином, сервисом, брендом или \
товаром — определи, что это. Маркетплейсы (Ozon, Wildberries, Яндекс Маркет, AliExpress) — \
это покупки разных вещей, а не продукты. Новые названия — короткие, по-русски, с большой \
буквы, в стиле списка. icon — только для new_category, иначе null."""

_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "category": {"type": ["string", "null"]},
        "subcategory": {"type": ["string", "null"]},
        "icon": {"type": ["string", "null"]},
    },
    "required": ["action", "category", "subcategory", "icon"],
    "additionalProperties": False,
}

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "category_choices",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"items": {"type": "array", "items": _ANSWER_SCHEMA}},
            "required": ["items"],
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


def _parse(data: dict) -> AiAnswer:
    action = data.get("action")
    if action not in ACTIONS:
        raise ValueError(f"unexpected action {action!r}")
    return AiAnswer(action, data.get("category"), data.get("subcategory"), data.get("icon"))


def _parse_many(content: str, count: int) -> list[AiAnswer]:
    content = content.strip()
    if content.startswith("```"):  # на случай провайдера без structured outputs
        content = content.strip("`").removeprefix("json").strip()
    items = json.loads(content)["items"]
    if len(items) != count:
        raise ValueError(f"expected {count} answers, got {len(items)}")
    return [_parse(item) for item in items]


def _body(user: str) -> dict:
    body = {
        "model": settings.AI_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "response_format": RESPONSE_FORMAT,
        "max_tokens": 4000,  # с запасом на рассуждения у reasoning-моделей и пачку ответов
    }
    if "gpt-oss" in settings.AI_MODEL:  # задача простая — долго думать не нужно, так быстрее
        body["reasoning_effort"] = "low"
    return body


def _retry_after(response: httpx.Response) -> float:
    try:
        return float(response.headers.get("retry-after", "1"))
    except ValueError:
        return 1.0


async def ask_provider_many(titles: list[str], tree: str, examples: list[str]) -> list[AiAnswer]:
    """Один запрос к OpenAI-совместимому /chat/completions на несколько трат сразу (у
    бесплатных тарифов лимит на токены в минуту, а дерево категорий — основная часть запроса).
    При 429 «подождите секунду» — ждём и пробуем ещё раз. Исключения — наверх."""
    user = f"Категории:\n{tree}\n\n"
    if examples:
        user += "Как пользователь раскладывал раньше:\n" + "\n".join(examples) + "\n\n"
    user += "Траты:\n" + "\n".join(f"{n}. «{t}»" for n, t in enumerate(titles, 1))
    async with httpx.AsyncClient(timeout=settings.AI_TIMEOUT) as client:
        for attempt in range(2):
            response = await client.post(
                settings.AI_BASE_URL.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {settings.AI_API_KEY}"},
                json=_body(user),
            )
            wait = _retry_after(response)
            if response.status_code != 429 or attempt or wait > MAX_RETRY_WAIT:
                break
            await asyncio.sleep(wait)
    if response.is_error:  # текст ошибки провайдера — чтобы было понятно, что не так с запросом
        raise RuntimeError(f"{response.status_code}: {response.text[:500]}")
    return _parse_many(response.json()["choices"][0]["message"]["content"], len(titles))


async def ask_provider(title: str, tree: str, examples: list[str]) -> AiAnswer:
    return (await ask_provider_many([title], tree, examples))[0]


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


async def ai_guess_many(session: AsyncSession, chat: Chat, titles: list[str]) -> dict[str, AiAnswer]:
    """Спросить ИИ (или кэш), куда отнести траты. → {нормализованное название: ответ}; кого
    нет в ответе — ИИ выключен, не ответил, лимит или слишком короткое название."""
    if not enabled(chat):
        return {}
    by_keyword = {}
    for title in titles:
        keyword = normalize(title)[:255]
        if len(keyword) >= MIN_TITLE:
            by_keyword.setdefault(keyword, title.strip()[:255])
    if not by_keyword:
        return {}
    tree = _visible(await cc.tree_for(session, chat))
    text = tree_text(tree)
    tree_hash = hashlib.sha1(text.encode()).hexdigest()[:16]

    result = await session.execute(
        select(AiGuess).where(AiGuess.chat_id == chat.id, AiGuess.keyword.in_(by_keyword))
    )
    cached = {row.keyword: row for row in result.scalars().all()}
    answers: dict[str, AiAnswer] = {}
    pending: list[str] = []
    for keyword in by_keyword:
        row = cached.get(keyword)
        if row is not None and row.action == DECLINED:  # от подкатегории отказались — категория остаётся
            answers[keyword] = resolve(AiAnswer("existing", row.category), tree) if row.category else AiAnswer("unknown")
        elif (
            row is not None
            and row.tree_hash == tree_hash
            and not (row.action == "unknown" and row.created_at < dt.datetime.now(dt.UTC).replace(tzinfo=None) - UNKNOWN_TTL)
        ):
            answers[keyword] = resolve(AiAnswer(row.action, row.category, row.subcategory, row.icon), tree)
        else:
            pending.append(keyword)
    pending = pending[: max(0, settings.AI_DAILY_LIMIT - await _calls_today(session, chat.id))]
    if not pending:
        return answers

    examples = await _examples(session, chat.id)
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    for start in range(0, len(pending), BATCH):
        chunk = pending[start : start + BATCH]
        try:
            replies = await ask_provider_many([by_keyword[k] for k in chunk], text, examples)
        except Exception as exc:  # сеть, таймаут, 4xx/5xx, кривой JSON — без подсказки, но не падаем
            logger.warning("AI category request failed: %s", exc)
            break
        for keyword, reply in zip(chunk, replies):
            answer = resolve(reply, tree)
            row = cached.get(keyword)
            if row is None:
                row = AiGuess(chat_id=chat.id, keyword=keyword)
                session.add(row)
            row.tree_hash = tree_hash
            row.action = answer.action
            row.category = answer.category
            row.subcategory = answer.subcategory
            row.icon = answer.icon
            row.created_at = now
            answers[keyword] = answer
    await session.flush()
    return answers


async def ai_guess(session: AsyncSession, chat: Chat, title: str) -> AiAnswer | None:
    """Спросить ИИ (или кэш), куда отнести трату. None — ИИ выключен, не ответил или лимит."""
    return (await ai_guess_many(session, chat, [title])).get(normalize(title)[:255])


def _to_suggestion(answer: AiAnswer | None) -> Suggestion | None:
    """Если ИИ предлагает новую подкатегорию, категорию подставляем сразу, а подкатегорию —
    только после согласия (Suggestion.proposal); новую категорию — тоже."""
    if answer is None or answer.action == "unknown":
        return None
    if answer.action == "existing":
        return Suggestion(answer.category, answer.subcategory, "ai")
    category = answer.category if answer.action == "new_subcategory" else None
    return Suggestion(category, None, "ai", proposal=answer)


async def suggest_with_ai(session: AsyncSession, chat: Chat, title: str) -> Suggestion | None:
    """Выученное → словарь → ИИ."""
    suggestion = await suggest_category(session, chat.id, title)
    if suggestion is not None:
        return suggestion
    return _to_suggestion(await ai_guess(session, chat, title))


async def ai_unknown(session: AsyncSession, chat_id: int, title: str) -> bool:
    """ИИ уже ответил, что не знает, что это за трата (из кэша)."""
    result = await session.execute(
        select(AiGuess.action).where(AiGuess.chat_id == chat_id, AiGuess.keyword == normalize(title)[:255])
    )
    return result.scalar_one_or_none() == "unknown"


async def cached_proposal(session: AsyncSession, chat_id: int, title: str) -> AiAnswer | None:
    """Предложение ИИ создать категорию для этого названия — из кэша, без запроса к провайдеру."""
    result = await session.execute(
        select(AiGuess).where(AiGuess.chat_id == chat_id, AiGuess.keyword == normalize(title)[:255])
    )
    row = result.scalar_one_or_none()
    if row is None or row.action not in NEW_ACTIONS:
        return None
    return AiAnswer(row.action, row.category, row.subcategory, row.icon)


async def decline(session: AsyncSession, chat_id: int, title: str) -> None:
    """Пользователь отказался создавать предложенное — больше для этого названия не предлагаем."""
    keyword = normalize(title)[:255]
    result = await session.execute(select(AiGuess).where(AiGuess.chat_id == chat_id, AiGuess.keyword == keyword))
    row = result.scalar_one_or_none()
    if row is None:
        row = AiGuess(chat_id=chat_id, keyword=keyword, tree_hash="")
        session.add(row)
    if row.action != "new_subcategory":
        row.category = None
    row.action = DECLINED
    row.subcategory = row.icon = None
    await session.flush()


async def _ensure(session: AsyncSession, chat: Chat, name: str, icon: str | None, parent: str | None) -> str:
    """Категория/подкатегория есть в пространстве и не скрыта; вернуть её точное имя."""
    nodes = await cc.chat_tree(session, chat.id)
    if parent is None:
        names = {n.name: n.hidden for n in nodes}
    else:
        node = next((n for n in nodes if n.name == parent), None)
        names = {s.name: s.hidden for s in node.subs} if node else {}
    existing = _find(names, name)
    if existing is None:
        await cc.create(session, chat, name, icon, parent)
        return cc.clean_name(name)
    if names[existing]:
        await cc.set_hidden(session, chat, existing, parent, False)
    return existing


async def apply_proposal(session: AsyncSession, chat: Chat, answer: AiAnswer) -> tuple[str, str | None]:
    """Создать предложенные ИИ категорию и подкатегорию (с согласия пользователя).
    Уже существующие (или скрытые) не дублируем, а возвращаем. → (категория, подкатегория)."""
    if answer.action not in NEW_ACTIONS or not answer.category:
        raise ValueError("Нечего создавать")
    family_icon = next((n.icon for n in await cc.tree_for(session, chat) if n.name == answer.category), None)
    category = await _ensure(session, chat, answer.category, answer.icon or family_icon, None)
    subcategory = None
    if answer.subcategory:
        subcategory = await _ensure(session, chat, answer.subcategory, None, category)
    return category, subcategory



# ---------------- Разбор старого «Другого» ----------------

TIDY_SECONDS = 15.0  # за один запрос — сколько успеем; остальное по кнопке «Найти ещё»


@dataclass
class TidyItem:
    title: str
    count: int
    total: Decimal
    suggestion: Suggestion


async def _other_expenses(session: AsyncSession, chat_id: int) -> list[Expense]:
    result = await session.execute(
        select(Expense)
        .where(Expense.chat_id == chat_id, Expense.category == OTHER, Expense.subcategory.is_(None))
        .order_by(Expense.expense_date.desc(), Expense.id.desc())
    )
    return list(result.scalars().all())


async def tidy_other(session: AsyncSession, chat: Chat) -> tuple[list[TidyItem], list[str], bool]:
    """Траты в «Другом», сгруппированные по названию, с подсказкой категории (выученное,
    словарь, ИИ пачками). → (что можно разложить, названия, которые не угадали, остались ли
    группы, до которых не дошли)."""
    groups: dict[str, list[Expense]] = {}
    for expense in await _other_expenses(session, chat.id):
        keyword = normalize(expense.title)
        if keyword:
            groups.setdefault(keyword, []).append(expense)
    items: list[TidyItem] = []
    pending: list[list[Expense]] = []
    for rows in sorted(groups.values(), key=len, reverse=True):
        suggestion = await suggest_category(session, chat.id, rows[0].title)
        if suggestion is not None and suggestion.category != OTHER:
            items.append(_tidy_item(rows, suggestion))
        else:
            pending.append(rows)

    started = time.monotonic()
    answers: dict[str, AiAnswer] = {}
    asked = 0
    for start in range(0, len(pending), BATCH):
        if time.monotonic() - started > TIDY_SECONDS:
            break
        answers.update(await ai_guess_many(session, chat, [rows[0].title for rows in pending[start : start + BATCH]]))
        asked = start + BATCH
    unknown: list[str] = []
    for rows in pending[:asked]:
        suggestion = _to_suggestion(answers.get(normalize(rows[0].title)[:255]))
        if suggestion is not None:
            items.append(_tidy_item(rows, suggestion))
        else:
            unknown.append(rows[0].title)
    return items, unknown, asked < len(pending)


def _tidy_item(rows: list[Expense], suggestion: Suggestion) -> TidyItem:
    return TidyItem(rows[0].title, len(rows), sum((e.amount for e in rows), Decimal("0")), suggestion)


async def apply_tidy(
    session: AsyncSession, chat: Chat, title: str, category: str | None, subcategory: str | None, create: bool
) -> int:
    """Переложить траты «Другого» с таким названием в category › subcategory (или, если
    create, — в предложенную ИИ новую категорию) и запомнить. → сколько трат переложили."""
    if create:
        proposal = await cached_proposal(session, chat.id, title)
        if proposal is None:
            raise ValueError(f"Предложение для «{title}» устарело")
        category, subcategory = await apply_proposal(session, chat, proposal)
    tree = _visible(await cc.tree_for(session, chat))
    subs_of = {name: subs for name, _, subs in tree}
    if category not in subs_of or (subcategory and subcategory not in subs_of[category]):
        raise ValueError(f"Нет категории «{category}»")
    keyword = normalize(title)
    moved = 0
    for expense in await _other_expenses(session, chat.id):
        if normalize(expense.title) == keyword:
            expense.category = category
            expense.subcategory = subcategory or None
            moved += 1
    await learn_category(session, chat.id, title, category, subcategory or None)
    await session.flush()
    return moved

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
    for start in range(0, len(titles), BATCH):
        chunk = titles[start : start + BATCH]
        try:
            answers = [resolve(a, tree) for a in await ask_provider_many(chunk, text, [])]
        except Exception as exc:
            print(f"ОШИБКА: {exc}")
            continue
        for title, answer in zip(chunk, answers):
            where = " › ".join(x for x in (answer.category, answer.subcategory) if x)
            print(f"{title:28} {answer.action:16} {answer.icon or ''} {where}")


if __name__ == "__main__":
    # Внутри контейнера: python -m app.shared.ai_category [название ...]
    import asyncio
    import sys

    asyncio.run(_check(sys.argv[1:] or SAMPLE_TITLES))
