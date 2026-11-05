"""Импорт банковской выписки в «Мои финансы»: предпросмотр файла и запись выбранных покупок.

Между шагами на сервере ничего не храним: файл разбирается в памяти и сразу забывается,
а в импорт клиент присылает уже выбранные строки."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.dependencies import ChatContext, get_chat_context
from app.shared.bank_import import MAX_SIZE, ImportError_, parse
from app.shared.bank_import import preview as bank
from app.shared.schemas import (
    BankGroupOut,
    BankImportIn,
    BankImportOut,
    BankPreviewOut,
    BankPurchaseOut,
    BankSkippedOut,
)

router = APIRouter(tags=["bank-import"])


def _personal(ctx: ChatContext) -> None:
    if not ctx.chat.is_personal:
        raise HTTPException(status_code=400, detail="Выписку можно загрузить только в «Мои финансы»")


@router.post("/chats/{chat_id}/bank-import/preview", response_model=BankPreviewOut)
async def preview(file: UploadFile = File(...), ctx: ChatContext = Depends(get_chat_context)):
    _personal(ctx)
    data = await file.read(MAX_SIZE + 1)
    try:
        statement = parse(data, file.filename or "")
    except ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    purchases, skipped = bank.purchases_of(statement)
    await bank.mark_duplicates(ctx.session, ctx.member, purchases)
    groups = await bank.group_with_categories(ctx.session, ctx.chat, purchases)
    await ctx.session.commit()  # кэш ответов ИИ
    return BankPreviewOut(
        bank=statement.bank,
        period_from=statement.period_from,
        period_to=statement.period_to,
        groups=[
            BankGroupOut(
                title=g.title,
                category=g.category,
                subcategory=g.subcategory,
                source=g.source,
                purchases=[
                    BankPurchaseOut(
                        date=p.date,
                        amount=p.amount,
                        description=p.description,
                        duplicate_title=p.duplicate.title if p.duplicate else None,
                        duplicate_date=p.duplicate.expense_date if p.duplicate else None,
                    )
                    for p in g.purchases
                ],
            )
            for g in groups
        ],
        skipped=[BankSkippedOut(kind=s.kind, count=s.count, total=s.total) for s in skipped],
    )


@router.post("/chats/{chat_id}/bank-import", response_model=BankImportOut)
async def import_rows(payload: BankImportIn, ctx: ChatContext = Depends(get_chat_context)):
    _personal(ctx)
    rows = [bank.NewExpense(r.date, r.amount, r.title, r.category, r.subcategory) for r in payload.rows]
    try:
        created = await bank.apply(ctx.session, ctx.chat, ctx.member, rows)
    except ValueError as exc:
        await ctx.session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await ctx.session.commit()
    return BankImportOut(created=created)
