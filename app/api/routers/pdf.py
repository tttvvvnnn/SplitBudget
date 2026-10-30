"""PDF-отчёт «Моих финансов» за месяц плана (app/shared/pdf_report.py).

В веб-версии файл скачивается (GET …/report.pdf). Внутри Telegram мини-апп скачивать файлы
толком не умеет, поэтому там отчёт присылает бот в личку (POST …/report/send).
"""
from __future__ import annotations

import datetime as dt
from typing import Literal

from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from app.api.dependencies import ChatContext, get_chat_context
from app.bot.bot_instance import bot
from app.shared import pdf_report

router = APIRouter(tags=["report"])

Theme = Literal["light", "dark"]


class ReportSendIn(BaseModel):
    date: dt.date | None = None  # любой день месяца плана; нет — текущий
    theme: Theme = "light"


async def build(ctx: ChatContext, date: dt.date | None, theme: str) -> tuple[bytes, str]:
    if not ctx.chat.is_personal:
        raise HTTPException(status_code=400, detail="PDF-отчёт есть только в «Моих финансах»")
    report = await pdf_report.collect(ctx.session, ctx.chat, date or dt.date.today())
    return await pdf_report.to_pdf(pdf_report.render_html(report, theme)), pdf_report.file_name(report)


@router.get("/chats/{chat_id}/report.pdf")
async def download(
    date: dt.date | None = None, theme: Theme = "light", ctx: ChatContext = Depends(get_chat_context)
) -> Response:
    pdf, name = await build(ctx, date, theme)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/chats/{chat_id}/report/send")
async def send(payload: ReportSendIn, ctx: ChatContext = Depends(get_chat_context)) -> dict:
    pdf, name = await build(ctx, payload.date, payload.theme)
    try:
        await bot.send_document(ctx.member.tg_user_id, BufferedInputFile(pdf, filename=name),
                                caption="📄 Отчёт «Мои финансы»")
    except TelegramAPIError as exc:
        raise HTTPException(status_code=409, detail="Не получилось отправить: напишите боту /start в личке") from exc
    return {"sent": True}
