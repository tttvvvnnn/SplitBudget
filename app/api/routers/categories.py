"""Свои категории и подкатегории пространства: добавить, переименовать, скрыть, удалить."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import ChatContext, get_chat_context
from app.shared import custom_categories as cc
from app.shared.models import CustomCategory
from app.shared.schemas import CategoryHiddenIn, CategoryTreeNodeOut, CustomCategoryIn, SubcategoryNodeOut

router = APIRouter(tags=["categories"])


def nodes_out(nodes: list[cc.Node]) -> list[CategoryTreeNodeOut]:
    return [
        CategoryTreeNodeOut(
            name=n.name,
            icon=n.icon,
            subcategories=n.visible_subs,
            all_subcategories=[SubcategoryNodeOut(name=s.name, custom=s.custom, hidden=s.hidden, id=s.id) for s in n.subs],
            custom=n.custom,
            hidden=n.hidden,
            id=n.id,
            family=n.family,
            family_subcategories=n.family_subs,
        )
        for n in nodes
    ]


async def _tree(ctx: ChatContext) -> list[CategoryTreeNodeOut]:
    return nodes_out(await cc.tree_for(ctx.session, ctx.chat))


async def _row(ctx: ChatContext, category_id: int) -> CustomCategory:
    row = await ctx.session.get(CustomCategory, category_id)
    if row is None or row.chat_id != ctx.chat.id or row.is_builtin:
        raise HTTPException(status_code=404, detail="Категория не найдена")
    return row


@router.get("/chats/{chat_id}/categories", response_model=list[CategoryTreeNodeOut])
async def list_categories(ctx: ChatContext = Depends(get_chat_context)) -> list[CategoryTreeNodeOut]:
    return await _tree(ctx)


@router.post("/chats/{chat_id}/categories", response_model=list[CategoryTreeNodeOut], status_code=201)
async def create_category(payload: CustomCategoryIn, ctx: ChatContext = Depends(get_chat_context)):
    try:
        await cc.create(ctx.session, ctx.chat, payload.name, payload.icon, payload.parent)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await ctx.session.commit()
    return await _tree(ctx)


@router.patch("/chats/{chat_id}/categories/{category_id}", response_model=list[CategoryTreeNodeOut])
async def update_category(category_id: int, payload: CustomCategoryIn, ctx: ChatContext = Depends(get_chat_context)):
    row = await _row(ctx, category_id)
    try:
        await cc.rename(ctx.session, ctx.chat, row, payload.name, payload.icon)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await ctx.session.commit()
    return await _tree(ctx)


@router.delete("/chats/{chat_id}/categories/{category_id}", response_model=list[CategoryTreeNodeOut])
async def delete_category(category_id: int, ctx: ChatContext = Depends(get_chat_context)):
    row = await _row(ctx, category_id)
    try:
        await cc.delete(ctx.session, ctx.chat, row)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await ctx.session.commit()
    return await _tree(ctx)


@router.put("/chats/{chat_id}/categories/hidden", response_model=list[CategoryTreeNodeOut])
async def hide_category(payload: CategoryHiddenIn, ctx: ChatContext = Depends(get_chat_context)):
    try:
        await cc.set_hidden(ctx.session, ctx.chat, payload.name, payload.parent, payload.hidden)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await ctx.session.commit()
    return await _tree(ctx)
