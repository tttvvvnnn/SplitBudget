"""Миграция 7b3c9d2e4f10: старые категории переезжают в новое дерево."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent.parent


def test_old_categories_are_renamed(tmp_path, monkeypatch):
    db = tmp_path / "old.sqlite3"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    monkeypatch.setattr("app.shared.config.settings.DATABASE_PATH", str(db))

    command.upgrade(cfg, "5e1f0a7c3d21")
    con = sqlite3.connect(db)
    con.execute("insert into chats(id, title, currency, created_at, is_personal) values (-1, 't', 'RUB', '2026-01-01', 0)")
    con.execute("insert into members(chat_id, tg_user_id, full_name, is_active, added_at) values (-1, 1, 'A', 1, '2026-01-01')")
    for i, cat in enumerate(["Еда", "ЖКХ", "Одежда", "Транспорт"]):
        con.execute(
            "insert into expenses(chat_id, title, amount, category, expense_date, payer_member_id,"
            " split_type, created_by_member_id, created_at, updated_at)"
            f" values (-1, 'e{i}', 10, ?, '2026-01-01', 1, 'equal', 1, '2026-01-01', '2026-01-01')",
            (cat,),
        )
    con.commit()
    con.close()

    command.upgrade(cfg, "7b3c9d2e4f10")
    con = sqlite3.connect(db)
    rows = dict(
        (title, (cat, sub))
        for title, cat, sub in con.execute("select title, category, subcategory from expenses")
    )
    assert rows == {
        "e0": ("Продукты", None),
        "e1": ("Дом", "Коммуналка"),
        "e2": ("Одежда и обувь", None),
        "e3": ("Транспорт", None),
    }


def test_old_expenses_get_subcategories(tmp_path, monkeypatch):
    db = tmp_path / "old.sqlite3"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    monkeypatch.setattr("app.shared.config.settings.DATABASE_PATH", str(db))

    command.upgrade(cfg, "b3e7c9d1f625")
    con = sqlite3.connect(db)
    con.execute("insert into chats(id, title, currency, created_at, is_personal) values (-1, 't', 'RUB', '2026-01-01', 0)")
    con.execute("insert into members(chat_id, tg_user_id, full_name, is_active, added_at) values (-1, 1, 'A', 1, '2026-01-01')")
    rows = [
        ("Винлаб", "Продукты", None),  # словарь согласен с категорией → подкатегория
        ("Пятёрочка у дома", "Другое", None),  # «Другое» → категория и подкатегория из словаря
        ("Винлаб подарок", "Подарки", None),  # человек выбрал другую категорию — не трогаем
        ("Магнит", "Продукты", "Фрукты и овощи"),  # подкатегория уже есть — не трогаем
        ("Что-то непонятное", "Продукты", None),  # словарь не знает — не трогаем
    ]
    for i, (title, cat, sub) in enumerate(rows):
        con.execute(
            "insert into expenses(chat_id, title, amount, category, subcategory, expense_date, payer_member_id,"
            " split_type, created_by_member_id, created_at, updated_at)"
            " values (-1, ?, 10, ?, ?, '2026-01-01', 1, 'equal', 1, '2026-01-01', '2026-01-01')",
            (title, cat, sub),
        )
    con.commit()
    con.close()

    command.upgrade(cfg, "c4f8a0e2b736")
    con = sqlite3.connect(db)
    got = {title: (cat, sub) for title, cat, sub in con.execute("select title, category, subcategory from expenses")}
    assert got == {
        "Винлаб": ("Продукты", "Алкоголь"),
        "Пятёрочка у дома": ("Продукты", "Супермаркет"),
        "Винлаб подарок": ("Подарки", None),
        "Магнит": ("Продукты", "Фрукты и овощи"),
        "Что-то непонятное": ("Продукты", None),
    }
