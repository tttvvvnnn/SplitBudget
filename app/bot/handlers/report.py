"""Команда /summary — итоги текущего месяца: в личке по «Моим финансам», в группе — по чату.
Команда /pdf — PDF-отчёт «Моих финансов» за текущий месяц плана (app/shared/pdf_report.py)."""
from __future__ import annotations

import datetime as dt

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from app.shared import pdf_report
from app.shared.crud import get_or_create_personal_space
from app.shared.database import async_session_maker
from app.shared.models import Chat
from app.shared.obligations import month_of
from app.shared.report import build_report

router = Router(name="report")


@router.message(Command("summary", "itogi"))
async def summary_command(message: Message) -> None:
    month = month_of(dt.date.today())
    async with async_session_maker() as session:
        if message.chat.type == "private":
            user = message.from_user
            chat, _ = await get_or_create_personal_space(session, user.id, user.username, user.full_name)
        else:
            chat = await session.get(Chat, message.chat.id)
        text = await build_report(session, chat, month, partial=True) if chat else None
        await session.commit()
    await message.answer(text or "В этом месяце трат пока нет.")


@router.message(Command("pdf"))
async def pdf_command(message: Message) -> None:
    if message.chat.type != "private":
        await message.answer("PDF-отчёт — по «Моим финансам»: напишите /pdf мне в личку.")
        return
    user = message.from_user
    async with async_session_maker() as session:
        chat, _ = await get_or_create_personal_space(session, user.id, user.username, user.full_name)
        report = await pdf_report.collect(session, chat, dt.date.today())
        await session.commit()
    pdf = await pdf_report.to_pdf(pdf_report.render_html(report))
    await message.answer_document(BufferedInputFile(pdf, filename=pdf_report.file_name(report)),
                                  caption="📄 Отчёт «Мои финансы»")
