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

/* Иконка категории — из дерева категорий с сервера (state.categoryTree, см.
   app/shared/categories.py). Для категорий не из дерева — 🏷️. */
function categoryIcon(category) {
  const node = state.categoryTree.find((c) => c.name === category);
  return node ? node.icon : "🏷️";
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
