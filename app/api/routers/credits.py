"""Вкладка «Кредиты» «Моих финансов»: кредитные карты с операциями и кредиты."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import ChatContext, get_chat_context
from app.shared import credits
from app.shared.models import CreditCard, CreditOperation
from app.shared.schemas import (
    CreditAdjustIn,
    CreditAmountIn,
    CreditCardIn,
    CreditCardOut,
    CreditExtendIn,
    CreditOperationOut,
    CreditPurchaseOut,
    CreditsOut,
    LoanOut,
)

router = APIRouter(tags=["credits"])

RECENT_OPERATIONS = 20
ZERO = credits.ZERO


def _personal(ctx: ChatContext) -> None:
    if not ctx.chat.is_personal:
        raise HTTPException(status_code=400, detail="Кредиты ведутся только в «Моих финансах»")


async def _card_out(ctx: ChatContext, card: CreditCard, today: dt.date) -> CreditCardOut:
    operations = await credits.operations_of(ctx.session, card.id, RECENT_OPERATIONS)
    purchases = await credits.recent_purchases(ctx.session, card.id, RECENT_OPERATIONS)
    debt = await credits.debt_of(ctx.session, card)
    return CreditCardOut(
        id=card.id,
        title=card.title,
        credit_limit=card.credit_limit,
        debt=debt,
        statement_day=card.statement_day,
        grace_until=card.grace_until,
        min_payment=card.min_payment,
        payment_date=card.payment_date,
        next_deadline=credits.next_deadline(card, debt, today),
        category=card.category,
        subcategory=card.subcategory,
        is_archived=card.is_archived,
        operations=[CreditOperationOut.model_validate(o) for o in operations],
        purchases=[
            CreditPurchaseOut.model_validate(e).model_copy(update={"is_payment": e.repays_card_id == card.id})
            for e in purchases
        ],
    )


async def _card(ctx: ChatContext, card_id: int) -> CreditCard:
    _personal(ctx)
    card = await ctx.session.get(CreditCard, card_id)
    if card is None or card.chat_id != ctx.chat.id:
        raise HTTPException(status_code=404, detail="Карта не найдена")
    return card


@router.get("/chats/{chat_id}/credits", response_model=CreditsOut)
async def list_credits(ctx: ChatContext = Depends(get_chat_context)) -> CreditsOut:
    _personal(ctx)
    today = dt.date.today()
    cards = [await _card_out(ctx, c, today) for c in await credits.cards_of(ctx.session, ctx.chat.id)]
    loans = await credits.loans_of(ctx.session, ctx.chat, today)
    await ctx.session.commit()  # платежи месяца создаются при первом обращении
    loans_out = [
        LoanOut(
            recurring_id=s.recurring.id,
            title=s.recurring.title,
            amount=s.recurring.amount,
            debt=s.recurring.debt,
            day_of_month=s.recurring.day_of_month,
            end_month=s.recurring.end_month,
            is_active=s.recurring.is_active,
            payment_id=s.payment.id if s.payment else None,
            payment_status=s.payment.status if s.payment else None,
            payment_amount=(s.payment.amount if s.payment.amount is not None else s.recurring.amount)
            if s.payment else None,
            payment_due=s.payment.due_date if s.payment else None,
            next_due=s.next_due,
        )
        for s in loans
    ]
    active_cards = [c for c in cards if not c.is_archived]
    deadlines = [c.next_deadline for c in active_cards if c.next_deadline]
    deadlines += [loan.next_due for loan in loans_out if loan.is_active and loan.next_due]
    return CreditsOut(
        cards=cards,
        loans=loans_out,
        total_debt=sum((c.debt for c in active_cards), ZERO)
        + sum((loan.debt or ZERO for loan in loans_out if loan.is_active), ZERO),
        next_deadline=min(deadlines) if deadlines else None,
    )


def _apply(card: CreditCard, payload: CreditCardIn) -> None:
    card.title = payload.title.strip()[:255]
    card.credit_limit = payload.credit_limit
    card.statement_day = payload.statement_day
    card.grace_until = payload.grace_until
    card.payment_date = payload.payment_date
    card.min_payment = payload.min_payment if payload.payment_date else None
    card.category = payload.category or "Кредиты"
    card.subcategory = payload.subcategory or None
    card.is_archived = payload.is_archived


@router.post("/chats/{chat_id}/credit-cards", response_model=CreditCardOut, status_code=201)
async def create_card(payload: CreditCardIn, ctx: ChatContext = Depends(get_chat_context)) -> CreditCardOut:
    _personal(ctx)
    card = CreditCard(chat_id=ctx.chat.id, debt=payload.debt, reminders_sent="")
    _apply(card, payload)
    ctx.session.add(card)
    await ctx.session.commit()
    return await _card_out(ctx, card, dt.date.today())


@router.patch("/chats/{chat_id}/credit-cards/{card_id}", response_model=CreditCardOut)
async def update_card(card_id: int, payload: CreditCardIn, ctx: ChatContext = Depends(get_chat_context)) -> CreditCardOut:
    """Название, лимит, даты, категория. Долг здесь не меняется — для этого «Сверить с банком»."""
    card = await _card(ctx, card_id)
    if payload.grace_until != card.grace_until or payload.payment_date != card.payment_date:
        card.reminders_sent = ",".join(k for k in card.reminders_sent.split(",") if k.startswith("stmt:"))
    _apply(card, payload)
    await ctx.session.commit()
    return await _card_out(ctx, card, dt.date.today())


@router.delete("/chats/{chat_id}/credit-cards/{card_id}", status_code=204)
async def delete_card(card_id: int, ctx: ChatContext = Depends(get_chat_context)):
    card = await _card(ctx, card_id)
    await credits.delete_card(ctx.session, card)
    await ctx.session.commit()


async def _run(ctx: ChatContext, card: CreditCard, action) -> CreditCardOut:
    try:
        await action
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await ctx.session.commit()
    return await _card_out(ctx, card, dt.date.today())


@router.post("/chats/{chat_id}/credit-cards/{card_id}/pay", response_model=CreditCardOut)
async def pay_card(card_id: int, payload: CreditAmountIn, ctx: ChatContext = Depends(get_chat_context)) -> CreditCardOut:
    card = await _card(ctx, card_id)
    return await _run(ctx, card, credits.pay(ctx.session, card, payload.amount, payload.op_date))


@router.post("/chats/{chat_id}/credit-cards/{card_id}/extend", response_model=CreditCardOut)
async def extend_card(card_id: int, payload: CreditExtendIn, ctx: ChatContext = Depends(get_chat_context)) -> CreditCardOut:
    card = await _card(ctx, card_id)
    return await _run(
        ctx, card, credits.extend(ctx.session, card, payload.grace_until, payload.payment_date, payload.min_payment)
    )


@router.post("/chats/{chat_id}/credit-cards/{card_id}/interest", response_model=CreditCardOut)
async def card_interest(card_id: int, payload: CreditAmountIn, ctx: ChatContext = Depends(get_chat_context)) -> CreditCardOut:
    card = await _card(ctx, card_id)
    return await _run(ctx, card, credits.interest(ctx.session, card, ctx.member, payload.amount))


@router.post("/chats/{chat_id}/credit-cards/{card_id}/adjust", response_model=CreditCardOut)
async def adjust_card(card_id: int, payload: CreditAdjustIn, ctx: ChatContext = Depends(get_chat_context)) -> CreditCardOut:
    card = await _card(ctx, card_id)
    return await _run(ctx, card, credits.adjust(ctx.session, card, payload.debt))


@router.delete("/chats/{chat_id}/credit-cards/{card_id}/operations/{operation_id}", response_model=CreditCardOut)
async def undo_operation(card_id: int, operation_id: int, ctx: ChatContext = Depends(get_chat_context)) -> CreditCardOut:
    card = await _card(ctx, card_id)
    operation = await ctx.session.get(CreditOperation, operation_id)
    if operation is None or operation.card_id != card.id:
        raise HTTPException(status_code=404, detail="Операция не найдена")
    return await _run(ctx, card, credits.undo(ctx.session, card, operation))
