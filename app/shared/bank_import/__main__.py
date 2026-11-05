"""Проверка разбора выписки: `python -m app.shared.bank_import выписка.pdf [--all]`.

Печатает итоги по типам и очищенные названия трат; с --all — каждую операцию."""
from __future__ import annotations

import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from app.shared.bank_import import ImportError_, parse

_NAMES = {
    "expense": "🛒 траты",
    "refund": "↩️ возвраты",
    "income": "💰 доходы",
    "savings": "🐷 копилка",
    "self": "↔️ свои переводы",
    "contract": "📄 переводы между договорами",
    "transfer": "📲 переводы по телефону/реквизитам",
    "cash": "💵 наличные",
    "unknown": "❓ непонятные",
}


def _money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    path = Path(argv[0])
    try:
        statement = parse(path.read_bytes(), path.name)
    except ImportError_ as exc:
        print(f"Ошибка: {exc}")
        return 1
    print(f"Банк: {statement.bank}, период {statement.period_from} — {statement.period_to}")
    print(f"Операций: {len(statement.operations)}; итоги сошлись: "
          f"+{_money(statement.total_in or 0)} / −{_money(statement.total_out or 0)}")
    groups: dict[str, list] = defaultdict(list)
    for op in statement.operations:
        groups[op.kind].append(op)
    print()
    for kind, name in _NAMES.items():
        ops = groups.get(kind)
        if ops:
            total = sum((op.amount for op in ops), Decimal("0"))
            print(f"{name}: {len(ops)} шт., {_money(total)}")
    titles: dict[str, list] = defaultdict(list)
    for op in groups.get("expense", []):
        titles[op.title].append(op)
    print("\nНазвания трат:")
    for title, ops in sorted(titles.items(), key=lambda kv: sum(o.amount for o in kv[1])):
        print(f"  {title:<30} {len(ops):>3} шт. {_money(-sum(o.amount for o in ops)):>12}   ← {ops[0].description}")
    contracts: dict[str, Decimal] = defaultdict(Decimal)
    for op in groups.get("contract", []):
        contracts[op.contract or "?"] += op.amount
    if contracts:
        print("\nДоговоры:")
        for contract, total in contracts.items():
            print(f"  {contract}: {_money(total)}")
    if "--all" in argv:
        print()
        for op in statement.operations:
            print(f"{op.date} {op.time or '':5} {_money(op.amount):>12} {op.kind:<9} {op.title}  [{op.description}]")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
