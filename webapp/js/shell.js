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
import { renderPlanTab, openNewPlanItem } from "./tabs/plan.js";

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

/* Иконки таббара — линейные SVG (24×24, stroke = currentColor), а не эмодзи: эмодзи
   рисуются шрифтом системы, на каждой платформе выглядят по-разному и не красятся в цвет
   темы, а SVG берут цвет текста кнопки — активная вкладка подсвечивается цветом темы. */
const TAB_ICONS = {
  expenses: '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"/><path d="M12 17.5v-11"/>',
  balance: '<path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="M7 21h10"/><path d="M12 3v18"/><path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/>',
  stats: '<path d="M21.21 15.89A10 10 0 1 1 8 2.83"/><path d="M22 12A10 10 0 0 0 12 2v10z"/>',
  budgets: '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
  recurring: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/>',
  income: '<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1"/><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>',
  goals: '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><path d="M4 22v-7"/>',
  plan: '<path d="M21 12c.552 0 1.005-.449.95-.998a10 10 0 0 0-8.953-8.951c-.55-.055-.998.398-.998.95v8a1 1 0 0 0 1 1z"/><path d="M21.21 15.89A10 10 0 1 1 8 2.83"/>',
};

function tabIconSvg(id) {
  return `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${TAB_ICONS[id]}</svg>`;
}

const TABS = [
  { id: "expenses", label: "Траты" },
  { id: "balance", label: "Баланс" },
  { id: "stats", label: "Статистика" },
  { id: "budgets", label: "Лимиты" },
  { id: "plan", label: "План" },
  { id: "recurring", label: "Платежи" },
  { id: "income", label: "Доходы" },
  { id: "goals", label: "Цели" },
];

// Только личные вкладки — в семейном чате их нет
const PERSONAL_ONLY_TABS = ["income", "goals", "plan"];
// В «Моих финансах» вместо лимитов — план распределения дохода (там же платежи месяца)
const FAMILY_ONLY_TABS = ["balance", "budgets"];

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
async function hideCurrentChat(onSwitchSpace, question) {
  const chat = state.chat;
  const ok = await confirmAction(
    (question || `Убрать «${chat.title || chat.id}» из списка?`) +
    " Чат пропадёт только у вас, траты и долги останутся. " +
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
  if (isPersonal()) tabs = TABS.filter((t) => !FAMILY_ONLY_TABS.includes(t.id));
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
    <button class="fab" id="fab-add" title="Добавить" aria-label="Добавить">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
    </button>
    <nav class="tabbar">
      ${tabs.map((t) => `
        <button data-tab="${t.id}" class="${state.tab === t.id ? "active" : ""}"${state.tab === t.id ? ' aria-current="page"' : ""}>
          ${tabIconSvg(t.id)}<span>${t.label}</span>
        </button>`).join("")}
    </nav>`;

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
    else if (state.tab === "plan") openNewPlanItem();
    else openExpenseModal(null);
  });
}

/* Переключение вкладки — вынесено отдельно, чтобы можно было вызвать не только из таббара
   внизу, но и, например, тапом по сводке баланса вверху вкладки «Траты» */
async function switchTab(tabId) {
  if (tabId === state.tab) return;
  haptic("selection");
  state.tab = tabId;
  document.querySelectorAll(".tabbar button").forEach((b) => {
    const active = b.dataset.tab === tabId;
    b.classList.toggle("active", active);
    if (active) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
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
  else if (state.tab === "plan") await renderPlanTab();
}

export { loadVersionBadge, renderShell, switchTab, renderTab, hideCurrentChat, PERSONAL_ONLY_TABS, FAMILY_ONLY_TABS };
