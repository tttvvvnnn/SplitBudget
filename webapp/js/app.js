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
import { state } from "./state.js";
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

  if (!state.chatId) {
    try {
      const chats = await api("/my-chats");
      if (chats.length === 1) {
        state.chatId = chats[0].id;
      } else if (chats.length === 0) {
        renderError(
          "Вы пока не в одном семейном чате с ботом. Откройте приложение из кнопки в групповом чате."
        );
        return;
      } else {
        renderChatPicker(chats);
        return;
      }
    } catch (e) {
      renderError(e.message);
      return;
    }
  }

  await loadMeAndRender();
}

function renderChatPicker(chats) {
  const app = document.getElementById("app");
  app.innerHTML = `
    <div class="header"><h1>Выберите чат</h1><div class="sub">В нескольких чатах есть учёт трат</div></div>
    <div class="content">
      ${chats.map((c) => `<div class="card expense-card" data-id="${c.id}"><div class="expense-main"><div class="expense-title">${escapeHtml(c.title || String(c.id))}</div></div></div>`).join("")}
    </div>`;
  app.querySelectorAll(".card").forEach((el) => {
    el.addEventListener("click", () => {
      state.chatId = el.dataset.id;
      loadMeAndRender();
    });
  });
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
    renderShell();
    loadVersionBadge(); // не блокирует основной рендер — бейдж в углу подтянется чуть позже
    await renderTab();
  } catch (e) {
    renderError(e.message);
  }
}

init();
