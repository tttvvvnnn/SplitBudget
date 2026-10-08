import { state, ALL_SPACE, isAll, isPersonal } from "./state.js";
import { haptic } from "./telegram.js";
import { escapeHtml } from "./format.js";
import { memberLabel } from "./members.js";
import { renderExpensesTab, openExpenseModal } from "./tabs/expenses.js";
import { renderBalanceTab } from "./tabs/balance.js";
import { renderStatsTab } from "./tabs/stats.js";
import { renderAllExpensesTab } from "./tabs/all.js";
import { renderRecurringTab, openRecurringModal } from "./tabs/recurring.js";

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
  { id: "recurring", icon: "🔁", label: "Повторы" },
];

/* Переключатель «📋 Все траты | 👤 Мои финансы | 🏠 семейные чаты» под заголовком. */
function spaceSwitcherHtml() {
  const spaces = [
    { id: ALL_SPACE, label: "📋 Все траты" },
    { id: state.personalChatId, label: "👤 Мои финансы" },
    ...state.familyChats.map((c) => ({ id: c.id, label: `🏠 ${c.title || c.id}` })),
  ].filter((s) => s.id !== null);
  if (spaces.length < 2) return "";
  return `
    <div class="space-switcher">
      ${spaces.map((s) => `<button data-space="${s.id}" class="${String(s.id) === String(state.chatId) ? "active" : ""}">${escapeHtml(s.label)}</button>`).join("")}
    </div>`;
}

function renderShell({ onSwitchSpace } = {}) {
  const app = document.getElementById("app");
  let tabs = TABS;
  if (isAll()) tabs = TABS.filter((t) => t.id === "expenses" || t.id === "stats");
  else if (isPersonal()) tabs = TABS.filter((t) => t.id !== "balance");
  let sub;
  if (isAll()) sub = "Личные + ваша доля в семейных · видно только вам";
  else if (isPersonal()) sub = `Видно только вам · Валюта: ${escapeHtml(state.chat.currency)}`;
  else sub = `Валюта: ${escapeHtml(state.chat.currency)} · Вы: ${escapeHtml(memberLabel(state.member.id))}`;
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

  app.querySelectorAll(".space-switcher button").forEach((btn) => {
    btn.addEventListener("click", () => {
      haptic("selection");
      if (onSwitchSpace) onSwitchSpace(btn.dataset.space);
    });
  });

  app.querySelectorAll(".tabbar button").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  updateFab();
  document.getElementById("fab-add").addEventListener("click", () => {
    haptic("impact", "light");
    if (state.tab === "recurring") openRecurringModal(null);
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

/* «+» не нужен на статистике и в сводке «Все траты» (непонятно, в какой чат добавлять —
   траты добавляются в своём пространстве). */
function updateFab() {
  document.getElementById("fab-add").style.display = state.tab === "stats" || isAll() ? "none" : "flex";
}

async function renderTab() {
  const content = document.getElementById("content");
  content.innerHTML = `<div class="loading">Загрузка…</div>`;
  if (state.tab === "expenses" && isAll()) await renderAllExpensesTab();
  else if (state.tab === "expenses") await renderExpensesTab();
  else if (state.tab === "balance") await renderBalanceTab();
  else if (state.tab === "stats") await renderStatsTab();
  else if (state.tab === "recurring") await renderRecurringTab();
}

export { loadVersionBadge, renderShell, switchTab, renderTab };
