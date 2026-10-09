import { state } from "../state.js";
import { api } from "../api.js";
import { tg, haptic, toast, confirmAction } from "../telegram.js";
import { monthLabel, shiftMonth, todayISO, fmtMoney, escapeHtml } from "../format.js";
import { openSheet, closeSheet, useMainButtonFor, showFormError } from "../sheet.js";

/* ---------------- Вкладка «Доходы» ----------------
   Только личное: «Мои финансы» и «Все траты» (хранится в личном пространстве). Сверху —
   сколько свободно в месяце и сколько можно тратить в день до следующего поступления
   (app/shared/income.py), ниже — регулярные доходы (в их день бот спрашивает «Пришла?»)
   и поступления месяца. */

const MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

function shortDate(iso) {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS_SHORT[m - 1]}`;
}

function incomeChatId() {
  return state.personalChatId;
}

async function renderIncomeTab() {
  const content = document.getElementById("content");
  let data;
  try {
    data = await api(`/chats/${incomeChatId()}/income?month=${state.month}`);
  } catch (e) {
    content.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    return;
  }

  const free = Number(data.free);
  const perDay = data.per_day !== null ? Number(data.per_day) : null;
  let untilNext = "";
  if (data.next_date) {
    const before = Number(data.obligations_before_next);
    untilNext = `
      <div class="free-next">
        До «${escapeHtml(data.next_title)}» ${shortDate(data.next_date)} — ${data.days_to_next} дн.
        <div class="free-per-day ${perDay < 0 ? "negative" : ""}">
          ${perDay >= 0 ? `можно тратить ~${fmtMoney(perDay)} в день` : `не хватает ${fmtMoney(-perDay * data.days_to_next)}`}
        </div>
        <div class="hint-text">Из уже полученных денег${before > 0 ? `, с учётом платежей до этой даты: ${fmtMoney(before)}` : ""}</div>
      </div>`;
  }

  content.innerHTML = `
    <div class="month-picker">
      <button data-dir="-1">‹</button>
      <div class="label">${monthLabel(state.month)}</div>
      <button data-dir="1">›</button>
    </div>
    <div class="card free-card">
      <div class="hint-text">Свободно в этом месяце</div>
      <div class="free-amount ${free < 0 ? "negative" : ""}">${fmtMoney(free)}</div>
      <div class="free-rows">
        <div><span>Получено</span><span>+${fmtMoney(data.received)}</span></div>
        ${Number(data.expected) > 0 ? `<div><span>Ожидается</span><span>+${fmtMoney(data.expected)}</span></div>` : ""}
        <div><span>Траты</span><span>−${fmtMoney(data.spent)}</span></div>
        ${Number(data.obligations_pending) > 0 ? `<div><span>📌 Платежи впереди</span><span>−${fmtMoney(data.obligations_pending)}</span></div>` : ""}
      </div>
      ${untilNext}
    </div>

    <div class="section-title">🔁 Регулярные доходы</div>
    ${data.sources.length === 0
      ? '<div class="hint-text" style="margin: 0 4px 10px;">Добавьте зарплату и аванс — в их день бот спросит «Пришла?», а здесь будет видно, сколько можно тратить до следующего поступления.</div>'
      : data.sources.map((s) => `
        <div class="card income-row" data-source="${s.id}">
          <div class="ob-main">
            <div class="ob-title">${escapeHtml(s.title)}</div>
            <div class="hint-text">каждое ${s.day_of_month} число${s.is_active ? "" : " · остановлен"}</div>
          </div>
          <div class="ob-right">
            <div class="ob-amount">${fmtMoney(s.amount)}</div>
            ${s.received ? '<span class="ob-status paid">✅ пришла</span>' : `<span class="ob-status">⏳ ${s.date ? shortDate(s.date) : ""}</span>`}
          </div>
        </div>`).join("")}
    <button class="btn secondary small" id="add-source" style="margin: 4px 0 18px;">+ Регулярный доход</button>

    <div class="section-title">💰 Поступления</div>
    ${data.incomes.length === 0
      ? '<div class="empty-state">Поступлений в этом месяце нет.<br>Нажмите «+» или напишите боту, например, <b>+120000 зарплата</b>.</div>'
      : data.incomes.map((i) => `
        <div class="card income-row" data-income="${i.id}">
          <div class="ob-main">
            <div class="ob-title">${escapeHtml(i.title)}</div>
            <div class="hint-text">${shortDate(i.income_date)}</div>
          </div>
          <div class="ob-right"><div class="ob-amount income-plus">+${fmtMoney(i.amount)}</div></div>
        </div>`).join("")}`;

  content.querySelectorAll(".month-picker button").forEach((b) => {
    b.addEventListener("click", async () => {
      haptic("selection");
      state.month = shiftMonth(state.month, Number(b.dataset.dir));
      await renderIncomeTab();
    });
  });
  content.querySelector("#add-source").addEventListener("click", () => openSourceModal(null));
  content.querySelectorAll("[data-source]").forEach((el) => {
    el.addEventListener("click", () => {
      const s = data.sources.find((x) => x.id === Number(el.dataset.source));
      if (!s.received && s.is_active) openIncomeModal(null, s);
      else openSourceModal(s);
    });
  });
  content.querySelectorAll("[data-income]").forEach((el) => {
    el.addEventListener("click", () => openIncomeModal(data.incomes.find((x) => x.id === Number(el.dataset.income))));
  });
}

/* Поступление: новое, по регулярному доходу (source — «Пришла») или правка существующего. */
function openIncomeModal(existing, source = null) {
  const isEdit = !!existing;
  const overlay = openSheet(`
    <div class="sheet-title">${isEdit ? "Поступление" : source ? `Пришла «${escapeHtml(source.title)}»?` : "Новый доход"}</div>
    <div class="field">
      <label>Название</label>
      <input type="text" id="i-title" value="${escapeHtml(existing ? existing.title : source ? source.title : "")}" placeholder="Зарплата, фриланс, кэшбэк…">
    </div>
    <div class="field-row">
      <div class="field">
        <label>Сумма (${escapeHtml(state.chat.currency)})</label>
        <input type="number" id="i-amount" min="0" step="0.01" value="${existing ? Number(existing.amount) : source ? Number(source.amount) : ""}">
      </div>
      <div class="field">
        <label>Дата</label>
        <input type="date" id="i-date" value="${existing ? existing.income_date : todayISO()}">
      </div>
    </div>
    <div class="error-text" id="i-error" style="display:none;"></div>
    <button class="btn" id="i-submit">${isEdit ? "Сохранить" : source ? "✅ Пришла" : "Добавить"}</button>
    ${isEdit ? '<button class="btn danger" id="i-delete" style="margin-top:10px;">Удалить</button>' : ""}
    ${source ? '<button class="btn secondary" id="i-source" style="margin-top:10px;">Изменить регулярный доход</button>' : ""}
  `);

  const submit = overlay.querySelector("#i-submit");
  submit.addEventListener("click", async () => {
    const errorEl = overlay.querySelector("#i-error");
    const amount = Number(overlay.querySelector("#i-amount").value || 0);
    if (!amount || amount <= 0) { showFormError(errorEl, "Укажите сумму больше нуля"); return; }
    const body = {
      title: overlay.querySelector("#i-title").value.trim() || "Доход",
      amount,
      income_date: overlay.querySelector("#i-date").value || null,
    };
    if (source) body.source_id = source.id;
    if (tg && tg.MainButton) tg.MainButton.showProgress(false);
    try {
      if (isEdit) await api(`/chats/${incomeChatId()}/income/${existing.id}`, { method: "PATCH", body });
      else await api(`/chats/${incomeChatId()}/income`, { method: "POST", body });
      haptic("notification", "success");
      closeSheet(overlay);
      await renderIncomeTab();
    } catch (e) {
      showFormError(errorEl, e.message);
    } finally {
      if (tg && tg.MainButton) tg.MainButton.hideProgress();
    }
  });
  useMainButtonFor(overlay, submit, isEdit ? "Сохранить" : source ? "✅ Пришла" : "Добавить");

  const del = overlay.querySelector("#i-delete");
  if (del) {
    del.addEventListener("click", async () => {
      if (!(await confirmAction("Удалить это поступление?"))) return;
      try {
        await api(`/chats/${incomeChatId()}/income/${existing.id}`, { method: "DELETE" });
        closeSheet(overlay);
        await renderIncomeTab();
      } catch (e) {
        toast(e.message);
      }
    });
  }
  const editSource = overlay.querySelector("#i-source");
  if (editSource) {
    editSource.addEventListener("click", () => {
      closeSheet(overlay);
      openSourceModal(source);
    });
  }
}

function openSourceModal(existing) {
  const isEdit = !!existing;
  const overlay = openSheet(`
    <div class="sheet-title">${isEdit ? "Регулярный доход" : "Новый регулярный доход"}</div>
    <div class="field">
      <label>Название</label>
      <input type="text" id="s-title" value="${existing ? escapeHtml(existing.title) : ""}" placeholder="Зарплата, аванс…">
    </div>
    <div class="field-row">
      <div class="field">
        <label>Сумма (${escapeHtml(state.chat.currency)})</label>
        <input type="number" id="s-amount" min="0" step="0.01" value="${existing ? Number(existing.amount) : ""}">
      </div>
      <div class="field">
        <label>День (1–28)</label>
        <input type="number" id="s-day" min="1" max="28" value="${existing ? existing.day_of_month : 10}">
      </div>
    </div>
    ${isEdit ? `<div class="field"><label><input type="checkbox" id="s-active" ${existing.is_active ? "checked" : ""}> Активен</label></div>` : ""}
    <div class="hint-text" style="margin-bottom:12px;">В этот день бот спросит в личке «Пришла?» — после ответа доход запишется.</div>
    <div class="error-text" id="s-error" style="display:none;"></div>
    <button class="btn" id="s-submit">${isEdit ? "Сохранить" : "Добавить"}</button>
    ${isEdit ? '<button class="btn danger" id="s-delete" style="margin-top:10px;">Удалить</button>' : ""}
  `);
  const submit = overlay.querySelector("#s-submit");
  submit.addEventListener("click", async () => {
    const errorEl = overlay.querySelector("#s-error");
    const title = overlay.querySelector("#s-title").value.trim();
    const amount = Number(overlay.querySelector("#s-amount").value || 0);
    const day = Number(overlay.querySelector("#s-day").value);
    if (!title) { showFormError(errorEl, "Укажите название"); return; }
    if (!amount || amount <= 0) { showFormError(errorEl, "Укажите сумму больше нуля"); return; }
    if (!day || day < 1 || day > 28) { showFormError(errorEl, "День должен быть от 1 до 28"); return; }
    const body = { title, amount, day_of_month: day, is_active: isEdit ? overlay.querySelector("#s-active").checked : true };
    try {
      if (isEdit) await api(`/chats/${incomeChatId()}/income-sources/${existing.id}`, { method: "PATCH", body });
      else await api(`/chats/${incomeChatId()}/income-sources`, { method: "POST", body });
      haptic("notification", "success");
      closeSheet(overlay);
      await renderIncomeTab();
    } catch (e) {
      showFormError(errorEl, e.message);
    }
  });
  useMainButtonFor(overlay, submit, isEdit ? "Сохранить" : "Добавить");
  const del = overlay.querySelector("#s-delete");
  if (del) {
    del.addEventListener("click", async () => {
      if (!(await confirmAction("Удалить регулярный доход? Записанные поступления останутся."))) return;
      try {
        await api(`/chats/${incomeChatId()}/income-sources/${existing.id}`, { method: "DELETE" });
        closeSheet(overlay);
        await renderIncomeTab();
      } catch (e) {
        toast(e.message);
      }
    });
  }
}

export { renderIncomeTab, openIncomeModal };
