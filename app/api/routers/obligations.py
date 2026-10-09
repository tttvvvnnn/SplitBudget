"""Обязательные платежи месяца и отметка «оплачено / пропустить / вернуть»."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.dependencies import ChatContext, get_chat_context
from app.bot.notify import notify_budget_alerts, notify_payment_marked
from app.shared import obligations as ob
from app.shared.budgets import alerts_after_expense, month_range
from app.shared.models import RecurringExpense, RecurringPayment
from app.shared.schemas import ObligationOut, ObligationsOut, PayIn

router = APIRouter(tags=["obligations"])

ZERO = ob.ZERO


def _check_month(month: str) -> None:
    try:
        month_range(month)
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail="month должен быть в формате YYYY-MM") from exc


@router.get("/chats/{chat_id}/obligations", response_model=ObligationsOut)
async def list_obligations(month: str, ctx: ChatContext = Depends(get_chat_context)) -> ObligationsOut:
    _check_month(month)
    items = await ob.obligations(ctx.session, ctx.chat, month)
    await ctx.session.commit()  # платежи месяца создаются при первом обращении
    counted = [o for o in items if o.counts]
    return ObligationsOut(
        items=[
            ObligationOut(
                payment_id=o.payment.id,
                recurring_id=o.recurring.id,
                chat_id=o.chat.id,
                chat_title=o.chat.title if o.chat.id != ctx.chat.id else None,
                title=o.recurring.title,
                kind=o.recurring.kind,
                category=o.recurring.category,
                subcategory=o.recurring.subcategory,
                due_date=o.payment.due_date,
                status=o.payment.status,
                amount=o.amount,
                share=o.share,
                counts=o.counts,
                debt=o.recurring.debt,
                end_month=o.recurring.end_month,
            )
            for o in items
        ],
        total=sum((o.share for o in counted), ZERO),
        paid=sum((o.share for o in counted if o.payment.status == "paid"), ZERO),
        pending=sum((o.share for o in counted if o.payment.status == "pending"), ZERO),
    )


async def _get_payment(ctx: ChatContext, payment_id: int) -> tuple[RecurringPayment, RecurringExpense]:
    result = await ctx.session.execute(
        select(RecurringPayment, RecurringExpense)
        .join(RecurringExpense, RecurringExpense.id == RecurringPayment.recurring_id)
        .where(RecurringPayment.id == payment_id, RecurringExpense.chat_id == ctx.chat.id)
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Платёж не найден")
    return row[0], row[1]


@router.post("/chats/{chat_id}/payments/{payment_id}/pay", status_code=204)
async def pay_payment(payment_id: int, payload: PayIn, ctx: ChatContext = Depends(get_chat_context)):
    payment, recurring = await _get_payment(ctx, payment_id)
    try:
        expense_id = await ob.pay(ctx.session, payment, payload.amount, ctx.member.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await ctx.session.commit()
    await notify_payment_marked(ctx.chat, recurring.title, payment.amount, ctx.member, paid=True)
    if expense_id:
        alerts = await alerts_after_expense(ctx.session, expense_id)
        if alerts:
            await ctx.session.commit()
            await notify_budget_alerts(alerts)


@router.post("/chats/{chat_id}/payments/{payment_id}/skip", status_code=204)
async def skip_payment(payment_id: int, ctx: ChatContext = Depends(get_chat_context)):
    payment, _ = await _get_payment(ctx, payment_id)
    try:
        await ob.skip(ctx.session, payment)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await ctx.session.commit()


@router.post("/chats/{chat_id}/payments/{payment_id}/reset", status_code=204)
async def reset_payment(payment_id: int, ctx: ChatContext = Depends(get_chat_context)):
    payment, _ = await _get_payment(ctx, payment_id)
    await ob.reset(ctx.session, payment)
    await ctx.session.commit()
