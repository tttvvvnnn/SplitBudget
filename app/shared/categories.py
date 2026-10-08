"""Дерево категорий трат: категория → подкатегории, с иконками.

Категория и подкатегория хранятся в тратах обычными строками (Expense.category /
Expense.subcategory), поэтому дерево — это то, что предлагает мини-апп, а не жёсткое
ограничение: траты со старыми или нестандартными категориями продолжают работать.
Отдаётся фронтенду в GET /chats/{id}/me (category_tree)."""
from __future__ import annotations

from decimal import Decimal

from app.shared.schemas import CategoryStat, SubcategoryStat

OTHER = "Другое"

# (категория, иконка, подкатегории)
CATEGORY_TREE: list[tuple[str, str, list[str]]] = [
    ("Продукты", "🛒", ["Супермаркет", "Алкоголь", "Рынок и фермерское", "Доставка продуктов"]),
    ("Кафе и рестораны", "🍽️", ["Кафе", "Рестораны", "Кофе", "Фастфуд", "Доставка еды"]),
    ("Транспорт", "🚗", ["Такси", "Общественный транспорт", "Топливо", "Парковка", "Каршеринг", "Обслуживание авто"]),
    ("Дом", "🏠", ["Коммуналка", "Аренда", "Интернет и связь", "Ремонт и мебель", "Бытовая химия"]),
    ("Здоровье", "💊", ["Аптека", "Врачи", "Анализы", "Спорт"]),
    ("Красота", "💅", ["Парикмахер", "Косметика"]),
    ("Одежда и обувь", "👕", ["Одежда", "Обувь", "Аксессуары"]),
    ("Дети", "🧸", ["Игрушки", "Кружки и секции", "Школа и сад"]),
    ("Развлечения", "🎬", ["Кино и театр", "Хобби", "Игры"]),
    ("Путешествия", "✈️", ["Билеты", "Жильё", "Экскурсии"]),
    ("Подписки", "📱", ["Стриминг", "Музыка", "Облако и софт"]),
    ("Подарки", "🎁", []),
    ("Питомцы", "🐾", ["Корм", "Ветеринар"]),
    (OTHER, "📦", []),
]

CATEGORY_NAMES: list[str] = [name for name, _, _ in CATEGORY_TREE]


def category_tree_out() -> list[dict]:
    return [
        {"name": name, "icon": icon, "subcategories": subs} for name, icon, subs in CATEGORY_TREE
    ]


def summarize_by_category(rows) -> tuple[Decimal, list[CategoryStat]]:
    """Сводка для статистики: rows — (категория, подкатегория | None, сумма). Возвращает общую
    сумму и категории по убыванию суммы, у каждой — разбивка по подкатегориям (тоже по
    убыванию; траты без подкатегории — subcategory="")."""
    totals: dict[str, Decimal] = {}
    counts: dict[str, int] = {}
    sub_totals: dict[str, dict[str, Decimal]] = {}
    sub_counts: dict[str, dict[str, int]] = {}
    for category, subcategory, amount in rows:
        sub = subcategory or ""
        totals[category] = totals.get(category, Decimal("0")) + amount
        counts[category] = counts.get(category, 0) + 1
        st = sub_totals.setdefault(category, {})
        sc = sub_counts.setdefault(category, {})
        st[sub] = st.get(sub, Decimal("0")) + amount
        sc[sub] = sc.get(sub, 0) + 1

    stats = [
        CategoryStat(
            category=c,
            total=t,
            count=counts[c],
            subcategories=sorted(
                (
                    SubcategoryStat(subcategory=s, total=st, count=sub_counts[c][s])
                    for s, st in sub_totals[c].items()
                ),
                key=lambda x: x.total,
                reverse=True,
            ),
        )
        for c, t in totals.items()
    ]
    stats.sort(key=lambda x: x.total, reverse=True)
    return sum(totals.values(), Decimal("0")), stats
