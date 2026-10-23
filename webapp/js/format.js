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
  const node = state.categoryNodes.find((c) => c.name === category) || state.categoryTree.find((c) => c.name === category);
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

/* День платежа или дохода: 1–30 или 31 — «последний день месяца» (в коротких месяцах платёж
   приходится на последний день, см. day_in_month в app/shared/obligations.py). */
const LAST_DAY = 31;

function dayOptionsHtml(selected) {
  const days = Array.from({ length: 30 }, (_, i) => i + 1)
    .map((d) => `<option value="${d}" ${d === selected ? "selected" : ""}>${d}</option>`);
  return days.join("") + `<option value="${LAST_DAY}" ${selected >= LAST_DAY ? "selected" : ""}>Последний день месяца</option>`;
}

/* «каждое 15 число» / «в последний день месяца» */
function dayOfMonthLabel(day) {
  return day >= LAST_DAY ? "в последний день месяца" : `каждое ${day} число`;
}

export { dayOptionsHtml, dayOfMonthLabel, todayMonth, todayISO, categoryIcon, monthLabel, shiftMonth, fmtMoney, escapeHtml };
