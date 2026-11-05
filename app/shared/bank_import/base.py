from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal


class ImportError_(Exception):
    """Выписку не разобрать; текст показываем пользователю как есть."""


# Что делаем с операцией по умолчанию (пользователь может поменять в предпросмотре)
EXPENSE = "expense"  # трата
INCOME = "income"  # доход: зарплата, кэшбэк
REFUND = "refund"  # возврат покупки: уменьшает трату
SAVINGS = "savings"  # Инвесткопилка, накопительный счёт — не трата
SELF = "self"  # перевод между своими счетами — пропуск
CONTRACT = "contract"  # перевод на другой договор банка: кредитка, вклад или человек — спросить
TRANSFER = "transfer"  # перевод по телефону/реквизитам: человеку или себе в другой банк — спросить
CASH = "cash"  # снятие наличных — спросить
UNKNOWN = "unknown"


@dataclass
class Operation:
    date: dt.date  # дата операции (не списания)
    amount: Decimal  # в валюте счёта: минус — списание, плюс — поступление
    description: str  # как в выписке, переносы строк склеены
    card: str | None = None  # последние 4 цифры карты, None — операция без карты («—»)
    time: str | None = None  # 'HH:MM'
    kind: str = UNKNOWN
    title: str = ""  # очищенное название для траты/дохода
    contract: str | None = None  # номер договора для «Внутренний перевод на договор N»


@dataclass
class Statement:
    bank: str
    period_from: dt.date | None
    period_to: dt.date | None
    account: str | None  # номер договора/счёта из шапки
    operations: list[Operation] = field(default_factory=list)
    # Итоги из выписки — для проверки, что ничего не потеряли
    total_in: Decimal | None = None
    total_out: Decimal | None = None

    def check_totals(self) -> None:
        got_in = sum((op.amount for op in self.operations if op.amount > 0), Decimal("0"))
        got_out = -sum((op.amount for op in self.operations if op.amount < 0), Decimal("0"))
        bad_in = self.total_in is not None and got_in != self.total_in
        bad_out = self.total_out is not None and got_out != self.total_out
        if bad_in or bad_out:
            raise ImportError_(
                "Суммы не сошлись с итогами выписки "
                f"(пополнения {got_in} из {self.total_in}, расходы {got_out} из {self.total_out}). "
                "Пришли файл разработчику — формат, видимо, поменялся."
            )
