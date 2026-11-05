"""Черновая разметка операций выписки и очистка названий.

«Оплата в DA2A ТС Пятерочка» → трата «Пятерочка»; «CPPK-2000002-APB09 MOSCOW RUS» → «CPPK».
Чистое название нужно и для траты, и для автокатегорий: выученные правила и словарь магазинов
срабатывают на «пятерочка», а не на «da2a тс пятерочка».
"""
from __future__ import annotations

import re

from app.shared.bank_import import base
from app.shared.bank_import.base import Operation

# (начало описания, тип, у описания после префикса есть название)
_RULES: list[tuple[str, str]] = [
    ("Оплата услуг", base.EXPENSE),
    ("Оплата в", base.EXPENSE),
    ("Оплата", base.EXPENSE),
    ("Возврат покупки через СБП.", base.REFUND),
    ("Возврат покупки", base.REFUND),
    ("Перевод для пополнения счета Инвесткопилка", base.SAVINGS),
    ("Пополнение Инвесткопилки", base.SAVINGS),
    ("Перевод себе", base.SELF),
    ("Вывод средств с брокерского счета", base.SELF),
    ("Внутренний перевод на договор", base.CONTRACT),
    ("Перевод с договора", base.CONTRACT),
    ("Внешний перевод", base.TRANSFER),
    ("Внешний банковский перевод", base.TRANSFER),
    ("Пополнение. Система быстрых платежей", base.TRANSFER),
    ("Пополнение. ", base.TRANSFER),
    ("Банковский перевод.", base.INCOME),
    ("Кэшбэк", base.INCOME),
    ("Проценты", base.INCOME),
    ("Снятие наличных", base.CASH),
]

_LEGAL = {"ооо", "ип", "ао", "пао", "зао", "оао", "llc", "ltd"}
_NOISE = {"sbp", "p", "qr", "c2b", "тс", "rus", "g.", "г."}
# Одна сеть пишется в выписке то латиницей, то кириллицей — сводим, чтобы была одна группа
_ALIASES = {"pyaterochka": "Пятерочка", "cppk": "ЦППК", "magnit": "Магнит", "perekrestok": "Перекресток"}
_CONTRACT_NUM = re.compile(r"договор[а]?\s+(\d+)")


def clean_title(text: str) -> str:
    """Название магазина из хвоста описания после «Оплата в»."""
    text = re.sub(r"-\s+", "-", text.strip())  # «ЦППК- 2000012» — перенос строки в PDF
    tokens = text.split()
    # «… MOSCOW RUS», «… g. Tula RUS»: город и страна в конце карточных операций
    if len(tokens) > 1 and tokens[-1].upper() == "RUS":
        tokens = tokens[:-1]
        if len(tokens) > 1:
            tokens = tokens[:-1]
        if len(tokens) > 1 and tokens[-1].lower() in ("g.", "г."):
            tokens = tokens[:-1]
    out: list[str] = []
    for token in tokens:
        # «YM*AMPP» — префикс платёжного агрегатора
        if "*" in token and len(token.split("*", 1)[0]) <= 3:
            token = token.split("*", 1)[1]
        kept = []
        for part in token.split("_"):
            # «CPPK-2000002-APB09» → «CPPK»: куски с цифрами — номера касс и точек
            pieces = [p for p in part.split("-") if p and not re.search(r"\d", p)]
            part = "-".join(pieces)
            if part and part.lower() not in _NOISE:
                kept.append(part)
        word = " ".join(kept).strip(".,;:_- ")
        if word and word.lower() not in _NOISE and word.lower() not in _LEGAL:
            out.append(word)
    title = " ".join(out)
    if len(re.sub(r"[^A-Za-zА-Яа-яЁё]", "", title)) < 2:
        # Остался мусор вроде «Ч» из «Ч50510_P_QR» — лучше показать как есть, без суффиксов
        title = re.sub(r"(_P)?_(SBP|QR)$", "", text).strip()
    if (alias := _ALIASES.get(title.split(" ")[0].lower())) is not None:
        return alias
    if title.isupper() and len(title) > 3 and re.search(r"[A-Z]", title):
        title = title.title()  # «PYATEROCHKA» → «Pyaterochka»
    return title


def classify(op: Operation) -> Operation:
    desc = op.description
    for prefix, kind in _RULES:
        if desc.startswith(prefix):
            op.kind = kind
            rest = desc[len(prefix):].strip(" .")
            break
    else:
        op.kind = base.UNKNOWN
        rest = desc

    if op.kind == base.EXPENSE:
        op.title = clean_title(rest) or desc
    elif op.kind == base.REFUND:
        rest = "" if rest.startswith("Возврат покупки") else rest
        op.title = clean_title(rest) if rest else "Возврат покупки"
    elif op.kind == base.CONTRACT:
        if m := _CONTRACT_NUM.search(desc):
            op.contract = m[1]
        op.title = desc
    elif op.kind == base.CASH:
        op.title = "Наличные"
    elif op.kind == base.INCOME and desc.startswith("Кэшбэк"):
        op.title = "Кэшбэк"
    else:
        op.title = desc
    # Знак важнее описания: «Оплата» с плюсом — это возврат, доход с минусом — непонятно что
    if op.kind == base.EXPENSE and op.amount > 0:
        op.kind = base.REFUND
    elif op.kind == base.INCOME and op.amount < 0:
        op.kind = base.UNKNOWN
    return op
