import { state } from "../state.js";
import { api } from "../api.js";
import { tg, haptic, toast, confirmAction } from "../telegram.js";
import { todayISO, fmtMoney, escapeHtml } from "../format.js";
import { openSheet, closeSheet, useMainButtonFor, showFormError } from "../sheet.js";
import { categoryFieldsHtml, bindCategoryFields, readCategoryFields } from "../category-fields.js";
import { openPaymentSheet } from "./budgets.js";
import { openRecurringModal } from "./recurring.js";

/* ---------------- Вкладка «Кредиты» ----------------
   Кредитные карты и кредиты «Моих финансов» (app/shared/credits.py). У карты долг и
   плавающие даты: срок без процентов и минимальный платёж. «Внести» уменьшает долг (это не
   трата), «Продлить срок» переносит даты при том же долге, «Начислили %» — трата в категории
   карты, долг растёт, «Сверить с банком» правит долг руками. Покупки — траты, у которых в
   «Чем оплачено» выбрана карта (tabs/expenses.js): долг считается с ними. Кредиты — обязательные платежи
   с остатком долга: платёж записывается тратой и уменьшает остаток. */

const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря"];

const OP_LABELS = {
  payment: "Внесено",
  extend: "Срок продлён",
  interest: "Проценты",
  adjust: "Сверка с банком",
};

let cache = null; // последний ответ GET /credits — для шторок

function creditsChatId() {
  return state.personalChatId;
}

function dateLabel(iso) {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS_GEN[m - 1]}`;
}

function daysUntil(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const [ty, tm, td] = todayISO().split("-").map(Number);
  return Math.round((Date.UTC(y, m - 1, d) - Date.UTC(ty, tm - 1, td)) / 86400000);
}

function daysText(iso) {
  const days = daysUntil(iso);
  if (days < 0) return "срок прошёл";
  if (days === 0) return "сегодня";
  if (days === 1) return "завтра";
  const n = days % 100;
  const word = n % 10 === 1 && n !== 11 ? "день" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n) ? "дня" : "дней";
  return `ещё ${days} ${word}`;
}

function monthShort(ym) {
  const [y, m] = ym.split("-").map(Number);
  return `${["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"][m - 1]} ${y}`;
}

/* Даты будущего месяца: тот же день через месяц (последний день, если такого нет). */
function plusMonth(iso) {
  const base = iso || todayISO();
  const [y, m, d] = base.split("-").map(Number);
  const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  return new Date(Date.UTC(y, m, Math.min(d, last))).toISOString().slice(0, 10);
}

function cardLines(c) {
  const lines = [];
  if (Number(c.debt) <= 0) {
    lines.push("Долга нет");
  } else {
    if (c.grace_until) {
      const late = daysUntil(c.grace_until) < 0;
      lines.push(`<span class="${late ? "negative" : ""}">Без процентов до ${dateLabel(c.grace_until)} (${daysText(c.grace_until)})</span>`);
    }
    if (c.payment_date) {
      const min = c.min_payment !== null && Number(c.min_payment) ? `Мин. платёж ${fmtMoney(c.min_payment)}` : "Платёж";
      lines.push(`${min} до ${dateLabel(c.payment_date)} (${daysText(c.payment_date)})`);
    }
  }
  if (c.statement_day) lines.push(`Выписка ${c.statement_day}-го`);
  return lines;
}

function cardHtml(c) {
  const limit = c.credit_limit !== null ? Number(c.credit_limit) : 0;
  const debt = Number(c.debt);
  const pct = limit > 0 ? Math.min((debt / limit) * 100, 100) : 0;
  const level = pct >= 90 ? "over" : pct >= 70 ? "warn" : "ok";
  return `
    <div class="card budget-card credit-card ${c.is_archived ? "archived" : ""}" data-card="${c.id}">
      <div class="budget-head">
        <span>💳 ${escapeHtml(c.title)}</span>
        <span>${fmtMoney(c.debt)}</span>
      </div>
      ${limit > 0 ? `
        <div class="budget-bar"><div class="budget-fill ${level}" style="width:${pct}%"></div></div>
        <div class="budget-sub credit-sub"><span>лимит ${fmtMoney(limit)}</span><span>доступно ${fmtMoney(Math.max(limit - debt, 0))}</span></div>` : ""}
      <div class="credit-lines">${cardLines(c).join("<br>")}</div>
      ${c.is_archived ? '<div class="hint-text">в архиве</div>' : ""}
    </div>`;
}

function loanHtml(l) {
  const meta = [`Платёж ${Number(l.amount) ? fmtMoney(l.amount) : "без суммы"} · ${l.day_of_month}-го`];
  if (l.end_month) meta.push(`до ${monthShort(l.end_month)}`);
  let status = "";
  if (!l.is_active) status = "остановлен";
  else if (l.payment_status === "paid") status = `✅ в этом месяце оплачен${l.next_due ? ` · следующий ${dateLabel(l.next_due)}` : ""}`;
  else if (l.payment_status === "pending") status = `Платёж ${dateLabel(l.payment_due)} (${daysText(l.payment_due)})`;
  else if (l.next_due) status = `Следующий платёж ${dateLabel(l.next_due)}`;
  return `
    <div class="card budget-card credit-card ${l.is_active ? "" : "archived"}" data-loan="${l.recurring_id}">
      <div class="budget-head">
        <span>🏦 ${escapeHtml(l.title)}</span>
        <span>${l.debt !== null ? fmtMoney(l.debt) : ""}</span>
      </div>
      <div class="credit-lines">${meta.join(" · ")}${status ? `<br>${status}` : ""}</div>
    </div>`;
}

async function renderCreditsTab() {
  const content = document.getElementById("content");
  try {
    cache = await api(`/chats/${creditsChatId()}/credits`);
  } catch (e) {
    content.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    return;
  }
  const { cards, loans } = cache;
  const active = cards.filter((c) => !c.is_archived);
  const archived = cards.filter((c) => c.is_archived);
  const empty = !cards.length && !loans.length;

  content.innerHTML = `
    ${empty ? "" : `
      <div class="card credit-summary">
        <div class="hint-text">Всего долг</div>
        <div class="credit-total">${fmtMoney(cache.total_debt)}</div>
        ${cache.next_deadline ? `<div class="hint-text">Ближайший срок: ${dateLabel(cache.next_deadline)} (${daysText(cache.next_deadline)})</div>` : ""}
      </div>`}
    <div class="section-title">💳 Кредитные карты</div>
    ${active.length ? active.map(cardHtml).join("") : '<div class="empty-state">Карт пока нет.<br>Нажмите «+», чтобы добавить кредитку.</div>'}
    <div class="section-title">🏦 Кредиты</div>
    ${loans.map(loanHtml).join("")}
    <button class="btn secondary" id="add-loan">＋ Добавить кредит</button>
    <div class="hint-text" style="margin: 10px 4px 0;">Платёж по кредиту записывается тратой целиком и уменьшает остаток. За 7, 3 и 1 день до срока бот напомнит в личке — и про карты, и про кредиты.</div>
    ${archived.length ? `<div class="section-title">Архив</div>${archived.map(cardHtml).join("")}` : ""}`;

  content.querySelectorAll("[data-card]").forEach((el) => {
    el.addEventListener("click", () => openCardSheet(cards.find((c) => c.id === Number(el.dataset.card))));
  });
  content.querySelectorAll("[data-loan]").forEach((el) => {
    el.addEventListener("click", () => openLoan(loans.find((l) => l.recurring_id === Number(el.dataset.loan))));
  });
  content.querySelector("#add-loan").addEventListener("click", () => {
    haptic("impact", "light");
    openRecurringModal(null, { kind: "loan", onSaved: renderCreditsTab });
  });
}

/* Кредит: платёж этого месяца — та же шторка, что в «Платежах»; без платежа — правка кредита. */
async function openLoan(l) {
  if (l.payment_id && l.payment_status) {
    openPaymentSheet({
      kind: "loan", chat_id: creditsChatId(), payment_id: l.payment_id, title: l.title,
      due_date: l.payment_due, status: l.payment_status, amount: l.payment_amount, share: l.payment_amount,
      debt: l.debt, end_month: l.end_month, chat_title: null,
    }, renderCreditsTab, () => editLoan(l));
    return;
  }
  await editLoan(l);
}

async function editLoan(l) {
  try {
    const list = await api(`/chats/${creditsChatId()}/recurring`);
    const r = list.find((x) => x.id === l.recurring_id);
    if (r) openRecurringModal(r, { onSaved: renderCreditsTab });
  } catch (e) {
    toast(e.message);
  }
}

function operationText(o) {
  if (o.kind === "extend") {
    let to = "";
    try {
      const n = JSON.parse(o.note).new;
      to = [n.grace_until && `без % до ${dateLabel(n.grace_until)}`, n.payment_date && `платёж ${dateLabel(n.payment_date)}`]
        .filter(Boolean).join(", ");
    } catch (e) { /* старый формат — просто подпись */ }
    return `${OP_LABELS.extend}${to ? `: ${to}` : ""}`;
  }
  const amount = Number(o.amount);
  if (o.kind === "adjust") return `${OP_LABELS.adjust}: ${amount >= 0 ? "+" : "−"}${fmtMoney(Math.abs(amount))}`;
  if (o.kind === "payment") return `${OP_LABELS.payment} −${fmtMoney(amount)}`;
  return `${OP_LABELS.interest} +${fmtMoney(amount)}`;
}

/* История карты: операции (их можно отменить) и покупки — траты, оплаченные картой (они
   правятся во вкладке «Траты»). */
function historyHtml(c) {
  const rows = [
    ...c.operations.map((o) => ({ date: o.op_date, id: o.id, text: operationText(o), undo: true })),
    ...c.purchases.map((p) => ({ date: p.expense_date, id: -p.id, text: `🛒 ${p.title} +${fmtMoney(p.amount)}`, undo: false })),
  ].sort((a, b) => (a.date === b.date ? b.id - a.id : a.date < b.date ? 1 : -1));
  if (!rows.length) return "";
  return `
    <div class="section-title" style="margin-top:18px;">История</div>
    ${rows.map((r) => `
      <div class="deposit-row">
        <span>${r.date.split("-").reverse().join(".")}</span>
        <span>${escapeHtml(r.text)}</span>
        ${r.undo ? `<button class="link-btn" data-undo="${r.id}" aria-label="Отменить">✕</button>` : '<span class="link-btn" aria-hidden="true"></span>'}
      </div>`).join("")}`;
}

function openCardSheet(c) {
  const base = `/chats/${creditsChatId()}/credit-cards/${c.id}`;
  const suggested = c.min_payment !== null && Number(c.min_payment) ? Number(c.min_payment) : "";
  const overlay = openSheet(`
    <div class="sheet-title">💳 ${escapeHtml(c.title)}</div>
    <div class="hint-text" style="margin-bottom:12px;">Долг ${fmtMoney(c.debt)}${c.credit_limit !== null ? ` из ${fmtMoney(c.credit_limit)}` : ""}<br>${cardLines(c).join("<br>")}</div>
    <div class="field">
      <label>Сумма (${escapeHtml(state.chat.currency)})</label>
      <input type="number" id="cc-amount" min="0" step="0.01" value="${suggested}">
    </div>
    <div class="error-text" id="cc-error" style="display:none;"></div>
    <button class="btn" id="cc-pay">✅ Внести</button>
    <div class="credit-actions">
      <button class="btn secondary" id="cc-extend">📅 Продлить срок</button>
      <button class="btn secondary" id="cc-interest">➕ Начислили %</button>
    </div>
    <button class="btn secondary" id="cc-adjust" style="margin-top:10px;">🔄 Сверить с банком</button>
    <div class="hint-text" style="margin-top:8px;">«Внести» уменьшает долг и тратой не считается. «Продлить срок» — долг тот же, новые даты. «Начислили %» запишет трату в категории карты. Покупки добавляются сами: в трате выберите «Чем оплачено» → эту карту.</div>
    ${historyHtml(c)}
    <button class="btn secondary" id="cc-edit" style="margin-top:18px;">✏️ Изменить карту</button>
  `);
  const errorEl = overlay.querySelector("#cc-error");

  async function run(path, body, method = "POST") {
    try {
      await api(`${base}${path}`, { method, body });
      haptic("notification", "success");
      closeSheet(overlay);
      await renderCreditsTab();
    } catch (e) {
      showFormError(errorEl, e.message);
    }
  }
  function amount() {
    const value = Number(overlay.querySelector("#cc-amount").value || 0);
    if (!value || value <= 0) { showFormError(errorEl, "Укажите сумму больше нуля"); return null; }
    return value;
  }

  const payBtn = overlay.querySelector("#cc-pay");
  payBtn.addEventListener("click", () => { const a = amount(); if (a) run("/pay", { amount: a, op_date: todayISO() }); });
  useMainButtonFor(overlay, payBtn, "✅ Внести");
  overlay.querySelector("#cc-interest").addEventListener("click", () => { const a = amount(); if (a) run("/interest", { amount: a }); });
  overlay.querySelector("#cc-extend").addEventListener("click", () => { closeSheet(overlay); openExtendSheet(c); });
  overlay.querySelector("#cc-adjust").addEventListener("click", () => { closeSheet(overlay); openAdjustSheet(c); });
  overlay.querySelector("#cc-edit").addEventListener("click", () => { closeSheet(overlay); openCardModal(c); });
  overlay.querySelectorAll("[data-undo]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!(await confirmAction("Отменить эту операцию? Долг и даты вернутся как были."))) return;
      run(`/operations/${btn.dataset.undo}`, undefined, "DELETE");
    });
  });
}

/* «Продлить срок»: долг не меняется, вводятся новые даты из выписки. */
function openExtendSheet(c) {
  const overlay = openSheet(`
    <div class="sheet-title">📅 Продлить срок</div>
    <div class="hint-text" style="margin-bottom:12px;">Долг остаётся ${fmtMoney(c.debt)}. Укажите новые даты из выписки или приложения банка.</div>
    <div class="field">
      <label>Без процентов до</label>
      <input type="date" id="ce-grace" value="${c.grace_until ? plusMonth(c.grace_until) : ""}">
    </div>
    <div class="field-row">
      <div class="field">
        <label>Мин. платёж до</label>
        <input type="date" id="ce-date" value="${c.payment_date ? plusMonth(c.payment_date) : ""}">
      </div>
      <div class="field">
        <label>Мин. платёж</label>
        <input type="number" id="ce-min" min="0" step="0.01" value="${c.min_payment !== null ? Number(c.min_payment) : ""}" placeholder="необязательно">
      </div>
    </div>
    <div class="error-text" id="ce-error" style="display:none;"></div>
    <button class="btn" id="ce-submit">Продлить</button>
  `);
  const submit = overlay.querySelector("#ce-submit");
  submit.addEventListener("click", async () => {
    const grace = overlay.querySelector("#ce-grace").value || null;
    const date = overlay.querySelector("#ce-date").value || null;
    const min = overlay.querySelector("#ce-min").value;
    if (!grace && !date) { showFormError(overlay.querySelector("#ce-error"), "Укажите хотя бы одну дату"); return; }
    try {
      await api(`/chats/${creditsChatId()}/credit-cards/${c.id}/extend`, {
        method: "POST", body: { grace_until: grace, payment_date: date, min_payment: date && min !== "" ? Number(min) : null },
      });
      haptic("notification", "success");
      closeSheet(overlay);
      await renderCreditsTab();
    } catch (e) {
      showFormError(overlay.querySelector("#ce-error"), e.message);
    }
  });
  useMainButtonFor(overlay, submit, "Продлить");
}

function openAdjustSheet(c) {
  const overlay = openSheet(`
    <div class="sheet-title">🔄 Сверить с банком</div>
    <div class="hint-text" style="margin-bottom:12px;">Сейчас долг ${fmtMoney(c.debt)}. Если в банке другая сумма (комиссии, покупки, которые не записали), укажите её.</div>
    <div class="field">
      <label>Долг по данным банка (${escapeHtml(state.chat.currency)})</label>
      <input type="number" id="ca-debt" min="0" step="0.01" value="${Number(c.debt)}">
    </div>
    <div class="error-text" id="ca-error" style="display:none;"></div>
    <button class="btn" id="ca-submit">Сохранить</button>
  `);
  const submit = overlay.querySelector("#ca-submit");
  submit.addEventListener("click", async () => {
    const raw = overlay.querySelector("#ca-debt").value;
    if (raw === "" || Number(raw) < 0) { showFormError(overlay.querySelector("#ca-error"), "Укажите долг"); return; }
    try {
      await api(`/chats/${creditsChatId()}/credit-cards/${c.id}/adjust`, { method: "POST", body: { debt: Number(raw) } });
      haptic("notification", "success");
      closeSheet(overlay);
      await renderCreditsTab();
    } catch (e) {
      showFormError(overlay.querySelector("#ca-error"), e.message);
    }
  });
  useMainButtonFor(overlay, submit, "Сохранить");
}

function openCardModal(existing) {
  const isEdit = !!existing;
  const e = existing || {};
  const num = (v) => (v !== null && v !== undefined ? Number(v) : "");
  const overlay = openSheet(`
    <div class="sheet-title">${isEdit ? "Кредитная карта" : "Новая кредитная карта"}</div>
    <div class="field">
      <label>Название</label>
      <input type="text" id="cm-title" value="${isEdit ? escapeHtml(e.title) : ""}" placeholder="Т-Банк Платинум, Альфа 100 дней…">
    </div>
    <div class="field-row">
      ${isEdit ? "" : `
      <div class="field">
        <label>Долг сейчас (${escapeHtml(state.chat.currency)})</label>
        <input type="number" id="cm-debt" min="0" step="0.01" placeholder="0">
      </div>`}
      <div class="field">
        <label>Кредитный лимит</label>
        <input type="number" id="cm-limit" min="0" step="1" value="${num(e.credit_limit)}" placeholder="необязательно">
      </div>
    </div>
    <div class="field-row">
      <div class="field">
        <label>Без процентов до</label>
        <input type="date" id="cm-grace" value="${e.grace_until || ""}">
      </div>
      <div class="field">
        <label>День выписки</label>
        <input type="number" id="cm-statement" min="1" max="31" value="${num(e.statement_day)}" placeholder="необязательно">
      </div>
    </div>
    <div class="field-row">
      <div class="field">
        <label>Мин. платёж до</label>
        <input type="date" id="cm-date" value="${e.payment_date || ""}">
      </div>
      <div class="field">
        <label>Мин. платёж</label>
        <input type="number" id="cm-min" min="0" step="0.01" value="${num(e.min_payment)}" placeholder="необязательно">
      </div>
    </div>
    <div class="hint-text" style="margin-bottom:12px;">Даты — из последней выписки. Бот напомнит о них за 7, 3 и 1 день. Платежи по карте и проценты попадут в «План» по категории ниже.</div>
    ${categoryFieldsHtml("cm", e.category || "Кредиты", isEdit ? e.subcategory : "Кредитная карта")}
    ${isEdit ? `<div class="field"><label><input type="checkbox" id="cm-archived" ${e.is_archived ? "checked" : ""}> В архиве (карта закрыта)</label></div>` : ""}
    <div class="error-text" id="cm-error" style="display:none;"></div>
    <button class="btn" id="cm-submit">${isEdit ? "Сохранить" : "Добавить карту"}</button>
    ${isEdit ? '<button class="btn danger" id="cm-delete" style="margin-top:10px;">Удалить карту</button>' : ""}
  `);
  bindCategoryFields(overlay, "cm", () => {});

  const submit = overlay.querySelector("#cm-submit");
  submit.addEventListener("click", async () => {
    const errorEl = overlay.querySelector("#cm-error");
    const title = overlay.querySelector("#cm-title").value.trim();
    if (!title) { showFormError(errorEl, "Укажите название"); return; }
    const value = (id) => overlay.querySelector(id).value;
    const optNumber = (id) => (value(id) === "" ? null : Number(value(id)));
    const { category, subcategory } = readCategoryFields(overlay, "cm");
    const body = {
      title,
      credit_limit: optNumber("#cm-limit") || null,
      statement_day: optNumber("#cm-statement"),
      grace_until: value("#cm-grace") || null,
      payment_date: value("#cm-date") || null,
      min_payment: optNumber("#cm-min"),
      category, subcategory,
      is_archived: isEdit ? overlay.querySelector("#cm-archived").checked : false,
    };
    if (!isEdit) body.debt = optNumber("#cm-debt") || 0;
    if (tg && tg.MainButton) tg.MainButton.showProgress(false);
    try {
      const base = `/chats/${creditsChatId()}/credit-cards`;
      if (isEdit) await api(`${base}/${e.id}`, { method: "PATCH", body });
      else await api(base, { method: "POST", body });
      haptic("notification", "success");
      closeSheet(overlay);
      await renderCreditsTab();
    } catch (err) {
      showFormError(errorEl, err.message);
    } finally {
      if (tg && tg.MainButton) tg.MainButton.hideProgress();
    }
  });
  useMainButtonFor(overlay, submit, isEdit ? "Сохранить" : "Добавить карту");

  const del = overlay.querySelector("#cm-delete");
  if (del) {
    del.addEventListener("click", async () => {
      if (!(await confirmAction("Удалить карту вместе с историей? Записанные траты процентов останутся. Если карту просто закрыли, лучше отправить её в архив."))) return;
      try {
        await api(`/chats/${creditsChatId()}/credit-cards/${e.id}`, { method: "DELETE" });
        closeSheet(overlay);
        await renderCreditsTab();
      } catch (err) {
        toast(err.message);
      }
    });
  }
}

export { renderCreditsTab, openCardModal };
