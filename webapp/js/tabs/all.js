import { state } from "../state.js";
import { api, loadAuthedImage } from "../api.js";
import { haptic } from "../telegram.js";
import { categoryIcon, monthLabel, shiftMonth, fmtMoney, escapeHtml } from "../format.js";
import { openExpenseModal, dayHeaderLabel } from "./expenses.js";
import { categoryBadgeHtml } from "../category-fields.js";

/* ---------------- Вкладка «Траты» в «Моих финансах» ----------------
   Личные траты и траты семейных чатов, в которых у пользователя есть доля, одним списком.
   Переключатель «Моя доля / Всего»: в семейной трате на 3 000 на двоих моя доля — 1 500,
   всего — 3 000 (вторая сумма — мелко под первой). Личную трату тап открывает на
   редактирование прямо здесь, семейную — в её чате. */

let allFilters = { search: "", space: "" };

/* Режим сумм — общий для вкладок «Траты» и «Статистика», запоминается на устройстве. */
function shareMode() {
  if (!state.shareMode) {
    try { state.shareMode = localStorage.getItem("shareMode") || "share"; } catch (e) { state.shareMode = "share"; }
  }
  return state.shareMode;
}

function setShareMode(mode) {
  state.shareMode = mode;
  try { localStorage.setItem("shareMode", mode); } catch (e) { /* не критично */ }
}

/* Переключатель «Моя доля / Всего»; onChange вызывается после смены режима. */
function shareToggleHtml() {
  const mode = shareMode();
  return `
    <div class="share-toggle" id="share-toggle">
      <button data-mode="share" class="${mode === "share" ? "active" : ""}">Моя доля</button>
      <button data-mode="total" class="${mode === "total" ? "active" : ""}">Всего</button>
    </div>`;
}

function bindShareToggle(root, onChange) {
  root.querySelectorAll("#share-toggle button").forEach((b) => {
    b.addEventListener("click", () => {
      if (b.dataset.mode === shareMode()) return;
      haptic("selection");
      setShareMode(b.dataset.mode);
      onChange();
    });
  });
}

async function renderAllExpensesTab() {
  const content = document.getElementById("content");
  let items;
  try {
    // Личные траты в полном виде нужны для редактирования прямо из этого списка
    const [all, personal] = await Promise.all([
      api(`/all/expenses?month=${state.month}`),
      api(`/chats/${state.personalChatId}/expenses?month=${state.month}`),
    ]);
    items = all;
    state.expenses = personal;
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
    ${shareToggleHtml()}
    <div class="total-line" id="total-line"></div>
    <div class="field-row" style="margin-bottom: 10px;">
      <div class="field">
        <input type="text" id="all-search" placeholder="Поиск по названию…" value="${escapeHtml(allFilters.search)}">
      </div>
      <div class="field">
        <select id="all-space">
          <option value="">Все траты</option>
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
  bindShareToggle(content, () => {
    content.querySelectorAll("#share-toggle button").forEach((x) => x.classList.toggle("active", x.dataset.mode === shareMode()));
    renderList(items);
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
  return e.is_personal ? "👤 Личные" : `🏠 ${e.chat_title || e.chat_id}`;
}

function shownAmount(e) {
  return shareMode() === "total" ? Number(e.amount) : Number(e.my_share);
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
    fmtMoney(filtered.reduce((s, e) => s + shownAmount(e), 0));

  if (filtered.length === 0) {
    list.innerHTML = `<div class="empty-state">${items.length ? "Ничего не найдено." : "Трат за этот месяц пока нет.<br>Нажмите «+», чтобы добавить первую."}</div>`;
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
    el.addEventListener("click", () => openExpense(Number(el.dataset.chat), Number(el.dataset.id)));
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
  const sub = !shared ? "" : shareMode() === "total"
    ? `<div class="amount-sub">ваша доля ${fmtMoney(e.my_share)}</div>`
    : `<div class="amount-sub">из ${fmtMoney(e.amount)}</div>`;
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
        ${fmtMoney(shownAmount(e))}
        ${sub}
      </div>
    </div>`;
}

/* Личную трату открываем здесь же, семейную — в её чате (тот же месяц). */
async function openExpense(chatId, expenseId) {
  haptic("selection");
  if (String(chatId) === String(state.personalChatId)) {
    const expense = state.expenses.find((x) => x.id === expenseId);
    if (expense) openExpenseModal(expense);
    return;
  }
  if (!state.switchSpace) return;
  await state.switchSpace(chatId);
  const expense = state.expenses.find((x) => x.id === expenseId);
  if (expense) openExpenseModal(expense);
}

export { renderAllExpensesTab, shareMode, shareToggleHtml, bindShareToggle };
