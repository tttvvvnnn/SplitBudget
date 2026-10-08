import { state } from "../state.js";
import { api, loadAuthedImage } from "../api.js";
import { haptic } from "../telegram.js";
import { categoryIcon, monthLabel, shiftMonth, fmtMoney, escapeHtml } from "../format.js";
import { openExpenseModal, dayHeaderLabel } from "./expenses.js";
import { categoryBadgeHtml } from "../category-fields.js";

/* ---------------- Вкладка «Траты» в сводке «Все траты» ----------------
   Личные траты и траты семейных чатов, в которых у пользователя есть доля, одним списком.
   Сумма — доля пользователя (в семейной трате на 3 000 на двоих это 1 500), полная сумма —
   мелко под ней. Тап по трате открывает её в своём пространстве. */

let allFilters = { search: "", space: "" };

async function renderAllExpensesTab() {
  const content = document.getElementById("content");
  let items;
  try {
    items = await api(`/all/expenses?month=${state.month}`);
  } catch (e) {
    content.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    return;
  }

  const spaces = [];
  for (const e of items) {
    if (!spaces.some((s) => s.id === e.chat_id)) spaces.push({ id: e.chat_id, label: spaceLabel(e) });
  }

  content.innerHTML = `
    <div class="month-picker">
      <button data-dir="-1">‹</button>
      <div class="label">${monthLabel(state.month)}</div>
      <button data-dir="1">›</button>
    </div>
    <div class="total-line" id="total-line"></div>
    <div class="field-row" style="margin-bottom: 10px;">
      <div class="field">
        <input type="text" id="all-search" placeholder="Поиск по названию…" value="${escapeHtml(allFilters.search)}">
      </div>
      <div class="field">
        <select id="all-space">
          <option value="">Все пространства</option>
          ${spaces.map((s) => `<option value="${s.id}" ${String(allFilters.space) === String(s.id) ? "selected" : ""}>${escapeHtml(s.label)}</option>`).join("")}
        </select>
      </div>
    </div>
    <div id="expense-list"></div>`;

  content.querySelectorAll(".month-picker button").forEach((b) => {
    b.addEventListener("click", async () => {
      haptic("selection");
      state.month = shiftMonth(state.month, Number(b.dataset.dir));
      await renderAllExpensesTab();
    });
  });
  document.getElementById("all-search").addEventListener("input", (ev) => {
    allFilters.search = ev.target.value;
    renderList(items);
  });
  document.getElementById("all-space").addEventListener("change", (ev) => {
    haptic("selection");
    allFilters.space = ev.target.value;
    renderList(items);
  });

  renderList(items);
}

function spaceLabel(e) {
  return e.is_personal ? "👤 Личное" : `🏠 ${e.chat_title || e.chat_id}`;
}

function renderList(items) {
  const list = document.getElementById("expense-list");
  const q = allFilters.search.trim().toLowerCase();
  const filtered = items.filter((e) => {
    if (q && !e.title.toLowerCase().includes(q)) return false;
    if (allFilters.space && String(e.chat_id) !== String(allFilters.space)) return false;
    return true;
  });

  document.getElementById("total-line").textContent =
    fmtMoney(filtered.reduce((s, e) => s + Number(e.my_share), 0));

  if (filtered.length === 0) {
    list.innerHTML = `<div class="empty-state">${items.length ? "Ничего не найдено." : "Трат за этот месяц пока нет."}</div>`;
    return;
  }

  let html = "";
  let lastDate = null;
  for (const e of filtered) {
    if (e.expense_date !== lastDate) {
      html += `<div class="day-header">${dayHeaderLabel(e.expense_date)}</div>`;
      lastDate = e.expense_date;
    }
    html += cardHtml(e);
  }
  list.innerHTML = html;

  list.querySelectorAll(".expense-card").forEach((el) => {
    el.addEventListener("click", () => openInSpace(Number(el.dataset.chat), Number(el.dataset.id)));
  });
  list.querySelectorAll(".expense-thumb[data-photo]").forEach((img) => {
    loadAuthedImage(img, `/chats/${img.dataset.chat}/${img.dataset.photo}`);
  });
}

function cardHtml(e) {
  const thumb = e.photo_url
    ? `<img class="expense-thumb" data-chat="${e.chat_id}" data-photo="${escapeHtml(e.photo_url)}">`
    : `<div class="expense-thumb placeholder">${categoryIcon(e.category)}</div>`;
  const shared = Number(e.my_share) !== Number(e.amount);
  const payer = e.is_personal ? "" : ` · ${e.i_paid ? "платили вы" : `платил(а) ${escapeHtml(e.payer_name)}`}`;
  return `
    <div class="card expense-card" data-id="${e.id}" data-chat="${e.chat_id}">
      ${thumb}
      <div class="expense-main">
        <div class="expense-title">${escapeHtml(e.title)}</div>
        <div class="expense-meta">
          ${categoryBadgeHtml(e.category, e.subcategory)}
          <span>${escapeHtml(spaceLabel(e))}${payer}</span>
          ${e.is_recurring ? '<span title="Повторяющаяся">🔁</span>' : ""}
        </div>
      </div>
      <div class="expense-amount">
        ${fmtMoney(e.my_share)}
        ${shared ? `<div class="amount-sub">из ${fmtMoney(e.amount)}</div>` : ""}
      </div>
    </div>`;
}

/* Переход к трате в её пространстве: переключаемся туда (тот же месяц) и открываем её. */
async function openInSpace(chatId, expenseId) {
  haptic("selection");
  if (!state.switchSpace) return;
  await state.switchSpace(chatId);
  const expense = state.expenses.find((x) => x.id === expenseId);
  if (expense) openExpenseModal(expense);
}

export { renderAllExpensesTab };
