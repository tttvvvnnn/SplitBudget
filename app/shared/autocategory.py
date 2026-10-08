"""Автоопределение категории траты по названию — без внешних сервисов.

1. Выученное в этом чате: каждый раз, когда трату сохраняют, запоминаем
   «нормализованное название → категория › подкатегория» (CategoryRule). Совпадение по
   полному названию важнее совпадения по первому слову («винлаб» ↔ «винлаб на ленина»).
   Поправил категорию — правило перезаписалось, в следующий раз подставится твой вариант.
2. Встроенный словарь магазинов (app/shared/merchants.py): если выученного нет.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.merchants import MERCHANTS
from app.shared.models import CategoryRule

_NON_WORD = re.compile(r"[^0-9a-zа-я]+")


def normalize(title: str) -> str:
    """«Пятёрочка, у дома!» → «пятерочка у дома»."""
    return _NON_WORD.sub(" ", title.lower().replace("ё", "е")).strip()


def _first_word(normalized: str) -> str:
    return normalized.split(" ", 1)[0]


@dataclass
class Suggestion:
    category: str
    subcategory: str | None
    source: str  # 'learned' | 'dictionary'


def suggest_from_dictionary(normalized: str) -> Suggestion | None:
    best: tuple[int, str, str | None] | None = None
    for word in normalized.split():
        for key, category, subcategory in MERCHANTS:
            # Самый длинный подходящий ключ точнее: «метрополит» важнее «метро»
            if word.startswith(key) and (best is None or len(key) > best[0]):
                best = (len(key), category, subcategory)
    if best is None:
        return None
    return Suggestion(best[1], best[2], "dictionary")


async def suggest_category(session: AsyncSession, chat_id: int, title: str) -> Suggestion | None:
    normalized = normalize(title)
    if not normalized:
        return None

    result = await session.execute(select(CategoryRule).where(CategoryRule.chat_id == chat_id))
    rules = result.scalars().all()
    exact = next((r for r in rules if r.keyword == normalized), None)
    if exact is not None:
        return Suggestion(exact.category, exact.subcategory, "learned")

    first = _first_word(normalized)
    if len(first) >= 3:
        same_first = [r for r in rules if _first_word(r.keyword) == first]
        if same_first:
            latest = max(same_first, key=lambda r: r.updated_at)
            return Suggestion(latest.category, latest.subcategory, "learned")

    return suggest_from_dictionary(normalized)


async def learn_category(
    session: AsyncSession, chat_id: int, title: str, category: str, subcategory: str | None
) -> None:
    """Запомнить, что трата с таким названием в этом чате — category › subcategory."""
    normalized = normalize(title)[:255]
    if not normalized:
        return
    result = await session.execute(
        select(CategoryRule).where(CategoryRule.chat_id == chat_id, CategoryRule.keyword == normalized)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        session.add(
            CategoryRule(chat_id=chat_id, keyword=normalized, category=category, subcategory=subcategory)
        )
    else:
        rule.category = category
        rule.subcategory = subcategory
        rule.updated_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
