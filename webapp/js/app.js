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
     tabs/*.js        — вкладки «Траты», «Баланс», «Статистика», «Повторы» и их формы */

import { tg } from "./telegram.js";
import { state, ALL_SPACE, isAll } from "./state.js";
import { escapeHtml } from "./format.js";
import { api } from "./api.js";
import { loadVersionBadge, renderShell, renderTab } from "./shell.js";

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
  // chat_id (кнопка меню бота) открываем сводку «Все траты», по кнопке «Мои финансы» в
  // личке (?space=personal) — личное пространство.
  try {
    const [personal, chats, tree] = await Promise.all([api("/personal"), api("/my-chats"), api("/categories")]);
    state.categoryTree = tree;
    state.personalChatId = personal.id;
    state.personalCurrency = personal.currency;
    state.familyChats = chats;
  } catch (e) {
    renderError(e.message);
    return;
  }
  if (params.get("space") === "personal") state.chatId = state.personalChatId;
  else if (!state.chatId) state.chatId = ALL_SPACE;
  state.switchSpace = switchSpace;

  await loadMeAndRender();
}

/* Переключение между «Все траты», «Мои финансы» и семейными чатами из шапки. */
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
  if (isAll()) {
    // Сводка не привязана к одному чату: участников и плательщиков тут нет, валюта — личная.
    state.chat = { id: ALL_SPACE, title: "Все траты", currency: state.personalCurrency, is_personal: false };
    state.member = null;
    state.members = [];
    if (state.tab !== "stats") state.tab = "expenses";
    renderShell({ onSwitchSpace: switchSpace });
    loadVersionBadge();
    await renderTab();
    return;
  }
  try {
    const me = await api(`/chats/${state.chatId}/me`);
    state.chat = me.chat;
    state.member = me.member;
    state.members = me.members;
    state.categories = me.categories;
    if (state.chat.is_personal && state.tab === "balance") state.tab = "expenses";
    renderShell({ onSwitchSpace: switchSpace });
    loadVersionBadge(); // не блокирует основной рендер — бейдж в углу подтянется чуть позже
    await renderTab();
  } catch (e) {
    renderError(e.message);
  }
}

init();
