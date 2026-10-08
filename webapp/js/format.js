import { state } from "./state.js";

/* Даты, деньги, экранирование, иконки категорий — чистые помощники форматирования. */

function todayMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function todayISO() {
  const d = new Date();
  const off = d.getTimezoneOffset();
  const local = new Date(d.getTime() - off * 60000);
  return local.toISOString().slice(0, 10);
}

/* Иконки категорий — только для отображения, на сервер и в БД не уходят (категория
   как была строкой, так и осталась). Список категорий настраивается в
   app/shared/config.py — если добавите новую, впишите и сюда, иначе будет 🏷️. */
const CATEGORY_ICONS = {
  "Еда": "🍔",
  "Транспорт": "🚗",
  "ЖКХ": "🏠",
  "Развлечения": "🎬",
  "Здоровье": "💊",
  "Одежда": "👕",
  "Подписки": "📱",
  "Другое": "📦",
};

function categoryIcon(category) {
  return CATEGORY_ICONS[category] || "🏷️";
}

function monthLabel(ym) {
  const [y, m] = ym.split("-").map(Number);
  const names = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
  ];
  return `${names[m - 1]} ${y}`;
}

function shiftMonth(ym, delta) {
  let [y, m] = ym.split("-").map(Number);
  m += delta;
  if (m < 1) { m = 12; y -= 1; }
  if (m > 12) { m = 1; y += 1; }
  return `${y}-${String(m).padStart(2, "0")}`;
}

function fmtMoney(amount) {
  const n = Number(amount);
  const currency = state.chat ? state.chat.currency : "";
  return `${n.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

export { todayMonth, todayISO, categoryIcon, monthLabel, shiftMonth, fmtMoney, escapeHtml };
