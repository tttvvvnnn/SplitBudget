/* Семейные траты — mini app. Ванильный JS, без сборки: нативные ES-модули, точка входа —
   этот файл (подключён в index.html как <script type="module">). Что где лежит:
     telegram.js      — объект Telegram WebApp, вибрация, алерты/подтверждения
     state.js         — общее состояние приложения
     format.js        — даты, деньги, escapeHtml, иконки категорий
     api.js           — запросы к /api и загрузка картинок с авторизацией
     members.js       — подписи и аватарки участников
     sheet.js         — шторки (bottom sheet), нативная MainButton, ошибки форм
     member-modals.js — добавление/переименование/список участников
     shell.js         — шапка, таббар, переключение вкладок, бейдж версии
     tabs/*.js        — вкладки «Траты», «Баланс», «Статистика», «Лимиты», «Платежи» и их формы */

import { tg } from "./telegram.js";
import { state } from "./state.js";
import { escapeHtml } from "./format.js";
import { api } from "./api.js";
import { loadVersionBadge, renderShell, renderTab, PERSONAL_ONLY_TABS } from "./shell.js";

/* ---------------- Инициализация ---------------- */

async function init() {
  const params = new URLSearchParams(window.location.search);
  state.chatId = params.get("chat_id");
  // Кнопка в группах открывает приложение через прямую ссылку t.me/бот?startapp=chat_id
  // (Telegram не разрешает web_app-кнопки вне личных чатов) — тогда chat_id приходит не
  // через query-параметр, а через initDataUnsafe.start_param.
  if (!state.chatId && tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param) {
    state.chatId = tg.initDataUnsafe.start_param;
  }
  // Кнопка «Другая сумма» под вопросом бота: startapp=<chat_id>_pay — сразу на вкладку
  // «Лимиты» (платежи месяца), <chat_id>_income — на «Доходы».
  const START_TABS = { _pay: "budgets", _income: "income" };
  for (const [suffix, tab] of Object.entries(START_TABS)) {
    if (state.chatId && state.chatId.endsWith(suffix)) {
      state.chatId = state.chatId.slice(0, -suffix.length);
      state.tab = tab;
    }
  }
  state.initData = tg ? tg.initData : "";

  if (tg && tg.themeParams && tg.themeParams.bg_color) {
    document.body.style.background = tg.themeParams.bg_color;
  }

  if (!state.initData) {
    renderError(
      "Приложение нужно открывать внутри Telegram — из семейного чата (кнопка «Открыть учёт трат»)."
    );
    return;
  }

  // Личное пространство и семейные чаты нужны всегда — для переключателя в шапке. Без
  // chat_id (кнопка меню бота, «Мои финансы» в личке) открываем «Мои финансы».
  try {
    const [personal, chats, hidden, tree] = await Promise.all([
      api("/personal"), api("/my-chats"), api("/my-chats?hidden=true"), api("/categories"),
    ]);
    state.categoryTree = tree;
    state.personalChatId = personal.id;
    state.personalCurrency = personal.currency;
    state.familyChats = chats;
    state.hiddenChats = hidden;
  } catch (e) {
    renderError(e.message);
    return;
  }
  // chat_id=all — старые ссылки на бывшую сводку «Все траты», теперь это «Мои финансы»
  if (params.get("space") === "personal" || !state.chatId || state.chatId === "all") {
    state.chatId = state.personalChatId;
  }
  state.switchSpace = switchSpace;

  await loadMeAndRender();
}

/* Переключение между «Моими финансами» и семейными чатами из шапки. */
async function switchSpace(chatId) {
  if (String(chatId) === String(state.chatId)) return;
  state.chatId = chatId;
  state.filters = { search: "", category: "", payer: "" };
  state.filtersOpen = false;
  state.balances = null;
  await loadMeAndRender();
}

function renderError(message) {
  document.getElementById("app").innerHTML = `
    <div class="content" style="padding-top: 60px;">
      <div class="empty-state">⚠️ ${escapeHtml(message)}</div>
    </div>`;
}

async function loadMeAndRender() {
  try {
    const me = await api(`/chats/${state.chatId}/me`);
    state.chat = me.chat;
    state.member = me.member;
    state.members = me.members;
    state.categories = me.categories;
    await returnHiddenChat();
    if (state.chat.is_personal && state.tab === "balance") state.tab = "expenses";
    if (!state.chat.is_personal && PERSONAL_ONLY_TABS.includes(state.tab)) state.tab = "expenses";
    renderShell({ onSwitchSpace: switchSpace });
    loadVersionBadge(); // не блокирует основной рендер — бейдж в углу подтянется чуть позже
    await renderTab();
  } catch (e) {
    renderError(e.message);
  }
}

/* Открыли скрытый чат по кнопке из самой группы — значит, он снова нужен: возвращаем в шапку. */
async function returnHiddenChat() {
  const hidden = state.hiddenChats.find((c) => String(c.id) === String(state.chat.id));
  if (!hidden) return;
  try {
    await api(`/chats/${hidden.id}/unhide`, { method: "POST" });
    state.hiddenChats = state.hiddenChats.filter((c) => c !== hidden);
    state.familyChats = [...state.familyChats, hidden];
  } catch (e) { /* не критично — чат просто останется скрытым */ }
}

init();
