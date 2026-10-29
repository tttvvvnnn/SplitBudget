"""CRUD трат: создание (с фото), список, редактирование, удаление, отдача фото."""
from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import ChatContext, get_chat_context
from app.api.routers.categories import nodes_out
from app.bot.notify import notify_budget_alerts, notify_expense_deleted, notify_new_expense
from app.shared.ai_category import (
    ai_unknown,
    apply_proposal,
    apply_tidy,
    cached_proposal,
    decline,
    suggest_with_ai,
    tidy_other,
)
from app.shared.autocategory import learn_category, suggest_category
from app.shared.budgets import alerts_after_expense
from app.shared.config import settings
from app.shared.crud import build_custom_shares, build_equal_shares
from app.shared.credits import own_card
from app.shared.custom_categories import tree_for
from app.shared.models import CreditCard, Expense, ExpenseShare, Member
from app.shared.schemas import (
    AiProposalAcceptedOut,
    AiProposalOut,
    CategorySuggestionOut,
    ExpenseOut,
    ShareOut,
    TidyApplyIn,
    TidyApplyOut,
    TidyItemOut,
    TidyOut,
)

router = APIRouter(tags=["expenses"])

ALLOWED_PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_PHOTO_BYTES = 8 * 1024 * 1024


async def _validate_members(session: AsyncSession, chat_id: int, member_ids: set[int]) -> dict[int, Member]:
    if not member_ids:
        return {}
    result = await session.execute(
        select(Member).where(Member.chat_id == chat_id, Member.id.in_(member_ids))
    )
    found = {m.id: m for m in result.scalars().all()}
    missing = member_ids - found.keys()
    if missing:
        raise HTTPException(status_code=400, detail=f"Неизвестные участники: {sorted(missing)}")
    return found


def _parse_json_field(raw: str | None, default):
    if raw is None or raw == "":
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Некорректный JSON в форме") from exc


async def _save_photo(chat_id: int, photo: UploadFile) -> str:
    if photo.content_type not in ALLOWED_PHOTO_TYPES:
        raise HTTPException(status_code=400, detail="Поддерживаются только фото JPEG/PNG/WEBP")
    data = await photo.read()
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=400, detail="Фото слишком большое (максимум 8 МБ)")
    ext = ALLOWED_PHOTO_TYPES[photo.content_type]
    filename = f"{chat_id}_{uuid.uuid4().hex}{ext}"
    path = settings.photos_dir / filename
    path.write_bytes(data)
    return filename


async def _card_for(ctx: ChatContext, raw: str, payer_member_id: int) -> int | None:
    """card_id из формы: "" — оплачено своими деньгами. Картой можно оплатить только свою
    трату и только своей картой (из «Моих финансов»)."""
    if not raw:
        return None
    if payer_member_id != ctx.member.id:
        raise HTTPException(status_code=400, detail="Картой можно отметить только трату, которую оплатили вы")
    try:
        return (await own_card(ctx.session, int(raw), ctx.member.tg_user_id)).id
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _to_out(expense: Expense, shares: list[ExpenseShare], viewer: Member) -> ExpenseOut:
    # Важно: не присваиваем shares в expense.shares (лениво загружаемая relationship) —
    # на AsyncSession это провоцирует неявный синхронный lazy-load вне greenlet-контекста
    # (MissingGreenlet). Передаём доли отдельным параметром и строим ExpenseOut напрямую.
    return ExpenseOut(
        id=expense.id,
        title=expense.title,
        amount=expense.amount,
        category=expense.category,
        subcategory=expense.subcategory,
        photo_url=(f"photos/{expense.photo_path}" if expense.photo_path else None),
        expense_date=expense.expense_date,
        payer_member_id=expense.payer_member_id,
        split_type=expense.split_type,
        created_by_member_id=expense.created_by_member_id,
        shares=[ShareOut(member_id=s.member_id, amount=s.amount) for s in shares],
        created_at=expense.created_at,
        is_recurring=expense.recurring_id is not None,
        # Чем оплатил плательщик — его личное дело: остальным карта не показывается
        card_id=expense.card_id if expense.payer_member_id == viewer.id else None,
        repays_card_id=expense.repays_card_id if expense.payer_member_id == viewer.id else None,
    )


@router.get("/chats/{chat_id}/expenses", response_model=list[ExpenseOut])
async def list_expenses(
    ctx: ChatContext = Depends(get_chat_context),
    month: str | None = None,  # 'YYYY-MM'
    category: str | None = None,
    limit: int = 200,
    date_from: dt.date | None = None,  # или с … по … включительно (статистика за период)
    date_to: dt.date | None = None,
):
    query = select(Expense).where(Expense.chat_id == ctx.chat.id)
    if ctx.chat.is_personal:
        query = query.where(Expense.card_id.is_(None))  # покупки кредиткой — в карте (вкладка «Кредиты»)
    if month:
        try:
            year, mon = (int(p) for p in month.split("-"))
            start = dt.date(year, mon, 1)
            end = dt.date(year + (mon // 12), (mon % 12) + 1, 1)
        except (ValueError, IndexError) as exc:
            raise HTTPException(status_code=400, detail="month должен быть в формате YYYY-MM") from exc
        query = query.where(Expense.expense_date >= start, Expense.expense_date < end)
    if date_from:
        query = query.where(Expense.expense_date >= date_from)
    if date_to:
        query = query.where(Expense.expense_date <= date_to)
    if category:
        query = query.where(Expense.category == category)
    query = query.order_by(Expense.expense_date.desc(), Expense.id.desc()).limit(min(limit, 1000))

    result = await ctx.session.execute(query)
    expenses = result.scalars().unique().all()

    # подгружаем доли отдельным запросом, чтобы не городить сложный eager-load
    ids = [e.id for e in expenses]
    shares_by_expense: dict[int, list[ExpenseShare]] = {}
    if ids:
        shares_result = await ctx.session.execute(
            select(ExpenseShare).where(ExpenseShare.expense_id.in_(ids))
        )
        for share in shares_result.scalars().all():
            shares_by_expense.setdefault(share.expense_id, []).append(share)

    return [_to_out(e, shares_by_expense.get(e.id, []), ctx.member) for e in expenses]


@router.post("/chats/{chat_id}/expenses", response_model=ExpenseOut, status_code=201)
async def create_expense(
    ctx: ChatContext = Depends(get_chat_context),
    title: str = Form(...),
    amount: str = Form(...),
    category: str = Form("Другое"),
    subcategory: str | None = Form(None),
    expense_date: str = Form(...),
    payer_member_id: int = Form(...),
    split_type: str = Form("equal"),
    participant_ids: str | None = Form(None),  # JSON-массив id, для split_type='equal'
    custom_shares: str | None = Form(None),  # JSON-массив {member_id, amount}, для 'custom'
    card_id: str | None = Form(None),  # оплачено своей кредитной картой
    repays_card_id: str | None = Form(None),  # платёж по своей кредитной карте
    photo: UploadFile | None = File(None),
):
    try:
        amount_dec = Decimal(amount)
    except InvalidOperation as exc:
        raise HTTPException(status_code=400, detail="Некорректная сумма") from exc
    if amount_dec <= 0:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше нуля")

    try:
        expense_date_val = dt.date.fromisoformat(expense_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Дата должна быть в формате YYYY-MM-DD") from exc

    if split_type not in ("equal", "custom"):
        raise HTTPException(status_code=400, detail="split_type должен быть 'equal' или 'custom'")

    participants = _parse_json_field(participant_ids, [])
    custom = _parse_json_field(custom_shares, [])

    if split_type == "equal":
        ids = [int(i) for i in participants]
        await _validate_members(ctx.session, ctx.chat.id, set(ids) | {payer_member_id})
        shares = build_equal_shares(amount_dec, ids)
    else:
        pairs = [(int(item["member_id"]), Decimal(str(item["amount"]))) for item in custom]
        member_ids = {mid for mid, _ in pairs} | {payer_member_id}
        await _validate_members(ctx.session, ctx.chat.id, member_ids)
        try:
            shares = build_custom_shares(amount_dec, pairs)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    card = await _card_for(ctx, card_id or "", payer_member_id)
    repays = await _card_for(ctx, repays_card_id or "", payer_member_id)

    photo_filename = None
    if photo is not None and photo.filename:
        photo_filename = await _save_photo(ctx.chat.id, photo)

    expense = Expense(
        chat_id=ctx.chat.id,
        title=title.strip()[:255],
        amount=amount_dec,
        category=category or "Другое",
        subcategory=(subcategory or None),
        photo_path=photo_filename,
        expense_date=expense_date_val,
        payer_member_id=payer_member_id,
        split_type=split_type,
        created_by_member_id=ctx.member.id,
        card_id=card,
        repays_card_id=repays,
    )
    ctx.session.add(expense)
    await ctx.session.flush()

    for member_id, share_amount in shares:
        ctx.session.add(ExpenseShare(expense_id=expense.id, member_id=member_id, amount=share_amount))

    await learn_category(ctx.session, ctx.chat.id, expense.title, expense.category, expense.subcategory)
    await ctx.session.commit()

    members_result = await ctx.session.execute(
        select(Member).where(Member.chat_id == ctx.chat.id, Member.id.in_([m for m, _ in shares]))
    )
    members_by_id = {m.id: m for m in members_result.scalars().all()}
    payer = members_by_id.get(payer_member_id) or await ctx.session.get(Member, payer_member_id)
    labels = [
        (f"@{m.username}" if m.username else m.full_name) for m in members_by_id.values()
    ]
    category_label = expense.category + (f" › {expense.subcategory}" if expense.subcategory else "")
    await notify_new_expense(
        ctx.chat, payer, ctx.member, expense.title, expense.amount, category_label, labels
    )
    await _check_budgets(ctx, expense.id)

    result_shares = [ExpenseShare(expense_id=expense.id, member_id=m, amount=a) for m, a in shares]
    return _to_out(expense, result_shares, ctx.member)


async def _check_budgets(ctx: ChatContext, expense_id: int) -> None:
    """Проверить лимиты после сохранения траты и разослать новые уведомления (80% / 100%)."""
    alerts = await alerts_after_expense(ctx.session, expense_id)
    if alerts:
        await ctx.session.commit()  # отметки BudgetAlert — чтобы не повторяться
        await notify_budget_alerts(alerts)


def _proposal_out(proposal) -> AiProposalOut | None:
    if proposal is None:
        return None
    return AiProposalOut(
        action=proposal.action, category=proposal.category, subcategory=proposal.subcategory, icon=proposal.icon
    )


@router.get("/chats/{chat_id}/category-suggestion", response_model=CategorySuggestionOut | None)
async def category_suggestion(title: str, ai: bool = False, ctx: ChatContext = Depends(get_chat_context)):
    """Подсказка категории по названию траты — мини-апп подставляет её в форму, пока
    пользователь печатает название. null — угадать не получилось. ai=1 — если выученное и
    словарь молчат, спросить ИИ (мини-апп делает это реже, после паузы в наборе). ИИ может
    предложить новую категорию/подкатегорию (proposal) — мини-апп спрашивает согласия и
    создаёт её обычным POST /categories."""
    if ai:
        suggestion = await suggest_with_ai(ctx.session, ctx.chat, title)
        await ctx.session.commit()  # кэш ответа ИИ
        if suggestion is None and await ai_unknown(ctx.session, ctx.chat.id, title):
            # ИИ спросили, но он не узнал название — мини-апп так и пишет, а не молчит
            return CategorySuggestionOut(category=None, subcategory=None, source="ai_unknown")
    else:
        suggestion = await suggest_category(ctx.session, ctx.chat.id, title)
    if suggestion is None:
        return None
    proposal = suggestion.proposal
    return CategorySuggestionOut(
        category=suggestion.category,
        subcategory=suggestion.subcategory,
        source=suggestion.source,
        proposal=_proposal_out(proposal),
    )


@router.post("/chats/{chat_id}/category-suggestion/accept", response_model=AiProposalAcceptedOut)
async def accept_category_proposal(title: str, ctx: ChatContext = Depends(get_chat_context)):
    """«Создать» на предложение ИИ: создаём категорию/подкатегорию (уже существующие или
    скрытые не дублируем) и возвращаем, что выбрать в форме, и обновлённое дерево."""
    proposal = await cached_proposal(ctx.session, ctx.chat.id, title)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Предложение устарело — выберите категорию сами")
    try:
        category, subcategory = await apply_proposal(ctx.session, ctx.chat, proposal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await ctx.session.commit()
    return AiProposalAcceptedOut(
        category=category, subcategory=subcategory, categories=nodes_out(await tree_for(ctx.session, ctx.chat))
    )


@router.get("/chats/{chat_id}/tidy-other", response_model=TidyOut)
async def tidy_other_list(ctx: ChatContext = Depends(get_chat_context)):
    """Разобрать старое «Другое»: группы трат по названию и куда их, похоже, переложить.
    ИИ спрашиваем не дольше ~20 с за раз; more=true — можно запросить ещё."""
    if not ctx.chat.ai_enabled:
        raise HTTPException(status_code=400, detail="Подсказки ИИ выключены в этом пространстве")
    items, unknown, more = await tidy_other(ctx.session, ctx.chat)
    await ctx.session.commit()  # кэш ответов ИИ
    return TidyOut(
        items=[
            TidyItemOut(
                title=i.title,
                count=i.count,
                total=i.total,
                category=i.suggestion.category,
                subcategory=i.suggestion.subcategory,
                source=i.suggestion.source,
                proposal=_proposal_out(i.suggestion.proposal),
            )
            for i in items
        ],
        unknown=unknown,
        more=more,
    )


@router.post("/chats/{chat_id}/tidy-other", response_model=TidyApplyOut)
async def tidy_other_apply(payload: TidyApplyIn, ctx: ChatContext = Depends(get_chat_context)):
    """Переложить отмеченные группы из «Другого» (и запомнить на будущее)."""
    moved = 0
    try:
        for item in payload.items:
            moved += await apply_tidy(
                ctx.session, ctx.chat, item.title, item.category, item.subcategory, item.create
            )
    except ValueError as exc:
        await ctx.session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await ctx.session.commit()
    return TidyApplyOut(moved=moved, categories=nodes_out(await tree_for(ctx.session, ctx.chat)))


@router.post("/chats/{chat_id}/category-suggestion/decline", status_code=204)
async def decline_category_proposal(title: str, ctx: ChatContext = Depends(get_chat_context)):
    """«Не надо» на предложение ИИ создать категорию — для этого названия больше не предлагаем."""
    await decline(ctx.session, ctx.chat.id, title)
    await ctx.session.commit()


@router.get("/chats/{chat_id}/photos/{filename}")
async def get_photo(filename: str, ctx: ChatContext = Depends(get_chat_context)):
    # ctx.member гарантирует, что запрашивающий состоит в этом же чате
    path = settings.photos_dir / filename
    if not filename.startswith(f"{ctx.chat.id}_") or not path.is_file():
        raise HTTPException(status_code=404, detail="Фото не найдено")
    return FileResponse(path)


@router.patch("/chats/{chat_id}/expenses/{expense_id}", response_model=ExpenseOut)
async def update_expense(
    expense_id: int,
    ctx: ChatContext = Depends(get_chat_context),
    title: str | None = Form(None),
    amount: str | None = Form(None),
    category: str | None = Form(None),
    subcategory: str | None = Form(None),  # "" — убрать подкатегорию
    expense_date: str | None = Form(None),
    payer_member_id: int | None = Form(None),
    split_type: str | None = Form(None),
    participant_ids: str | None = Form(None),
    custom_shares: str | None = Form(None),
    card_id: str | None = Form(None),  # "" — своими деньгами
    repays_card_id: str | None = Form(None),  # "" — не платёж по карте
    photo: UploadFile | None = File(None),
):
    expense = await ctx.session.get(Expense, expense_id)
    if expense is None or expense.chat_id != ctx.chat.id:
        raise HTTPException(status_code=404, detail="Трата не найдена")

    if title is not None:
        expense.title = title.strip()[:255]
    if category is not None and category != expense.category:
        expense.category = category
        expense.subcategory = None  # подкатегория старой категории к новой не подходит
    if subcategory is not None:
        expense.subcategory = subcategory or None
    if expense_date is not None:
        try:
            expense.expense_date = dt.date.fromisoformat(expense_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Дата должна быть в формате YYYY-MM-DD") from exc
    if payer_member_id is not None:
        expense.payer_member_id = payer_member_id

    new_amount = expense.amount
    if amount is not None:
        try:
            new_amount = Decimal(amount)
        except InvalidOperation as exc:
            raise HTTPException(status_code=400, detail="Некорректная сумма") from exc
        expense.amount = new_amount

    effective_split = split_type or expense.split_type
    recompute_shares = any(
        v is not None for v in (amount, split_type, participant_ids, custom_shares, payer_member_id)
    )
    if recompute_shares:
        if effective_split == "equal":
            if participant_ids is not None:
                ids = [int(i) for i in _parse_json_field(participant_ids, [])]
            else:
                result = await ctx.session.execute(
                    select(ExpenseShare.member_id).where(ExpenseShare.expense_id == expense.id)
                )
                ids = [row[0] for row in result.all()]
            await _validate_members(ctx.session, ctx.chat.id, set(ids) | {expense.payer_member_id})
            new_shares = build_equal_shares(new_amount, ids)
        else:
            raw_custom = _parse_json_field(custom_shares, None)
            if raw_custom is None:
                raise HTTPException(
                    status_code=400, detail="Для ручного режима нужно передать custom_shares"
                )
            pairs = [(int(item["member_id"]), Decimal(str(item["amount"]))) for item in raw_custom]
            await _validate_members(
                ctx.session, ctx.chat.id, {mid for mid, _ in pairs} | {expense.payer_member_id}
            )
            try:
                new_shares = build_custom_shares(new_amount, pairs)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        expense.split_type = effective_split

        await ctx.session.execute(
            ExpenseShare.__table__.delete().where(ExpenseShare.expense_id == expense.id)
        )
        for member_id, share_amount in new_shares:
            ctx.session.add(ExpenseShare(expense_id=expense.id, member_id=member_id, amount=share_amount))

    if card_id is not None:
        expense.card_id = await _card_for(ctx, card_id, expense.payer_member_id)
    elif expense.card_id is not None and payer_member_id is not None:
        # Плательщика сменили — карта прежнего плательщика к трате больше не относится
        card = await ctx.session.get(CreditCard, expense.card_id)
        payer = await ctx.session.get(Member, expense.payer_member_id)
        if card is None or payer is None or payer.tg_user_id != card.chat_id:
            expense.card_id = None
    if repays_card_id is not None:
        expense.repays_card_id = await _card_for(ctx, repays_card_id, expense.payer_member_id)
    elif expense.repays_card_id is not None and payer_member_id is not None:
        card = await ctx.session.get(CreditCard, expense.repays_card_id)
        payer = await ctx.session.get(Member, expense.payer_member_id)
        if card is None or payer is None or payer.tg_user_id != card.chat_id:
            expense.repays_card_id = None

    if photo is not None and photo.filename:
        expense.photo_path = await _save_photo(ctx.chat.id, photo)

    if title is not None or category is not None or subcategory is not None:
        await learn_category(ctx.session, ctx.chat.id, expense.title, expense.category, expense.subcategory)
    await ctx.session.commit()

    result = await ctx.session.execute(select(ExpenseShare).where(ExpenseShare.expense_id == expense.id))
    final_shares = list(result.scalars().all())
    out = _to_out(expense, final_shares, ctx.member)
    await _check_budgets(ctx, expense.id)
    return out


@router.delete("/chats/{chat_id}/expenses/{expense_id}", status_code=204)
async def delete_expense(expense_id: int, ctx: ChatContext = Depends(get_chat_context)):
    expense = await ctx.session.get(Expense, expense_id)
    if expense is None or expense.chat_id != ctx.chat.id:
        raise HTTPException(status_code=404, detail="Трата не найдена")
    title = expense.title
    if expense.photo_path:
        photo_path = settings.photos_dir / expense.photo_path
        if photo_path.is_file():
            photo_path.unlink(missing_ok=True)
    await ctx.session.delete(expense)
    await ctx.session.commit()
    await notify_expense_deleted(ctx.chat, title, ctx.member)
    return None
