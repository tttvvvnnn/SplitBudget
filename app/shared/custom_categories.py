"""Дерево категорий пространства: базовые (app/shared/categories.py) плюс свои.

У каждого пространства свой набор: свои категории «Моих финансов» видит только владелец,
свои категории семейного чата — участники этого чата. Базовые категории удалить нельзя,
только скрыть (строка CustomCategory с is_builtin=True). Траты хранят категорию строкой,
поэтому при переименовании своей категории переименовываются и траты с ней.

В «Моих финансах» к дереву добавляются свои категории семейных чатов владельца (family):
его доля семейных трат попадает в эти категории, и их можно положить в статью плана.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.categories import CATEGORY_TREE, OTHER
from app.shared.models import (
    Budget,
    CategoryRule,
    Chat,
    CustomCategory,
    Expense,
    Member,
    PlanItem,
    RecurringExpense,
)

SUB_SEP = " › "  # как в app/shared/plan.py
DEFAULT_ICON = "🏷️"


@dataclass
class SubNode:
    name: str
    custom: bool = False
    hidden: bool = False
    id: int | None = None


@dataclass
class Node:
    name: str
    icon: str
    custom: bool = False
    hidden: bool = False
    id: int | None = None
    subs: list[SubNode] = field(default_factory=list)
    family: str | None = None  # название семейного чата, если категория оттуда
    family_subs: list[str] = field(default_factory=list)  # свои подкатегории семейных чатов

    @property
    def visible_subs(self) -> list[str]:
        return [s.name for s in self.subs if not s.hidden]


async def rows_of(session: AsyncSession, chat_id: int) -> list[CustomCategory]:
    result = await session.execute(
        select(CustomCategory).where(CustomCategory.chat_id == chat_id).order_by(CustomCategory.id)
    )
    return list(result.scalars().all())


def _build(rows: list[CustomCategory]) -> list[Node]:
    hidden = {(r.parent, r.name): r for r in rows if r.is_builtin}
    nodes: list[Node] = []
    for name, icon, subs in CATEGORY_TREE:
        row = hidden.get((None, name))
        node = Node(name, icon, hidden=bool(row and row.is_hidden), id=row.id if row else None)
        for sub in subs:
            sub_row = hidden.get((name, sub))
            node.subs.append(SubNode(sub, hidden=bool(sub_row and sub_row.is_hidden), id=sub_row.id if sub_row else None))
        nodes.append(node)
    builtin = {n.name for n in nodes}
    for r in rows:
        # Своя категория, которая потом появилась среди базовых (например, «Покупки»), — не дубль:
        # её подкатегории просто попадают в базовую
        if not r.is_builtin and r.parent is None and r.name not in builtin:
            nodes.append(Node(r.name, r.icon or DEFAULT_ICON, custom=True, hidden=r.is_hidden, id=r.id))
    by_name = {n.name: n for n in nodes}
    for r in rows:
        if not r.is_builtin and r.parent is not None and r.parent in by_name:
            node = by_name[r.parent]
            if all(sub.name != r.name for sub in node.subs):
                node.subs.append(SubNode(r.name, custom=True, hidden=r.is_hidden, id=r.id))
    # «Другое» — всегда последняя
    nodes.sort(key=lambda n: n.name == OTHER)
    return nodes


async def chat_tree(session: AsyncSession, chat_id: int) -> list[Node]:
    return _build(await rows_of(session, chat_id))


async def family_chats(session: AsyncSession, tg_user_id: int) -> list[Chat]:
    result = await session.execute(
        select(Chat)
        .join(Member, Member.chat_id == Chat.id)
        .where(Member.tg_user_id == tg_user_id, Member.is_active.is_(True), Chat.is_personal.is_(False))
        .order_by(Chat.id)
    )
    return list(result.scalars().all())


async def tree_for(session: AsyncSession, chat: Chat) -> list[Node]:
    """Дерево пространства; в «Моих финансах» — вместе со своими категориями семейных чатов."""
    nodes = await chat_tree(session, chat.id)
    if not chat.is_personal:
        return nodes
    by_name = {n.name: n for n in nodes}
    for family in await family_chats(session, chat.id):
        for fnode in await chat_tree(session, family.id):
            if fnode.hidden:
                continue
            own = by_name.get(fnode.name)
            if own is None:
                node = Node(fnode.name, fnode.icon, custom=True, family=family.title or str(family.id))
                node.family_subs = fnode.visible_subs
                nodes.insert(len(nodes) - 1, node)
                by_name[node.name] = node
            else:
                known = {s.name for s in own.subs} | set(own.family_subs)
                own.family_subs += [s.name for s in fnode.subs if s.custom and not s.hidden and s.name not in known]
    return nodes


def plan_keys(nodes: list[Node]) -> set[str]:
    """Что можно отметить в статье плана: категории и «Категория › Подкатегория»."""
    keys = set()
    for n in nodes:
        keys.add(n.name)
        keys.update(n.name + SUB_SEP + s.name for s in n.subs)
        keys.update(n.name + SUB_SEP + s for s in n.family_subs)
    return keys


def clean_name(name: str) -> str:
    name = " ".join(name.split())[:64]
    if not name:
        raise ValueError("Укажите название")
    if "›" in name:
        raise ValueError("Символ «›» в названии нельзя")
    return name


def _taken(nodes: list[Node], parent: str | None, name: str, skip_id: int | None = None) -> bool:
    low = name.lower()
    if parent is None:
        return any(n.name.lower() == low and not (n.custom and n.id == skip_id) for n in nodes if n.family is None)
    node = next((n for n in nodes if n.name == parent), None)
    return node is not None and any(s.name.lower() == low and not (s.custom and s.id == skip_id) for s in node.subs)


async def create(session: AsyncSession, chat: Chat, name: str, icon: str | None, parent: str | None) -> CustomCategory:
    name = clean_name(name)
    nodes = await chat_tree(session, chat.id)
    if parent is not None and not any(n.name == parent for n in nodes):
        raise ValueError("Нет такой категории")
    if _taken(nodes, parent, name):
        raise ValueError(f"«{name}» уже есть")
    row = CustomCategory(chat_id=chat.id, parent=parent, name=name, icon=(icon or DEFAULT_ICON)[:16])
    session.add(row)
    await session.flush()
    return row


async def _plan_chat_ids(session: AsyncSession, chat: Chat) -> list[int]:
    """Чьи планы могут ссылаться на категории пространства: владельца или участников семьи."""
    if chat.is_personal:
        return [chat.id]
    result = await session.execute(
        select(Member.tg_user_id).where(Member.chat_id == chat.id, Member.tg_user_id.is_not(None))
    )
    return [int(i) for i in result.scalars().all()]


async def _rename_plan_keys(session: AsyncSession, chat_ids: list[int], old: str, new: str, parent: str | None) -> None:
    if not chat_ids:
        return
    items = (
        await session.execute(select(PlanItem).where(PlanItem.chat_id.in_(chat_ids), PlanItem.kind == "spend"))
    ).scalars().all()
    for item in items:
        keys = json.loads(item.categories or "[]")
        changed = []
        for key in keys:
            cat, _, sub = key.partition(SUB_SEP)
            if parent is None and cat == old:
                key = new + (SUB_SEP + sub if sub else "")
            elif parent is not None and cat == parent and sub == old:
                key = parent + SUB_SEP + new
            changed.append(key)
        if changed != keys:
            item.categories = json.dumps(changed, ensure_ascii=False)


async def rename(session: AsyncSession, chat: Chat, row: CustomCategory, name: str, icon: str | None) -> None:
    """Переименовать свою категорию вместе с тратами, платежами, лимитами, правилами и планом."""
    name = clean_name(name)
    if icon:
        row.icon = icon[:16]
    if name == row.name:
        return
    nodes = await chat_tree(session, chat.id)
    if _taken(nodes, row.parent, name, skip_id=row.id):
        raise ValueError(f"«{name}» уже есть")
    old = row.name
    if row.parent is None:
        for model in (Expense, RecurringExpense, Budget, CategoryRule):
            await session.execute(
                update(model).where(model.chat_id == chat.id, model.category == old).values(category=name)
            )
        await session.execute(
            update(CustomCategory)
            .where(CustomCategory.chat_id == chat.id, CustomCategory.parent == old)
            .values(parent=name)
        )
    else:
        for model in (Expense, RecurringExpense, Budget, CategoryRule):
            await session.execute(
                update(model)
                .where(model.chat_id == chat.id, model.category == row.parent, model.subcategory == old)
                .values(subcategory=name)
            )
    await _rename_plan_keys(session, await _plan_chat_ids(session, chat), old, name, row.parent)
    row.name = name


async def delete(session: AsyncSession, chat: Chat, row: CustomCategory) -> None:
    """Удалить свою категорию можно, только если в ней нет трат; иначе — скрыть."""
    query = select(func.count()).select_from(Expense).where(Expense.chat_id == chat.id)
    if row.parent is None:
        query = query.where(Expense.category == row.name)
    else:
        query = query.where(Expense.category == row.parent, Expense.subcategory == row.name)
    if (await session.execute(query)).scalar_one():
        raise ValueError("В этой категории есть траты — её можно только скрыть")
    if row.parent is None:
        children = (
            await session.execute(
                select(CustomCategory).where(CustomCategory.chat_id == chat.id, CustomCategory.parent == row.name)
            )
        ).scalars().all()
        for child in children:
            await session.delete(child)
    await session.delete(row)


async def set_hidden(session: AsyncSession, chat: Chat, name: str, parent: str | None, hidden: bool) -> None:
    """Скрыть или вернуть категорию/подкатегорию (базовую или свою)."""
    if parent is None and name == OTHER:
        raise ValueError(f"«{OTHER}» скрыть нельзя")
    rows = await rows_of(session, chat.id)
    row = next((r for r in rows if r.parent == parent and r.name == name), None)
    if row is None:
        builtin = {(None, n) for n, _, _ in CATEGORY_TREE} | {(n, s) for n, _, subs in CATEGORY_TREE for s in subs}
        if (parent, name) not in builtin:
            raise ValueError("Нет такой категории")
        row = CustomCategory(chat_id=chat.id, parent=parent, name=name, icon=DEFAULT_ICON, is_builtin=True)
        session.add(row)
    row.is_hidden = hidden
    await session.flush()
