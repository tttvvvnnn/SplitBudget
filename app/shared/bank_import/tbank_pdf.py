"""PDF «Справка о движении средств» Т-Банка (дебетовый счёт).

Таблица: «Дата и время операции | Дата списания | Сумма в валюте операции | Сумма операции
в валюте карты | Описание операции | Номер карты». Строка операции занимает 2–3 строки
текста: на первой даты, суммы, начало описания и карта, ниже — время и продолжение описания.
Колонки режем по координатам заголовка, а не по пробелам: длинное описание иногда вплотную
подходит к номеру карты. В конце — итоги «Пополнения: … ₽ / Расходы: … ₽», по ним проверяем,
что ни одна операция не потерялась.
"""
from __future__ import annotations

import datetime as dt
import io
import re
from decimal import Decimal, InvalidOperation

from app.shared.bank_import.base import ImportError_, Operation, Statement

_DATE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
_TIME = re.compile(r"^\d{2}:\d{2}$")
_PERIOD = re.compile(r"за период с (\d{2}\.\d{2}\.\d{4}) по (\d{2}\.\d{2}\.\d{4})")
_CONTRACT = re.compile(r"Номер договора:\s*(\d+)")
_TOTAL = re.compile(r"^(Пополнения|Расходы):\s*([\d\s ]+,\d{2})")
# Слова заголовка, по которым определяем левые границы колонок
_HEADER = ("Дата", "Дата", "Сумма", "Сумма", "Описание", "Номер")


def _date(text: str) -> dt.date:
    return dt.datetime.strptime(text, "%d.%m.%Y").date()


def _money(text: str) -> Decimal:
    cleaned = re.sub(r"[\s ₽]", "", text).replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ImportError_(f"Не понял сумму «{text}»") from exc


def _lines(words: list[dict]) -> list[list[dict]]:
    """Слова страницы → строки (по вертикали, допуск 2 pt), слева направо."""
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - word["top"]) <= 2:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def _columns(line: list[dict]) -> list[float] | None:
    """Если это строка заголовка таблицы — левые границы шести колонок."""
    texts = [w["text"] for w in line]
    if texts[:1] != ["Дата"] or "Описание" not in texts:
        return None
    starts: list[float] = []
    i = 0
    for word in line:
        if i < len(_HEADER) and word["text"] == _HEADER[i]:
            starts.append(word["x0"])
            i += 1
    return starts if len(starts) == len(_HEADER) else None


def _cells(line: list[dict], cols: list[float]) -> list[str]:
    cells = [[] for _ in cols]
    for word in line:
        index = max(i for i, x in enumerate(cols) if word["x0"] >= x - 3) if word["x0"] >= cols[0] - 3 else 0
        cells[index].append(word["text"])
    return [" ".join(c) for c in cells]


def _join(parts: list[str]) -> str:
    """Склеивает перенесённое описание: «ЦППК-» + «2000012_SBP» → «ЦППК-2000012_SBP»."""
    text = ""
    for part in parts:
        if not part:
            continue
        text = text + part if text.endswith("-") or not text else f"{text} {part}"
    return text


def parse_words(pages: list[list[dict]]) -> Statement:
    """Разбор по словам pdfplumber (dict с text/x0/top) — отдельно от PDF, чтобы тестировать."""
    statement = Statement(bank="tbank", period_from=None, period_to=None, account=None)
    cols: list[float] | None = None
    current: dict | None = None

    def close() -> None:
        nonlocal current
        if current is not None:
            statement.operations.append(
                Operation(
                    date=current["date"],
                    amount=current["amount"],
                    description=_join(current["desc"]),
                    card=current["card"],
                    time=current["time"],
                )
            )
        current = None

    seen_bank = False
    for words in pages:
        for line in _lines(words):
            text = " ".join(w["text"] for w in line)
            if "ТБАНК" in text.upper().replace(" ", ""):
                seen_bank = True
            if m := _PERIOD.search(text):
                statement.period_from, statement.period_to = _date(m[1]), _date(m[2])
            if (m := _CONTRACT.search(text)) and statement.account is None:
                statement.account = m[1]
            if m := _TOTAL.match(text):
                close()
                value = _money(m[2])
                if m[1] == "Пополнения":
                    statement.total_in = value
                else:
                    statement.total_out = value
                continue
            if header := _columns(line):
                close()
                cols = header
                continue
            if cols is None:
                continue
            cells = _cells(line, cols)
            if _DATE.match(cells[0].split(" ")[0] if cells[0] else ""):
                close()
                card = cells[5].strip() or None
                current = {
                    "date": _date(cells[0].split(" ")[0]),
                    "amount": _money(cells[3] or cells[2]),
                    "desc": [cells[4]],
                    "card": card if card and card.isdigit() else None,
                    "time": None,
                }
            elif current is not None and (not cells[0] or _TIME.match(cells[0].split(" ")[0])) and not any(
                cells[i] for i in (2, 3, 5)
            ):
                if cells[0] and current["time"] is None:
                    current["time"] = cells[0].split(" ")[0]
                current["desc"].append(cells[4])
            else:
                close()  # подвал страницы, подпись и прочее — не часть операции
        close()

    if not seen_bank or cols is None:
        raise ImportError_("Это не похоже на справку о движении средств Т-Банка")
    if not statement.operations:
        raise ImportError_("В выписке нет операций")
    statement.check_totals()
    # В выписке новые сверху; удобнее по порядку
    statement.operations.reverse()
    return statement


def parse(data: bytes) -> Statement:
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = [page.extract_words() for page in pdf.pages]
    except Exception as exc:  # битый PDF, пароль и т.п.
        raise ImportError_("Не получилось открыть PDF") from exc
    if not any(pages):
        raise ImportError_("В PDF нет текста — похоже на скан, такие пока не читаю")
    return parse_words(pages)
