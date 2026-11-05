"""Импорт банковских выписок в «Мои финансы» (дизайн: design/bank-import.md).

Этап 1: разбор файла в список операций и черновая разметка (что это: трата, доход,
перевод себе…). Записи в базу пока не делаются.

    statement = parse(data, "Справка о движении средств.pdf")
    for op in statement.operations: ...

Проверка на настоящем файле: `python -m app.shared.bank_import путь/к/выписке.pdf`.
"""
from __future__ import annotations

from app.shared.bank_import.base import ImportError_, Operation, Statement
from app.shared.bank_import.classify import classify

MAX_SIZE = 10 * 1024 * 1024


def parse(data: bytes, file_name: str = "") -> Statement:
    """Определяет банк и формат и разбирает выписку. Ошибки — ImportError_ с текстом для человека."""
    if len(data) > MAX_SIZE:
        raise ImportError_("Файл больше 10 МБ")
    if data[:5] == b"%PDF-":
        from app.shared.bank_import import tbank_pdf

        statement = tbank_pdf.parse(data)
    else:
        raise ImportError_("Пока умею только PDF-справку о движении средств Т-Банка")
    for op in statement.operations:
        classify(op)
    return statement


__all__ = ["ImportError_", "MAX_SIZE", "Operation", "Statement", "parse"]
