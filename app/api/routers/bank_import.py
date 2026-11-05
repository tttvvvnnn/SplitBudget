"""Импорт банковской выписки в «Мои финансы»: предпросмотр файла и запись выбранного.

Между шагами на сервере ничего не храним: файл разбирается в памяти и сразу забывается,
а в импорт клиент присылает уже выбранные строки и решения по совпадениям."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.dependencies import ChatContext, get_chat_context
from app.shared.bank_import import MAX_SIZE, ImportError_, parse
from app.shared.bank_import import preview as bank
from app.shared.schemas import (
    BankGroupOut,
    BankImportIn,
    BankImportOut,
    BankIncomeOut,
    BankMatchOut,
    BankPreviewOut,
    BankPurchaseOut,
    BankSkippedOut,
)

router = APIRouter(tags=["bank-import"])


def _personal(ctx: ChatContext) -> None:
    if not ctx.chat.is_personal:
        raise HTTPException(status_code=400, detail="Выписку можно загрузить только в «Мои финансы»")


def _match_out(match: bank.Match | None) -> BankMatchOut | None:
    if match is None:
        return None
    if match.expense is not None:
        e = match.expense
        return BankMatchOut(level=match.level, default=bank.DEFAULT_CHOICE[match.level], id=e.id, title=e.title,
                            amount=e.amount, date=e.expense_date, category=e.category, space=match.space,
                            editable=match.editable)
    i = match.income
    return BankMatchOut(level=match.level, default=bank.DEFAULT_CHOICE[match.level], id=i.id, title=i.title,
                        amount=i.amount, date=i.income_date, editable=True)


@router.post("/chats/{chat_id}/bank-import/preview", response_model=BankPreviewOut)
async def preview(file: UploadFile = File(...), ctx: ChatContext = Depends(get_chat_context)):
    _personal(ctx)
    data = await file.read(MAX_SIZE + 1)
    try:
        statement = parse(data, file.filename or "")
    except ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = await bank.build(ctx.session, ctx.chat, ctx.member, statement)
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
                    BankPurchaseOut(date=p.date, amount=p.amount, description=p.description, match=_match_out(p.match))
                    for p in g.purchases
                ],
            )
            for g in result.groups
        ],
        incomes=[
            BankIncomeOut(date=i.date, amount=i.amount, title=i.title, description=i.description,
                          checked=i.checked, source_id=i.source.id if i.source else None, match=_match_out(i.match))
            for i in result.incomes
        ],
        skipped=[BankSkippedOut(kind=s.kind, count=s.count, total=s.total) for s in result.skipped],
    )


@router.post("/chats/{chat_id}/bank-import", response_model=BankImportOut)
async def import_rows(payload: BankImportIn, ctx: ChatContext = Depends(get_chat_context)):
    _personal(ctx)
    try:
        result = await bank.apply(
            ctx.session,
            ctx.chat,
            ctx.member,
            rows=[bank.NewExpense(r.date, r.amount, r.title, r.category, r.subcategory) for r in payload.rows],
            updates=[bank.ExpenseUpdate(u.expense_id, u.date, u.amount, u.title, u.category, u.subcategory)
                     for u in payload.updates],
            incomes=[bank.NewIncome(i.date, i.amount, i.title, i.source_id) for i in payload.incomes],
            income_updates=[bank.IncomeUpdate(u.income_id, u.date, u.amount) for u in payload.income_updates],
        )
    except ValueError as exc:
        await ctx.session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await ctx.session.commit()
    return BankImportOut(created=result.expenses, updated=result.updated, incomes=result.incomes,
                         payments=result.payments)
