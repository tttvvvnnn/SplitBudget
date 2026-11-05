"""Импорт банковской выписки, этап 1: разбор PDF Т-Банка и разметка операций.

Настоящую выписку в репозиторий не кладём (ФИО, адрес, номера договоров), поэтому
страницы собираем из слов с координатами — так их отдаёт pdfplumber."""
import datetime as dt
from decimal import Decimal

import pytest

from app.shared.bank_import import ImportError_, base, parse
from app.shared.bank_import.classify import classify, clean_title
from app.shared.bank_import.tbank_pdf import parse_words

COLS = (56, 126, 199, 294, 389, 499)


def _line(top: float, *cells: str) -> list[dict]:
    """Строка таблицы: текст по колонкам, каждое слово — отдельный dict."""
    words = []
    for x, cell in zip(COLS, cells):
        offset = 0
        for word in cell.split():
            words.append({"text": word, "x0": x + offset, "top": top})
            offset += 6 * len(word) + 4
    return words


def _text(top: float, text: str, x: float = 56) -> list[dict]:
    return [{"text": w, "x0": x + i * 30, "top": top} for i, w in enumerate(text.split())]


def _header(top: float) -> list[dict]:
    return (
        _line(top, "Дата и время", "Дата", "Сумма в валюте", "Сумма операции", "Описание", "Номер")
        + _line(top + 11, "операции", "списания", "операции", "в валюте карты", "операции", "карты")
    )


def _op(top, date, amount, desc1, card="0937", time="12:00", desc2=""):
    return _line(top, date, date, f"{amount} ₽", f"{amount} ₽", desc1, card) + _line(
        top + 11, time, time, "", "", desc2
    )


def _footer(top: float) -> list[dict]:
    return _text(top, "АО «ТБанк» универсальная лицензия Банка России № 2673", x=107)


def _pages(total_in="1 000,00", total_out="1 924,69"):
    page1 = (
        _text(40, "АКЦИОНЕРНОЕ ОБЩЕСТВО «ТБАНК»", x=300)
        + _text(150, "Справка о движении средств")
        + _text(234, "Номер договора: 5488043547")
        + _text(291, "Движение средств за период с 01.08.2026 по 31.08.2026")
        + _header(318)
        + _op(345, "31.08.2026", "-74.69", "Перевод для пополнения", desc2="счета Инвесткопилка")
        + _op(370, "31.08.2026", "-1 625.31", "Оплата в DA2A ТС", desc2="Пятерочка", time="19:25")
        + _op(395, "30.08.2026", "+1 000.00", "Перевод себе")
        + _footer(784)
    )
    page2 = (
        _header(40)
        + _op(67, "29.08.2026", "-178.00", "Оплата в ЦППК-", desc2="2000012_SBP")
        + _op(92, "28.08.2026", "-46.69", "Оплата в GLOBUS KOROLEV", card="5312")
        + _text(130, f"Пополнения: {total_in} ₽")
        + _text(145, f"Расходы: {total_out} ₽")
        + _text(200, "С уважением,")
        + _footer(784)
    )
    return [page1, page2]


def test_parse_words_reads_operations_and_header():
    st = parse_words(_pages())
    assert (st.period_from, st.period_to) == (dt.date(2026, 8, 1), dt.date(2026, 8, 31))
    assert st.account == "5488043547"
    assert st.total_in == Decimal("1000.00") and st.total_out == Decimal("1924.69")
    # В выписке новые сверху, у нас — по порядку
    assert [op.date.day for op in st.operations] == [28, 29, 30, 31, 31]
    globus, cppk, me, shop, piggy = st.operations
    assert shop.amount == Decimal("-1625.31")
    assert shop.description == "Оплата в DA2A ТС Пятерочка"
    assert shop.time == "19:25"
    assert piggy.description == "Перевод для пополнения счета Инвесткопилка"
    assert cppk.description == "Оплата в ЦППК-2000012_SBP"  # перенос внутри слова склеен
    assert globus.card == "5312"
    assert me.amount == Decimal("1000.00")


def test_totals_mismatch_is_an_error():
    with pytest.raises(ImportError_, match="не сошлись"):
        parse_words(_pages(total_out="1 900,00"))


def test_not_a_tbank_statement():
    with pytest.raises(ImportError_):
        parse_words([_text(40, "Какой-то другой документ")])


def test_parse_rejects_non_pdf():
    with pytest.raises(ImportError_, match="только PDF"):
        parse(b"date;amount\n", "x.csv")


@pytest.mark.parametrize(
    "raw, title",
    [
        ("DA2A ТС Пятерочка", "Пятерочка"),
        ("PYATEROCHKA 10563 MOSCOW RUS", "Pyaterochka"),
        ("CPPK-2000002-APB09 MOSCOW RUS", "Cppk"),
        ("ЦППК- 2000012_SBP", "ЦППК"),
        ("Vkusnoitochka_24105_SBP", "Vkusnoitochka"),
        ("Кофейня Gagarin._SBP", "Кофейня Gagarin"),
        (".Фрукты и овощи_P_SBP", "Фрукты и овощи"),
        ("Магазин 5208_P_QR", "Магазин"),
        ("ООО ПЕНТАН-К", "ПЕНТАН-К"),
        ("zt.ru g. Tula RUS", "zt.ru"),
        ("OKKO SANKT- PETERBU RUS", "Okko"),
        ("YM*AMPP MOSCOW RUS", "Ampp"),
        ("Ч50510_P_QR", "Ч50510"),
        ("Сервисы Яндекса", "Сервисы Яндекса"),
    ],
)
def test_clean_title(raw, title):
    assert clean_title(raw) == title


@pytest.mark.parametrize(
    "desc, amount, kind, title",
    [
        ("Оплата в DA2A ТС Пятерочка", "-100", base.EXPENSE, "Пятерочка"),
        ("Оплата услуг mBank.MegaFon", "-310", base.EXPENSE, "mBank.MegaFon"),
        ("Возврат покупки через СБП. ivi.ru", "19", base.REFUND, "ivi.ru"),
        ("Возврат покупки через СБП. Возврат покупки", "178", base.REFUND, "Возврат покупки"),
        ("Перевод для пополнения счета Инвесткопилка", "-2.40", base.SAVINGS, None),
        ("Пополнение Инвесткопилки", "-150", base.SAVINGS, None),
        ("Перевод себе", "500", base.SELF, None),
        ("Вывод средств с брокерского счета", "317.81", base.SELF, None),
        ("Внешний перевод по номеру телефона", "-750", base.TRANSFER, None),
        ("Пополнение. Система быстрых платежей", "605", base.TRANSFER, None),
        ("Банковский перевод. Филиал № 7701 Банка ВТБ", "29000.33", base.INCOME, None),
        ("Кэшбэк за обычные покупки", "115", base.INCOME, "Кэшбэк"),
        ("Снятие наличных. Т-Банк, 13997 Королёв Russia", "-24000", base.CASH, "Наличные"),
        ("Что-то новое", "-1", base.UNKNOWN, None),
    ],
)
def test_classify(desc, amount, kind, title):
    op = classify(base.Operation(date=dt.date(2026, 8, 1), amount=Decimal(amount), description=desc))
    assert op.kind == kind
    if title is not None:
        assert op.title == title


def test_classify_contract_number():
    op = classify(base.Operation(dt.date(2026, 8, 1), Decimal("-5000"), "Внутренний перевод на договор 8179909434"))
    assert (op.kind, op.contract) == (base.CONTRACT, "8179909434")
    op = classify(base.Operation(dt.date(2026, 8, 1), Decimal("10000"), "Перевод с договора 5246300403"))
    assert (op.kind, op.contract) == (base.CONTRACT, "5246300403")
