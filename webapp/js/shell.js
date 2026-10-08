import { state } from "./state.js";
import { haptic } from "./telegram.js";
import { escapeHtml } from "./format.js";
import { memberLabel } from "./members.js";
import { renderExpensesTab, openExpenseModal } from "./tabs/expenses.js";
import { renderBalanceTab } from "./tabs/balance.js";
import { renderStatsTab } from "./tabs/stats.js";
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

function renderShell() {
  const app = document.getElementById("app");
  app.innerHTML = `
    <div class="header">
      <h1>${escapeHtml(state.chat.title || "Семейные траты")}</h1>
      <div class="sub">Валюта: ${escapeHtml(state.chat.currency)} · Вы: ${escapeHtml(memberLabel(state.member.id))}</div>
    </div>
    <div id="content" class="content"></div>
    <button class="fab" id="fab-add" title="Добавить">+</button>
    <div class="tabbar">
      ${TABS.map((t) => `
        <button data-tab="${t.id}" class="${state.tab === t.id ? "active" : ""}">
          <span class="icon">${t.icon}</span><span>${t.label}</span>
        </button>`).join("")}
    </div>`;

  app.querySelectorAll(".tabbar button").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

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
  document.getElementById("fab-add").style.display = tabId === "stats" ? "none" : "flex";
  await renderTab();
}

async function renderTab() {
  const content = document.getElementById("content");
  content.innerHTML = `<div class="loading">Загрузка…</div>`;
  if (state.tab === "expenses") await renderExpensesTab();
  else if (state.tab === "balance") await renderBalanceTab();
  else if (state.tab === "stats") await renderStatsTab();
  else if (state.tab === "recurring") await renderRecurringTab();
}

export { loadVersionBadge, renderShell, switchTab, renderTab };
