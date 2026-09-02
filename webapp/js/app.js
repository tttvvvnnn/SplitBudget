/* Семейные траты — mini app. Ванильный JS, без сборки. */

const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
if (tg) {
  tg.ready();
  tg.expand();
}

const API_BASE = "/api";

const state = {
  chatId: null,
  initData: "",
  chat: null,
  member: null,
  members: [],
  categories: [],
  tab: "expenses",
  month: todayMonth(),
  expenses: [],
  expenseCategory: "",
  filters: { search: "", category: "", payer: "" },
  filtersOpen: false,
  balances: null,
  settlements: [],
  recurring: [],
  editingExpense: null,
  editingRecurring: null,
};

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

/* Иконки категорий — только для отображения, на сервер и в БД не уходят (категория
   как была строкой, так и осталась). Список категорий настраивается в
   app/shared/config.py — если добавите новую, впишите и сюда, иначе будет 🏷️. */
const CATEGORY_ICONS = {
  "Еда": "🍔",
  "Транспорт": "🚗",
  "ЖКХ": "🏠",
  "Развлечения": "🎬",
  "Здоровье": "💊",
  "Одежда": "👕",
  "Подписки": "📱",
  "Другое": "📦",
};

function categoryIcon(category) {
  return CATEGORY_ICONS[category] || "🏷️";
}

/* Аватарки участников — настоящее фото профиля Telegram, если бот успел его синхронизировать
   (member.avatar_url), иначе кружок с первой буквой имени. Цвет кружка стабильно зависит от
   id участника — просто чтобы разных людей было легче отличить друг от друга на глаз. */
const AVATAR_PALETTE = ["#2481cc", "#34c759", "#ff9500", "#ff3b30", "#af52de", "#5ac8fa", "#ffcc00", "#8e8e93"];

function avatarHtml(member) {
  if (member && member.avatar_url) {
    return `<img class="avatar" data-avatar="${escapeHtml(member.avatar_url)}">`;
  }
  const name = (member && member.full_name) || "?";
  const initial = (name.trim().charAt(0) || "?").toUpperCase();
  const color = AVATAR_PALETTE[Math.abs((member && member.id) || 0) % AVATAR_PALETTE.length];
  return `<div class="avatar-fallback" style="background:${color}">${escapeHtml(initial)}</div>`;
}

function loadAvatarsIn(container) {
  container.querySelectorAll(".avatar[data-avatar]").forEach((img) => {
    loadAuthedImage(img, `/chats/${state.chatId}/${img.dataset.avatar}`);
  });
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

function memberLabel(memberId) {
  const m = state.members.find((x) => x.id === memberId);
  if (!m) return "—";
  return m.username ? `@${m.username}` : m.full_name;
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

async function api(path, { method = "GET", body, isForm = false } = {}) {
  const headers = { "X-Telegram-Init-Data": state.initData };
  let fetchBody = body;
  if (body && !isForm) {
    headers["Content-Type"] = "application/json";
    fetchBody = JSON.stringify(body);
  }
  const res = await fetch(`${API_BASE}${path}`, { method, headers, body: fetchBody });
  if (!res.ok) {
    let detail = "Ошибка запроса";
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res;
}

async function loadAuthedImage(imgEl, path) {
  try {
    const res = await fetch(`${API_BASE}${path}`, { headers: { "X-Telegram-Init-Data": state.initData } });
    if (!res.ok) throw new Error("no photo");
    const blob = await res.blob();
    imgEl.src = URL.createObjectURL(blob);
  } catch (e) {
    imgEl.style.display = "none";
  }
}

function toast(message) {
  haptic("notification", "error"); // toast() в этом коде зовётся только из catch-веток
  if (tg && tg.showAlert) {
    tg.showAlert(message);
  } else {
    alert(message);
  }
}

function confirmAction(message) {
  return new Promise((resolve) => {
    if (tg && tg.showConfirm) {
      tg.showConfirm(message, (ok) => resolve(ok));
    } else {
      resolve(confirm(message));
    }
  });
}

/* Тактильный отклик через Telegram WebApp API — короткая вибрация на действие, без неё
   интерфейс ощущается «веб-страницей», а не частью Telegram. На старых клиентах, где
   HapticFeedback не поддерживается, тихо ничего не делает. */
function haptic(kind, style) {
  if (!tg || !tg.HapticFeedback) return;
  try {
    if (kind === "impact") tg.HapticFeedback.impactOccurred(style || "light");
    else if (kind === "notification") tg.HapticFeedback.notificationOccurred(style || "success");
    else if (kind === "selection") tg.HapticFeedback.selectionChanged();
  } catch (e) { /* не критично */ }
}

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
    await renderTab();
  } catch (e) {
    renderError(e.message);
  }
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
    btn.addEventListener("click", async () => {
      if (btn.dataset.tab === state.tab) return;
      haptic("selection");
      state.tab = btn.dataset.tab;
      app.querySelectorAll(".tabbar button").forEach((b) => b.classList.toggle("active", b === btn));
      document.getElementById("fab-add").style.display = state.tab === "stats" ? "none" : "flex";
      await renderTab();
    });
  });

  document.getElementById("fab-add").addEventListener("click", () => {
    haptic("impact", "light");
    if (state.tab === "recurring") openRecurringModal(null);
    else openExpenseModal(null);
  });
}

async function renderTab() {
  const content = document.getElementById("content");
  content.innerHTML = `<div class="loading">Загрузка…</div>`;
  if (state.tab === "expenses") await renderExpensesTab();
  else if (state.tab === "balance") await renderBalanceTab();
  else if (state.tab === "stats") await renderStatsTab();
  else if (state.tab === "recurring") await renderRecurringTab();
}

/* ---------------- Вкладка «Траты» ---------------- */

async function renderExpensesTab() {
  const content = document.getElementById("content");
  try {
    state.expenses = await api(`/chats/${state.chatId}/expenses?month=${state.month}`);
  } catch (e) {
    content.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    return;
  }

  const hasFilters = !!(state.filters.search || state.filters.category || state.filters.payer);

  content.innerHTML = `
    <div class="month-picker">
      <button data-dir="-1">‹</button>
      <div class="label">${monthLabel(state.month)}</div>
      <button data-dir="1">›</button>
    </div>
    <div class="total-line" id="total-line"></div>
    <div class="top-actions">
      <button class="btn small secondary" id="filter-toggle-btn">🔍 Фильтр${hasFilters ? " •" : ""}</button>
      <button class="btn small secondary" id="export-btn">⬇️ Excel</button>
    </div>
    <div id="filter-panel" style="display:${state.filtersOpen ? "block" : "none"}; margin: 0 0 10px;">
      <div class="field">
        <input type="text" id="filter-search" placeholder="Поиск по названию…" value="${escapeHtml(state.filters.search)}">
      </div>
      <div class="field-row">
        <div class="field">
          <select id="filter-category">
            <option value="">Все категории</option>
            ${state.categories.map((c) => `<option value="${escapeHtml(c)}" ${state.filters.category === c ? "selected" : ""}>${categoryIcon(c)} ${escapeHtml(c)}</option>`).join("")}
          </select>
        </div>
        <div class="field">
          <select id="filter-payer">
            <option value="">Все участники</option>
            ${state.members.map((m) => `<option value="${m.id}" ${String(state.filters.payer) === String(m.id) ? "selected" : ""}>${escapeHtml(m.full_name)}</option>`).join("")}
          </select>
        </div>
      </div>
    </div>
    <div id="expense-list"></div>`;

  content.querySelectorAll(".month-picker button").forEach((b) => {
    b.addEventListener("click", async () => {
      haptic("selection");
      state.month = shiftMonth(state.month, Number(b.dataset.dir));
      await renderExpensesTab();
    });
  });
  document.getElementById("export-btn").addEventListener("click", exportXlsx);

  const filterPanel = document.getElementById("filter-panel");
  const filterToggleBtn = document.getElementById("filter-toggle-btn");
  filterToggleBtn.addEventListener("click", () => {
    haptic("selection");
    state.filtersOpen = !state.filtersOpen;
    filterPanel.style.display = state.filtersOpen ? "block" : "none";
  });

  function updateFilterUI() {
    const active = !!(state.filters.search || state.filters.category || state.filters.payer);
    filterToggleBtn.textContent = `🔍 Фильтр${active ? " •" : ""}`;
    let resetBtn = document.getElementById("filter-reset-btn");
    if (active && !resetBtn) {
      resetBtn = document.createElement("button");
      resetBtn.type = "button";
      resetBtn.className = "btn small secondary";
      resetBtn.id = "filter-reset-btn";
      resetBtn.style.marginTop = "8px";
      resetBtn.textContent = "✕ Сбросить фильтр";
      resetBtn.addEventListener("click", () => {
        haptic("selection");
        state.filters = { search: "", category: "", payer: "" };
        document.getElementById("filter-search").value = "";
        document.getElementById("filter-category").value = "";
        document.getElementById("filter-payer").value = "";
        updateFilterUI();
        renderExpenseList();
      });
      filterPanel.appendChild(resetBtn);
    } else if (!active && resetBtn) {
      resetBtn.remove();
    }
  }

  document.getElementById("filter-search").addEventListener("input", (ev) => {
    state.filters.search = ev.target.value;
    updateFilterUI();
    renderExpenseList();
  });
  document.getElementById("filter-category").addEventListener("change", (ev) => {
    haptic("selection");
    state.filters.category = ev.target.value;
    updateFilterUI();
    renderExpenseList();
  });
  document.getElementById("filter-payer").addEventListener("change", (ev) => {
    haptic("selection");
    state.filters.payer = ev.target.value;
    updateFilterUI();
    renderExpenseList();
  });

  updateFilterUI();
  renderExpenseList();
}

function renderExpenseList() {
  const list = document.getElementById("expense-list");
  const totalLine = document.getElementById("total-line");
  if (!list) return;

  const q = state.filters.search.trim().toLowerCase();
  const { category, payer } = state.filters;
  const filtered = state.expenses.filter((e) => {
    if (q && !e.title.toLowerCase().includes(q)) return false;
    if (category && e.category !== category) return false;
    if (payer && String(e.payer_member_id) !== String(payer)) return false;
    return true;
  });

  const total = filtered.reduce((s, e) => s + Number(e.amount), 0);
  totalLine.textContent = fmtMoney(total);

  const hasAnyFilter = !!(q || category || payer);
  list.innerHTML = filtered.length === 0
    ? `<div class="empty-state">${hasAnyFilter ? "Ничего не найдено." : "Трат за этот месяц пока нет.<br>Нажмите «+», чтобы добавить первую."}</div>`
    : filtered.map(expenseCardHtml).join("");

  list.querySelectorAll(".expense-card").forEach((el) => {
    el.addEventListener("click", () => {
      const expense = state.expenses.find((e) => e.id === Number(el.dataset.id));
      openExpenseModal(expense);
    });
  });
  list.querySelectorAll(".expense-thumb[data-photo]").forEach((img) => {
    loadAuthedImage(img, `/chats/${state.chatId}/${img.dataset.photo}`);
  });
}

function expenseCardHtml(e) {
  const thumb = e.photo_url
    ? `<img class="expense-thumb" data-photo="${escapeHtml(e.photo_url)}">`
    : `<div class="expense-thumb placeholder">${categoryIcon(e.category)}</div>`;
  return `
    <div class="card expense-card" data-id="${e.id}">
      ${thumb}
      <div class="expense-main">
        <div class="expense-title">${escapeHtml(e.title)}</div>
        <div class="expense-meta">
          <span class="badge">${categoryIcon(e.category)} ${escapeHtml(e.category)}</span>
          ${new Date(e.expense_date).toLocaleDateString("ru-RU")} · ${escapeHtml(memberLabel(e.payer_member_id))}
          ${e.is_recurring ? " · 🔁" : ""}
        </div>
      </div>
      <div class="expense-amount">${fmtMoney(e.amount)}</div>
    </div>`;
}

async function exportXlsx() {
  try {
    const res = await api(`/chats/${state.chatId}/export?month=${state.month}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `expenses_${state.month}.xlsx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (e) {
    toast(e.message);
  }
}

/* ---------------- Модалка добавления/редактирования траты ---------------- */

function openSheet(innerHtml) {
  // на всякий случай сбрасываем нативные кнопки от предыдущей шторки, если та не закрылась
  // штатно (не должно случаться, но так спокойнее)
  if (tg && tg.MainButton) tg.MainButton.hide();
  if (tg && tg.BackButton) tg.BackButton.hide();

  const overlay = document.createElement("div");
  overlay.className = "sheet-overlay";
  overlay.innerHTML = `<div class="sheet"><div class="sheet-handle"></div>${innerHtml}</div>`;
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) closeSheet(overlay);
  });
  document.body.appendChild(overlay);

  // Системная кнопка «назад» Telegram закрывает текущую шторку так же, как тап по фону
  if (tg && tg.BackButton) {
    const backHandler = () => closeSheet(overlay);
    overlay._backHandler = backHandler;
    tg.BackButton.onClick(backHandler);
    tg.BackButton.show();
  }
  return overlay;
}

function closeSheet(overlay) {
  if (tg && tg.MainButton) {
    if (overlay._mainHandler) tg.MainButton.offClick(overlay._mainHandler);
    tg.MainButton.hide();
  }
  if (tg && tg.BackButton) {
    if (overlay._backHandler) tg.BackButton.offClick(overlay._backHandler);
    tg.BackButton.hide();
  }
  haptic("impact", "light");
  overlay.remove();
}

/* Подменяет самодельную кнопку submit/save внутри шторки на нативную нижнюю кнопку
   Telegram (MainButton) — просто прячет исходную кнопку и проксирует тап на неё же, чтобы
   вся логика клика (валидация, запрос к API, обработка ошибок) осталась в одном месте.
   Если MainButton недоступен (старый клиент Telegram или тестирование вне Telegram) —
   исходная кнопка остаётся видимой и рабочей, как раньше. */
function useMainButtonFor(overlay, btn, text) {
  if (!tg || !tg.MainButton) return;
  btn.style.display = "none";
  tg.MainButton.setText(text);
  tg.MainButton.show();
  tg.MainButton.enable();
  const handler = () => btn.click();
  overlay._mainHandler = handler;
  tg.MainButton.onClick(handler);
}

function openExpenseModal(existing) {
  state.editingExpense = existing;
  const isEdit = !!existing;
  const selectedIds = new Set(existing ? existing.shares.map((s) => s.member_id) : state.members.map((m) => m.id));
  let splitType = existing ? existing.split_type : "equal";
  const customAmounts = {};
  if (existing) {
    existing.shares.forEach((s) => { customAmounts[s.member_id] = Number(s.amount); });
  }

  const overlay = openSheet(`
    <div class="sheet-title">${isEdit ? "Редактировать трату" : "Новая трата"}</div>
    <div class="title-photo-row">
      <div class="field">
        <label>Название</label>
        <input type="text" id="f-title" value="${existing ? escapeHtml(existing.title) : ""}" placeholder="Продукты, аренда, такси…">
      </div>
      <label class="photo-picker" id="f-photo-picker" title="Фото чека (необязательно)">
        <span id="f-photo-icon">📷</span>
        <img id="f-photo-preview" style="display:none;">
        <input type="file" id="f-photo" accept="image/*" capture="environment">
      </label>
    </div>
    <div class="field-row">
      <div class="field">
        <label>Кто оплатил</label>
        <select id="f-payer">
          ${state.members.map((m) => `<option value="${m.id}" ${(existing ? existing.payer_member_id : state.member.id) === m.id ? "selected" : ""}>${escapeHtml(m.full_name)}</option>`).join("")}
        </select>
      </div>
      <div class="field">
        <label>Сумма (${escapeHtml(state.chat.currency)})</label>
        <input type="number" id="f-amount" min="0" step="0.01" value="${existing ? existing.amount : ""}">
      </div>
    </div>
    <div class="field-row">
      <div class="field">
        <label>Категория</label>
        <select id="f-category">
          ${state.categories.map((c) => `<option value="${escapeHtml(c)}" ${existing && existing.category === c ? "selected" : ""}>${categoryIcon(c)} ${escapeHtml(c)}</option>`).join("")}
        </select>
      </div>
      <div class="field">
        <label>Дата</label>
        <input type="date" id="f-date" value="${existing ? existing.expense_date : todayISO()}">
      </div>
    </div>
    <div class="field">
      <label>Как делить</label>
      <div class="split-toggle">
        <div data-v="equal" class="${splitType === "equal" ? "active" : ""}">Поровну между выбранными</div>
        <div data-v="custom" class="${splitType === "custom" ? "active" : ""}">Вручную по каждому</div>
      </div>
    </div>
    <div class="field" id="participants-block"></div>
    <div class="error-text" id="f-error" style="display:none;"></div>
    <button class="btn" id="f-submit">${isEdit ? "Сохранить" : "Добавить трату"}</button>
    ${isEdit ? '<button class="btn danger" id="f-delete" style="margin-top:10px;">Удалить трату</button>' : ""}
  `);

  const photoInput = overlay.querySelector("#f-photo");
  const photoPreview = overlay.querySelector("#f-photo-preview");
  const photoIcon = overlay.querySelector("#f-photo-icon");
  function showPhotoPreview() {
    photoPreview.style.display = "block";
    photoIcon.style.display = "none";
  }
  if (existing && existing.photo_url) {
    showPhotoPreview();
    loadAuthedImage(photoPreview, `/chats/${state.chatId}/${existing.photo_url}`);
  }
  photoInput.addEventListener("change", () => {
    const file = photoInput.files[0];
    if (file) {
      photoPreview.src = URL.createObjectURL(file);
      showPhotoPreview();
    }
  });

  function currentAmount() {
    return Number(overlay.querySelector("#f-amount").value || 0);
  }

  function renderParticipants() {
    const block = overlay.querySelector("#participants-block");
    if (splitType === "equal") {
      block.innerHTML = `
        <label>Участники (делим поровну)</label>
        <div class="chip-row">
          ${state.members.map((m) => `<div class="chip ${selectedIds.has(m.id) ? "selected" : ""}" data-id="${m.id}">${avatarHtml(m)}${escapeHtml(m.full_name)}</div>`).join("")}
        </div>`;
      loadAvatarsIn(block);
      block.querySelectorAll(".chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          haptic("selection");
          const id = Number(chip.dataset.id);
          if (selectedIds.has(id)) selectedIds.delete(id); else selectedIds.add(id);
          chip.classList.toggle("selected");
        });
      });
    } else {
      const amount = currentAmount();
      const sum = state.members.reduce((s, m) => s + (customAmounts[m.id] || 0), 0);
      block.innerHTML = `
        <label>Сумма на каждого</label>
        ${state.members.map((m) => `
          <div class="custom-share-row">
            <div class="name">${avatarHtml(m)}${escapeHtml(m.full_name)}</div>
            <input type="number" min="0" step="0.01" data-id="${m.id}" class="custom-amount" value="${customAmounts[m.id] || ""}">
          </div>`).join("")}
        <div class="hint-text" id="sum-hint">Указано: ${sum.toFixed(2)} из ${amount.toFixed(2)} ${state.chat.currency}</div>`;
      loadAvatarsIn(block);
      block.querySelectorAll(".custom-amount").forEach((inp) => {
        inp.addEventListener("input", () => {
          customAmounts[Number(inp.dataset.id)] = Number(inp.value || 0);
          const s = state.members.reduce((acc, m) => acc + (customAmounts[m.id] || 0), 0);
          block.querySelector("#sum-hint").textContent = `Указано: ${s.toFixed(2)} из ${currentAmount().toFixed(2)} ${state.chat.currency}`;
        });
      });
    }
  }
  renderParticipants();

  overlay.querySelector("#f-amount").addEventListener("input", () => {
    if (splitType === "custom") renderParticipants();
  });

  overlay.querySelectorAll(".split-toggle div").forEach((el) => {
    el.addEventListener("click", () => {
      haptic("selection");
      splitType = el.dataset.v;
      overlay.querySelectorAll(".split-toggle div").forEach((x) => x.classList.toggle("active", x === el));
      renderParticipants();
    });
  });

  overlay.querySelector("#f-submit").addEventListener("click", async () => {
    const errorEl = overlay.querySelector("#f-error");
    errorEl.style.display = "none";
    const title = overlay.querySelector("#f-title").value.trim();
    const amount = currentAmount();
    const category = overlay.querySelector("#f-category").value;
    const date = overlay.querySelector("#f-date").value;
    const payerId = Number(overlay.querySelector("#f-payer").value);

    if (!title) { showFormError(errorEl, "Укажите название траты"); return; }
    if (!amount || amount <= 0) { showFormError(errorEl, "Укажите сумму больше нуля"); return; }

    const form = new FormData();
    form.append("title", title);
    form.append("amount", String(amount));
    form.append("category", category);
    form.append("expense_date", date);
    form.append("payer_member_id", String(payerId));
    form.append("split_type", splitType);

    if (splitType === "equal") {
      const ids = Array.from(selectedIds);
      if (ids.length === 0) { showFormError(errorEl, "Выберите хотя бы одного участника"); return; }
      form.append("participant_ids", JSON.stringify(ids));
    } else {
      const shares = state.members
        .filter((m) => customAmounts[m.id] > 0)
        .map((m) => ({ member_id: m.id, amount: customAmounts[m.id] }));
      const sum = shares.reduce((s, x) => s + x.amount, 0);
      if (Math.abs(sum - amount) > 0.01) {
        showFormError(errorEl, `Сумма долей (${sum.toFixed(2)}) не совпадает с суммой траты (${amount.toFixed(2)})`);
        return;
      }
      form.append("custom_shares", JSON.stringify(shares));
    }

    const photoFile = photoInput.files[0];
    if (photoFile) form.append("photo", photoFile);

    if (tg && tg.MainButton) tg.MainButton.showProgress(false);
    try {
      if (isEdit) {
        await api(`/chats/${state.chatId}/expenses/${existing.id}`, { method: "PATCH", body: form, isForm: true });
      } else {
        await api(`/chats/${state.chatId}/expenses`, { method: "POST", body: form, isForm: true });
      }
      haptic("notification", "success");
      closeSheet(overlay);
      await renderExpensesTab();
    } catch (e) {
      showFormError(errorEl, e.message);
    } finally {
      if (tg && tg.MainButton) tg.MainButton.hideProgress();
    }
  });
  useMainButtonFor(overlay, overlay.querySelector("#f-submit"), isEdit ? "Сохранить" : "Добавить трату");

  const deleteBtn = overlay.querySelector("#f-delete");
  if (deleteBtn) {
    deleteBtn.addEventListener("click", async () => {
      const ok = await confirmAction("Удалить эту трату без возможности восстановления?");
      if (!ok) return;
      try {
        await api(`/chats/${state.chatId}/expenses/${existing.id}`, { method: "DELETE" });
        haptic("notification", "success");
        closeSheet(overlay);
        await renderExpensesTab();
      } catch (e) {
        toast(e.message);
      }
    });
  }
}

function showFormError(el, message) {
  haptic("notification", "error");
  el.textContent = message;
  el.style.display = "block";
}

/* ---------------- Вкладка «Баланс» ---------------- */

async function renderBalanceTab() {
  const content = document.getElementById("content");
  try {
    state.balances = await api(`/chats/${state.chatId}/balances`);
    state.settlements = await api(`/chats/${state.chatId}/settlements`);
  } catch (e) {
    content.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    return;
  }

  const debts = state.balances.simplified_debts;
  content.innerHTML = `
    <div class="section-title">Кто кому должен</div>
    ${debts.length === 0
      ? '<div class="card empty-state" style="padding:20px;">Все в расчёте 🎉</div>'
      : debts.map((d, i) => `
        <div class="card debt-row" data-idx="${i}">
          <div class="who">${escapeHtml(memberLabel(d.from_member_id))} → ${escapeHtml(memberLabel(d.to_member_id))}</div>
          <div style="display:flex; align-items:center; gap:10px;">
            <b>${fmtMoney(d.amount)}</b>
            <button class="btn small settle-btn" data-idx="${i}">Погасить</button>
          </div>
        </div>`).join("")}

    <div class="section-title">Баланс участников</div>
    <div class="card">
      ${state.balances.balances.map((b) => `
        <div class="balance-row">
          <span>${escapeHtml(memberLabel(b.member_id))}</span>
          <span class="${Number(b.net) >= 0 ? "positive" : "negative"}">${Number(b.net) >= 0 ? "+" : ""}${fmtMoney(b.net)}</span>
        </div>`).join("")}
    </div>

    ${state.settlements.length > 0 ? `
      <div class="section-title">Последние платежи</div>
      <div class="card">
        ${state.settlements.slice(0, 10).map((s) => `
          <div class="balance-row">
            <span>${escapeHtml(memberLabel(s.from_member_id))} → ${escapeHtml(memberLabel(s.to_member_id))}</span>
            <span>${fmtMoney(s.amount)}</span>
          </div>`).join("")}
      </div>` : ""}
  `;

  content.querySelectorAll(".settle-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      haptic("impact", "light");
      openSettleModal(debts[Number(btn.dataset.idx)]);
    });
  });
}

function openSettleModal(debt) {
  const overlay = openSheet(`
    <div class="sheet-title">Погасить долг</div>
    <div class="field">
      <label>${escapeHtml(memberLabel(debt.from_member_id))} → ${escapeHtml(memberLabel(debt.to_member_id))}</label>
    </div>
    <div class="field">
      <label>Сумма (${escapeHtml(state.chat.currency)})</label>
      <input type="number" id="s-amount" min="0" step="0.01" value="${debt.amount}">
    </div>
    <div class="field">
      <label>Комментарий (необязательно)</label>
      <input type="text" id="s-note" placeholder="Перевёл на карту">
    </div>
    <div class="error-text" id="s-error" style="display:none;"></div>
    <button class="btn" id="s-submit">Подтвердить</button>
  `);

  overlay.querySelector("#s-submit").addEventListener("click", async () => {
    const errorEl = overlay.querySelector("#s-error");
    const amount = Number(overlay.querySelector("#s-amount").value || 0);
    if (!amount || amount <= 0) { showFormError(errorEl, "Укажите сумму больше нуля"); return; }
    if (tg && tg.MainButton) tg.MainButton.showProgress(false);
    try {
      await api(`/chats/${state.chatId}/settlements`, {
        method: "POST",
        body: {
          from_member_id: debt.from_member_id,
          to_member_id: debt.to_member_id,
          amount,
          note: overlay.querySelector("#s-note").value || null,
        },
      });
      haptic("notification", "success");
      closeSheet(overlay);
      await renderBalanceTab();
    } catch (e) {
      showFormError(errorEl, e.message);
    } finally {
      if (tg && tg.MainButton) tg.MainButton.hideProgress();
    }
  });
  useMainButtonFor(overlay, overlay.querySelector("#s-submit"), "Подтвердить");
}

/* ---------------- Вкладка «Статистика» ---------------- */

let statsChart = null;

async function renderStatsTab() {
  const content = document.getElementById("content");
  let stats;
  try {
    stats = await api(`/chats/${state.chatId}/stats?month=${state.month}`);
  } catch (e) {
    content.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    return;
  }

  content.innerHTML = `
    <div class="month-picker">
      <button data-dir="-1">‹</button>
      <div class="label">${monthLabel(state.month)}</div>
      <button data-dir="1">›</button>
    </div>
    <div class="total-line">${fmtMoney(stats.total)}</div>
    ${stats.by_category.length === 0 ? '<div class="empty-state">Нет трат за этот месяц.</div>' : `
      <div class="chart-wrap"><canvas id="stats-chart" height="220"></canvas></div>
      <div class="card">
        ${stats.by_category.map((c) => `
          <div class="balance-row">
            <span>${categoryIcon(c.category)} ${escapeHtml(c.category)} <span class="hint-text">(${c.count})</span></span>
            <span>${fmtMoney(c.total)}</span>
          </div>`).join("")}
      </div>`}
  `;

  content.querySelectorAll(".month-picker button").forEach((b) => {
    b.addEventListener("click", async () => {
      haptic("selection");
      state.month = shiftMonth(state.month, Number(b.dataset.dir));
      await renderStatsTab();
    });
  });

  if (stats.by_category.length > 0 && window.Chart) {
    const ctx = document.getElementById("stats-chart");
    if (statsChart) statsChart.destroy();
    const palette = ["#2481cc", "#34c759", "#ff9500", "#ff3b30", "#af52de", "#5ac8fa", "#ffcc00", "#8e8e93"];
    statsChart = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: stats.by_category.map((c) => `${categoryIcon(c.category)} ${c.category}`),
        datasets: [{
          data: stats.by_category.map((c) => Number(c.total)),
          backgroundColor: stats.by_category.map((_, i) => palette[i % palette.length]),
          borderWidth: 0,
        }],
      },
      options: {
        plugins: { legend: { position: "bottom", labels: { color: getComputedStyle(document.body).color } } },
      },
    });
  }
}

/* ---------------- Вкладка «Повторяющиеся траты» ---------------- */

async function renderRecurringTab() {
  const content = document.getElementById("content");
  try {
    state.recurring = await api(`/chats/${state.chatId}/recurring`);
  } catch (e) {
    content.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    return;
  }

  content.innerHTML = `
    <div class="hint-text" style="margin: 8px 4px 14px;">
      Раз в месяц, в указанный день, такая трата добавляется автоматически, и в чат приходит уведомление.
    </div>
    ${state.recurring.length === 0 ? '<div class="empty-state">Повторяющихся трат пока нет.<br>Нажмите «+», чтобы добавить (например, аренду).</div>' : state.recurring.map((r) => `
      <div class="card expense-card" data-id="${r.id}">
        <div class="expense-thumb placeholder">${r.is_active ? "🔁" : "⏸"}</div>
        <div class="expense-main">
          <div class="expense-title">${escapeHtml(r.title)}</div>
          <div class="expense-meta">
            <span class="badge">${categoryIcon(r.category)} ${escapeHtml(r.category)}</span>
            каждое ${r.day_of_month} число · ${escapeHtml(memberLabel(r.payer_member_id))}
            ${r.is_active ? "" : " · остановлено"}
          </div>
        </div>
        <div class="expense-amount">${fmtMoney(r.amount)}</div>
      </div>`).join("")}
  `;

  content.querySelectorAll(".expense-card").forEach((el) => {
    el.addEventListener("click", () => {
      const r = state.recurring.find((x) => x.id === Number(el.dataset.id));
      openRecurringModal(r);
    });
  });
}

function openRecurringModal(existing) {
  const isEdit = !!existing;
  const selectedIds = new Set(existing ? existing.participants.map((p) => p.member_id) : state.members.map((m) => m.id));
  let splitType = existing ? existing.split_type : "equal";
  const customAmounts = {};
  if (existing) existing.participants.forEach((p) => { if (p.custom_amount) customAmounts[p.member_id] = Number(p.custom_amount); });

  const overlay = openSheet(`
    <div class="sheet-title">${isEdit ? "Редактировать повтор" : "Новая повторяющаяся трата"}</div>
    <div class="field">
      <label>Название</label>
      <input type="text" id="r-title" value="${existing ? escapeHtml(existing.title) : ""}" placeholder="Аренда квартиры, подписка…">
    </div>
    <div class="field">
      <label>Сумма (${escapeHtml(state.chat.currency)})</label>
      <input type="number" id="r-amount" min="0" step="0.01" value="${existing ? existing.amount : ""}">
    </div>
    <div class="field">
      <label>Категория</label>
      <select id="r-category">
        ${state.categories.map((c) => `<option value="${escapeHtml(c)}" ${existing && existing.category === c ? "selected" : ""}>${categoryIcon(c)} ${escapeHtml(c)}</option>`).join("")}
      </select>
    </div>
    <div class="field">
      <label>День месяца (1–28)</label>
      <input type="number" id="r-day" min="1" max="28" value="${existing ? existing.day_of_month : 1}">
    </div>
    <div class="field">
      <label>Кто платит</label>
      <select id="r-payer">
        ${state.members.map((m) => `<option value="${m.id}" ${(existing ? existing.payer_member_id : state.member.id) === m.id ? "selected" : ""}>${escapeHtml(m.full_name)}</option>`).join("")}
      </select>
    </div>
    <div class="field">
      <label>Как делить</label>
      <div class="split-toggle">
        <div data-v="equal" class="${splitType === "equal" ? "active" : ""}">Поровну</div>
        <div data-v="custom" class="${splitType === "custom" ? "active" : ""}">Вручную</div>
      </div>
    </div>
    <div class="field" id="r-participants-block"></div>
    ${isEdit ? `<div class="field"><label><input type="checkbox" id="r-active" ${existing.is_active ? "checked" : ""}> Активна</label></div>` : ""}
    <div class="error-text" id="r-error" style="display:none;"></div>
    <button class="btn" id="r-submit">${isEdit ? "Сохранить" : "Добавить"}</button>
    ${isEdit ? '<button class="btn danger" id="r-delete" style="margin-top:10px;">Удалить шаблон</button>' : ""}
  `);

  function currentAmount() { return Number(overlay.querySelector("#r-amount").value || 0); }

  function renderParticipants() {
    const block = overlay.querySelector("#r-participants-block");
    if (splitType === "equal") {
      block.innerHTML = `
        <label>Участники</label>
        <div class="chip-row">
          ${state.members.map((m) => `<div class="chip ${selectedIds.has(m.id) ? "selected" : ""}" data-id="${m.id}">${escapeHtml(m.full_name)}</div>`).join("")}
        </div>`;
      block.querySelectorAll(".chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          haptic("selection");
          const id = Number(chip.dataset.id);
          if (selectedIds.has(id)) selectedIds.delete(id); else selectedIds.add(id);
          chip.classList.toggle("selected");
        });
      });
    } else {
      const amount = currentAmount();
      const sum = state.members.reduce((s, m) => s + (customAmounts[m.id] || 0), 0);
      block.innerHTML = `
        <label>Сумма на каждого</label>
        ${state.members.map((m) => `
          <div class="custom-share-row">
            <div class="name">${escapeHtml(m.full_name)}</div>
            <input type="number" min="0" step="0.01" data-id="${m.id}" class="r-custom-amount" value="${customAmounts[m.id] || ""}">
          </div>`).join("")}
        <div class="hint-text" id="r-sum-hint">Указано: ${sum.toFixed(2)} из ${amount.toFixed(2)} ${state.chat.currency}</div>`;
      block.querySelectorAll(".r-custom-amount").forEach((inp) => {
        inp.addEventListener("input", () => {
          customAmounts[Number(inp.dataset.id)] = Number(inp.value || 0);
          const s = state.members.reduce((acc, m) => acc + (customAmounts[m.id] || 0), 0);
          block.querySelector("#r-sum-hint").textContent = `Указано: ${s.toFixed(2)} из ${currentAmount().toFixed(2)} ${state.chat.currency}`;
        });
      });
    }
  }
  renderParticipants();

  overlay.querySelector("#r-amount").addEventListener("input", () => { if (splitType === "custom") renderParticipants(); });
  overlay.querySelectorAll(".split-toggle div").forEach((el) => {
    el.addEventListener("click", () => {
      haptic("selection");
      splitType = el.dataset.v;
      overlay.querySelectorAll(".split-toggle div").forEach((x) => x.classList.toggle("active", x === el));
      renderParticipants();
    });
  });

  overlay.querySelector("#r-submit").addEventListener("click", async () => {
    const errorEl = overlay.querySelector("#r-error");
    errorEl.style.display = "none";
    const title = overlay.querySelector("#r-title").value.trim();
    const amount = currentAmount();
    const category = overlay.querySelector("#r-category").value;
    const day = Number(overlay.querySelector("#r-day").value);
    const payerId = Number(overlay.querySelector("#r-payer").value);

    if (!title) { showFormError(errorEl, "Укажите название"); return; }
    if (!amount || amount <= 0) { showFormError(errorEl, "Укажите сумму больше нуля"); return; }
    if (!day || day < 1 || day > 28) { showFormError(errorEl, "День месяца должен быть от 1 до 28"); return; }

    let participants;
    if (splitType === "equal") {
      const ids = Array.from(selectedIds);
      if (ids.length === 0) { showFormError(errorEl, "Выберите хотя бы одного участника"); return; }
      participants = ids.map((id) => ({ member_id: id, custom_amount: null }));
    } else {
      participants = state.members
        .filter((m) => customAmounts[m.id] > 0)
        .map((m) => ({ member_id: m.id, custom_amount: customAmounts[m.id] }));
      const sum = participants.reduce((s, p) => s + p.custom_amount, 0);
      if (Math.abs(sum - amount) > 0.01) {
        showFormError(errorEl, `Сумма долей (${sum.toFixed(2)}) не совпадает с суммой (${amount.toFixed(2)})`);
        return;
      }
    }

    const payload = {
      title, amount, category, payer_member_id: payerId,
      split_type: splitType, day_of_month: day, participants,
    };
    if (isEdit) payload.is_active = overlay.querySelector("#r-active").checked;

    if (tg && tg.MainButton) tg.MainButton.showProgress(false);
    try {
      if (isEdit) {
        await api(`/chats/${state.chatId}/recurring/${existing.id}`, { method: "PATCH", body: payload });
      } else {
        await api(`/chats/${state.chatId}/recurring`, { method: "POST", body: payload });
      }
      haptic("notification", "success");
      closeSheet(overlay);
      await renderRecurringTab();
    } catch (e) {
      showFormError(errorEl, e.message);
    } finally {
      if (tg && tg.MainButton) tg.MainButton.hideProgress();
    }
  });
  useMainButtonFor(overlay, overlay.querySelector("#r-submit"), isEdit ? "Сохранить" : "Добавить");

  const deleteBtn = overlay.querySelector("#r-delete");
  if (deleteBtn) {
    deleteBtn.addEventListener("click", async () => {
      const ok = await confirmAction("Удалить этот шаблон повторяющейся траты?");
      if (!ok) return;
      try {
        await api(`/chats/${state.chatId}/recurring/${existing.id}`, { method: "DELETE" });
        haptic("notification", "success");
        closeSheet(overlay);
        await renderRecurringTab();
      } catch (e) {
        toast(e.message);
      }
    });
  }
}

init();
