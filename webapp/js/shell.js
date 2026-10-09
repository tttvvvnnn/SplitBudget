import { state, isPersonal } from "./state.js";
import { haptic, toast, confirmAction } from "./telegram.js";
import { api } from "./api.js";
import { openSheet, closeSheet } from "./sheet.js";
import { escapeHtml } from "./format.js";
import { memberLabel } from "./members.js";
import { renderExpensesTab, openExpenseModal } from "./tabs/expenses.js";
import { renderBalanceTab } from "./tabs/balance.js";
import { renderStatsTab } from "./tabs/stats.js";
import { renderBudgetsTab, openBudgetModal } from "./tabs/budgets.js";
import { renderRecurringTab, openRecurringModal } from "./tabs/recurring.js";
import { renderIncomeTab, openIncomeModal } from "./tabs/income.js";
import { renderGoalsTab, openGoalModal } from "./tabs/goals.js";

/* Версия/сборка в правом верхнем углу шапки — чтобы после пересборки контейнера на стенде
   было видно на глаз, старый код сейчас открыт или уже новый. /healthz отдаётся без
   Cache-Control-кеширования специально для этого (см. app/main.py). */
async function loadVersionBadge() {
  try {
    const res = await fetch("/healthz", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    state.version = data.version || "dev";
    state.buildInfo = data.build || "";
    renderVersionBadge();
  } catch (e) {
    // не критично — просто не покажем бейдж
  }
}

function renderVersionBadge() {
  const header = document.querySelector(".header");
  if (!header) return;
  let badge = document.getElementById("version-badge");
  if (!badge) {
    badge = document.createElement("div");
    badge.id = "version-badge";
    badge.className = "version-badge";
    header.appendChild(badge);
  }
  badge.textContent = [state.version, state.buildInfo].filter(Boolean).join(" · ");
}

/* ---------------- Каркас: шапка + вкладки ---------------- */

const TABS = [
  { id: "expenses", icon: "💸", label: "Траты" },
  { id: "balance", icon: "⚖️", label: "Баланс" },
  { id: "stats", icon: "📊", label: "Статистика" },
  { id: "budgets", icon: "🎯", label: "Лимиты" },
  { id: "recurring", icon: "📌", label: "Платежи" },
  { id: "income", icon: "💰", label: "Доходы" },
  { id: "goals", icon: "🐷", label: "Цели" },
];

// Только личные вкладки — в семейном чате их нет
const PERSONAL_ONLY_TABS = ["income", "goals"];

/* Переключатель «👤 Мои финансы | 🏠 семейные чаты» под заголовком. */
function spaceSwitcherHtml() {
  const spaces = [
    { id: state.personalChatId, label: "👤 Мои финансы" },
    ...state.familyChats.map((c) => ({ id: c.id, label: `🏠 ${c.title || c.id}` })),
  ].filter((s) => s.id !== null);
  const hidden = state.hiddenChats.length
    ? `<button id="hidden-chats" class="hidden-chip">🙈 Скрытые · ${state.hiddenChats.length}</button>`
    : "";
  if (spaces.length < 2 && !hidden) return "";
  return `
    <div class="space-switcher">
      ${spaces.map((s) => `<button data-space="${s.id}" class="${String(s.id) === String(state.chatId) ? "active" : ""}">${escapeHtml(s.label)}</button>`).join("")}
      ${hidden}
    </div>`;
}

/* «Убрать из списка» — одноразовый чат с друзьями больше не мешает в шапке. Только для
   себя: у остальных участников чат на месте, траты и долги не трогаются. */
async function hideCurrentChat(onSwitchSpace) {
  const chat = state.chat;
  const ok = await confirmAction(
    `Убрать «${chat.title || chat.id}» из списка? Чат пропадёт только у вас, траты и долги останутся. ` +
    "Вернуть можно в «🙈 Скрытые» или открыв приложение из этого чата."
  );
  if (!ok) return;
  try {
    await api(`/chats/${chat.id}/hide`, { method: "POST" });
  } catch (e) {
    toast(e.message);
    return;
  }
  haptic("notification", "success");
  state.familyChats = state.familyChats.filter((c) => String(c.id) !== String(chat.id));
  state.hiddenChats = [...state.hiddenChats, { id: chat.id, title: chat.title, currency: chat.currency, is_personal: false }];
  if (onSwitchSpace) await onSwitchSpace(state.personalChatId);
}

/* Шторка со скрытыми чатами: открыть или вернуть в шапку. */
function openHiddenChats(onSwitchSpace) {
  const overlay = openSheet(`
    <div class="sheet-title">Скрытые чаты</div>
    <div class="hint-text" style="margin: 0 0 12px;">Их не видно в шапке. Траты и долги в них сохранены.</div>
    ${state.hiddenChats.map((c) => `
      <div class="hidden-chat-row">
        <span>🏠 ${escapeHtml(c.title || String(c.id))}</span>
        <button class="btn small secondary" data-unhide="${c.id}">Вернуть</button>
      </div>`).join("")}`);
  overlay.querySelectorAll("[data-unhide]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.unhide;
      btn.disabled = true;
      try {
        await api(`/chats/${id}/unhide`, { method: "POST" });
      } catch (e) {
        toast(e.message);
        btn.disabled = false;
        return;
      }
      const chat = state.hiddenChats.find((c) => String(c.id) === id);
      state.hiddenChats = state.hiddenChats.filter((c) => c !== chat);
      state.familyChats = [...state.familyChats, chat];
      closeSheet(overlay);
      if (onSwitchSpace) await onSwitchSpace(chat.id);
    });
  });
}

function renderShell({ onSwitchSpace } = {}) {
  const app = document.getElementById("app");
  let tabs = TABS;
  if (isPersonal()) tabs = TABS.filter((t) => t.id !== "balance");
  else tabs = TABS.filter((t) => !PERSONAL_ONLY_TABS.includes(t.id));
  let sub;
  if (isPersonal()) sub = "Личное + ваша доля в семейных · видно только вам";
  else sub = `Валюта: ${escapeHtml(state.chat.currency)} · Вы: ${escapeHtml(memberLabel(state.member.id))}`
    + ` · <button class="link-btn inline" id="hide-chat">Убрать из списка</button>`;
  app.innerHTML = `
    <div class="header">
      <h1>${escapeHtml(state.chat.title || "Семейные траты")}</h1>
      <div class="sub">${sub}</div>
      ${spaceSwitcherHtml()}
    </div>
    <div id="content" class="content"></div>
    <button class="fab" id="fab-add" title="Добавить">+</button>
    <div class="tabbar">
      ${tabs.map((t) => `
        <button data-tab="${t.id}" class="${state.tab === t.id ? "active" : ""}">
          <span class="icon">${t.icon}</span><span>${t.label}</span>
        </button>`).join("")}
    </div>`;

  app.querySelectorAll(".space-switcher button[data-space]").forEach((btn) => {
    btn.addEventListener("click", () => {
      haptic("selection");
      if (onSwitchSpace) onSwitchSpace(btn.dataset.space);
    });
  });

  const hideBtn = document.getElementById("hide-chat");
  if (hideBtn) hideBtn.addEventListener("click", () => hideCurrentChat(onSwitchSpace));
  const hiddenBtn = document.getElementById("hidden-chats");
  if (hiddenBtn) hiddenBtn.addEventListener("click", () => { haptic("selection"); openHiddenChats(onSwitchSpace); });

  app.querySelectorAll(".tabbar button").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  updateFab();
  document.getElementById("fab-add").addEventListener("click", () => {
    haptic("impact", "light");
    if (state.tab === "recurring") openRecurringModal(null);
    else if (state.tab === "budgets") openBudgetModal(null);
    else if (state.tab === "income") openIncomeModal(null);
    else if (state.tab === "goals") openGoalModal(null);
    else openExpenseModal(null);
  });
}

/* Переключение вкладки — вынесено отдельно, чтобы можно было вызвать не только из таббара
   внизу, но и, например, тапом по сводке баланса вверху вкладки «Траты» */
async function switchTab(tabId) {
  if (tabId === state.tab) return;
  haptic("selection");
  state.tab = tabId;
  document.querySelectorAll(".tabbar button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tabId));
  updateFab();
  await renderTab();
}

/* «+» не нужен на статистике. В «Моих финансах» «+» на тратах добавляет личную трату. */
function updateFab() {
  const hidden = state.tab === "stats";
  document.getElementById("fab-add").style.display = hidden ? "none" : "flex";
}

async function renderTab() {
  const content = document.getElementById("content");
  content.innerHTML = `<div class="loading">Загрузка…</div>`;
  if (state.tab === "expenses") await renderExpensesTab();
  else if (state.tab === "balance") await renderBalanceTab();
  else if (state.tab === "stats") await renderStatsTab();
  else if (state.tab === "budgets") await renderBudgetsTab();
  else if (state.tab === "recurring") await renderRecurringTab();
  else if (state.tab === "income") await renderIncomeTab();
  else if (state.tab === "goals") await renderGoalsTab();
}

export { loadVersionBadge, renderShell, switchTab, renderTab, PERSONAL_ONLY_TABS };
