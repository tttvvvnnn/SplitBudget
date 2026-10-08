import { state, isPersonal, hideFamilyOnly } from "../state.js";
import { api, loadAuthedImage } from "../api.js";
import { tg, haptic, toast, confirmAction } from "../telegram.js";
import { todayISO, categoryIcon, monthLabel, shiftMonth, fmtMoney, escapeHtml } from "../format.js";
import { avatarHtml, loadAvatarsIn, memberById } from "../members.js";
import { openSheet, closeSheet, useMainButtonFor, showFormError } from "../sheet.js";
import { openAddMemberModal } from "../member-modals.js";
import { switchTab } from "../shell.js";
import { categoryFieldsHtml, bindCategoryFields, readCategoryFields, categoryBadgeHtml } from "../category-fields.js";

/* Компактная сводка личного баланса вверху вкладки «Траты» — чтобы не нужно было заходить
   на вкладку «Баланс» просто ради того, чтобы понять, кто кому сейчас должен. Тап по ней
   переключает на вкладку «Баланс» с полной картиной (кто кому и кнопка «Погасить»). */
function balanceBannerHtml() {
  if (!state.balances) return "";
  const mine = state.balances.balances.find((b) => b.member_id === state.member.id);
  const net = mine ? Number(mine.net) : 0;
  let cls = "neutral";
  let text = "Баланс: все в расчёте 🎉";
  if (net > 0.005) {
    cls = "positive";
    text = `Вам должны ${fmtMoney(net)}`;
  } else if (net < -0.005) {
    cls = "negative";
    text = `Вы должны ${fmtMoney(-net)}`;
  }
  return `
    <div class="balance-banner ${cls}" id="balance-banner">
      <span>${escapeHtml(text)}</span>
      <span class="chevron">›</span>
    </div>`;
}

/* ---------------- Вкладка «Траты» ---------------- */

async function renderExpensesTab() {
  const content = document.getElementById("content");
  try {
    // В личном пространстве один участник — баланса и долгов там нет.
    const [expenses, balances] = await Promise.all([
      api(`/chats/${state.chatId}/expenses?month=${state.month}`),
      isPersonal() ? null : api(`/chats/${state.chatId}/balances`),
    ]);
    state.expenses = expenses;
    state.balances = balances;
  } catch (e) {
    content.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    return;
  }

  const hasFilters = !!(state.filters.search || state.filters.category || state.filters.payer);

  content.innerHTML = `
    ${balanceBannerHtml()}
    <div class="month-picker">
      <button data-dir="-1">‹</button>
      <div class="label">${monthLabel(state.month)}</div>
      <button data-dir="1">›</button>
    </div>
    <div class="total-line" id="total-line"></div>
    <div class="top-actions">
      <button class="btn small secondary" id="filter-toggle-btn">🔍 Фильтр${hasFilters ? " •" : ""}</button>
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
        <div class="field" data-family-only>
          <select id="filter-payer">
            <option value="">Все участники</option>
            ${state.members.map((m) => `<option value="${m.id}" ${String(state.filters.payer) === String(m.id) ? "selected" : ""}>${escapeHtml(m.full_name)}</option>`).join("")}
          </select>
        </div>
      </div>
    </div>
    <div id="expense-list"></div>`;
  hideFamilyOnly(content);

  const balanceBanner = document.getElementById("balance-banner");
  if (balanceBanner) balanceBanner.addEventListener("click", () => switchTab("balance"));

  content.querySelectorAll(".month-picker button").forEach((b) => {
    b.addEventListener("click", async () => {
      haptic("selection");
      state.month = shiftMonth(state.month, Number(b.dataset.dir));
      await renderExpensesTab();
    });
  });
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
  if (filtered.length === 0) {
    list.innerHTML = `<div class="empty-state">${hasAnyFilter ? "Ничего не найдено." : "Трат за этот месяц пока нет.<br>Нажмите «+», чтобы добавить первую."}</div>`;
  } else {
    // Список уже приходит с бэкенда отсортированным по expense_date DESC — просто вставляем
    // заголовок при смене даты, без дополнительной группировки на клиенте
    let html = "";
    let lastDate = null;
    for (const e of filtered) {
      if (e.expense_date !== lastDate) {
        html += `<div class="day-header">${dayHeaderLabel(e.expense_date)}</div>`;
        lastDate = e.expense_date;
      }
      html += expenseCardHtml(e);
    }
    list.innerHTML = html;
  }

  list.querySelectorAll(".expense-card").forEach((el) => {
    el.addEventListener("click", () => {
      const expense = state.expenses.find((e) => e.id === Number(el.dataset.id));
      openExpenseModal(expense);
    });
  });
  list.querySelectorAll(".expense-thumb[data-photo]").forEach((img) => {
    loadAuthedImage(img, `/chats/${state.chatId}/${img.dataset.photo}`);
  });
  loadAvatarsIn(list);
}

/* «Сегодня» / «Вчера» / «пн, 3 сен» — заголовок группы трат за один день в списке */
function dayHeaderLabel(dateStr) {
  const d = new Date(`${dateStr}T00:00:00`);
  const today = new Date(`${todayISO()}T00:00:00`);
  const diffDays = Math.round((today - d) / 86400000);
  if (diffDays === 0) return "Сегодня";
  if (diffDays === 1) return "Вчера";
  const weekdays = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
  const months = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
  return `${weekdays[d.getDay()]}, ${d.getDate()} ${months[d.getMonth()]}`;
}

function expenseCardHtml(e) {
  const thumb = e.photo_url
    ? `<img class="expense-thumb" data-photo="${escapeHtml(e.photo_url)}">`
    : `<div class="expense-thumb placeholder">${categoryIcon(e.category)}</div>`;
  const payer = memberById(e.payer_member_id);
  // Кто должен скинуться на эту трату — участники из shares (обычно включая самого
  // плательщика, если он тоже участвует своей долей). Только аватарки, без имён — иначе
  // строка мета не помещалась бы уже при 3-4 участниках; полный список — во всплывающей
  // подсказке (title) и при открытии самой траты.
  const participants = e.shares.map((s) => memberById(s.member_id));
  const familyMeta = isPersonal() ? "" : `
          <span class="payer-avatar" title="Оплатил(а): ${escapeHtml(payer ? payer.full_name : "—")}">${avatarHtml(payer)}</span>
          ${participants.length > 0 ? `
            <span class="split-avatars" title="Делят: ${escapeHtml(participants.map((m) => (m ? m.full_name : "—")).join(", "))}">
              ${participants.map((m) => avatarHtml(m)).join("")}
            </span>` : ""}`;
  return `
    <div class="card expense-card" data-id="${e.id}">
      ${thumb}
      <div class="expense-main">
        <div class="expense-title">${escapeHtml(e.title)}</div>
        <div class="expense-meta">
          ${categoryBadgeHtml(e.category, e.subcategory)}${familyMeta}
          ${e.is_recurring ? '<span title="Повторяющаяся">🔁</span>' : ""}
        </div>
      </div>
      <div class="expense-amount">${fmtMoney(e.amount)}</div>
    </div>`;
}

/* ---------------- Модалка добавления/редактирования траты ---------------- */

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
      <div class="field" data-family-only>
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
    ${categoryFieldsHtml("f", existing && existing.category, existing && existing.subcategory)}
    <div class="field">
      <label>Дата</label>
      <input type="date" id="f-date" value="${existing ? existing.expense_date : todayISO()}">
    </div>
    <div class="field" data-family-only>
      <label>Как делить</label>
      <div class="split-toggle">
        <div data-v="equal" class="${splitType === "equal" ? "active" : ""}">Поровну между выбранными</div>
        <div data-v="custom" class="${splitType === "custom" ? "active" : ""}">Вручную по каждому</div>
      </div>
    </div>
    <div class="field" id="participants-block" data-family-only></div>
    <div class="error-text" id="f-error" style="display:none;"></div>
    <button class="btn" id="f-submit">${isEdit ? "Сохранить" : "Добавить трату"}</button>
    ${isEdit ? '<button class="btn danger" id="f-delete" style="margin-top:10px;">Удалить трату</button>' : ""}
  `);

  // В личном пространстве плательщик и участник — всегда сам пользователь (он же единственный
  // участник, поэтому selectedIds по умолчанию уже {он}), выбирать нечего.
  hideFamilyOnly(overlay);
  bindCategoryFields(overlay, "f");

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

  /* Payer-select строится один раз при открытии формы (см. ниже). Если новый участник
     добавлен прямо отсюда, его тоже нужно туда подмешать — иначе только что добавленного
     человека (например, "Мама заплатила за продукты") нельзя будет тут же выбрать
     плательщиком, только участником. */
  function refreshPayerOptions() {
    const select = overlay.querySelector("#f-payer");
    const current = select.value;
    select.innerHTML = state.members.map((m) => `<option value="${m.id}" ${String(m.id) === current ? "selected" : ""}>${escapeHtml(m.full_name)}</option>`).join("");
  }

  function renderParticipants() {
    const block = overlay.querySelector("#participants-block");
    if (splitType === "equal") {
      block.innerHTML = `
        <label>Участники (делим поровну)</label>
        <div class="chip-row">
          ${state.members.map((m) => `<div class="chip ${selectedIds.has(m.id) ? "selected" : ""}" data-id="${m.id}">${avatarHtml(m)}${escapeHtml(m.full_name)}</div>`).join("")}
          <div class="chip add-chip" id="add-participant-chip">+ Добавить</div>
        </div>`;
      loadAvatarsIn(block);
      block.querySelectorAll(".chip[data-id]").forEach((chip) => {
        chip.addEventListener("click", () => {
          haptic("selection");
          const id = Number(chip.dataset.id);
          if (selectedIds.has(id)) selectedIds.delete(id); else selectedIds.add(id);
          chip.classList.toggle("selected");
        });
      });
      block.querySelector("#add-participant-chip").addEventListener("click", () => {
        haptic("impact", "light");
        openAddMemberModal((member) => {
          selectedIds.add(member.id);
          refreshPayerOptions();
          renderParticipants();
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
        <div class="hint-text" id="sum-hint">Указано: ${sum.toFixed(2)} из ${amount.toFixed(2)} ${state.chat.currency}</div>
        <button type="button" class="btn secondary small" id="add-participant-btn" style="margin-top:8px;">+ Добавить участника</button>`;
      loadAvatarsIn(block);
      block.querySelectorAll(".custom-amount").forEach((inp) => {
        inp.addEventListener("input", () => {
          customAmounts[Number(inp.dataset.id)] = Number(inp.value || 0);
          const s = state.members.reduce((acc, m) => acc + (customAmounts[m.id] || 0), 0);
          block.querySelector("#sum-hint").textContent = `Указано: ${s.toFixed(2)} из ${currentAmount().toFixed(2)} ${state.chat.currency}`;
        });
      });
      block.querySelector("#add-participant-btn").addEventListener("click", () => {
        haptic("impact", "light");
        openAddMemberModal((member) => {
          customAmounts[member.id] = 0;
          refreshPayerOptions();
          renderParticipants();
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
    const { category, subcategory } = readCategoryFields(overlay, "f");
    const date = overlay.querySelector("#f-date").value;
    const payerId = Number(overlay.querySelector("#f-payer").value);

    if (!title) { showFormError(errorEl, "Укажите название траты"); return; }
    if (!amount || amount <= 0) { showFormError(errorEl, "Укажите сумму больше нуля"); return; }

    const form = new FormData();
    form.append("title", title);
    form.append("amount", String(amount));
    form.append("category", category);
    form.append("subcategory", subcategory);
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

export { renderExpensesTab, openExpenseModal, dayHeaderLabel };
