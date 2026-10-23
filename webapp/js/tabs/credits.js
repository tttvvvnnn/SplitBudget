import { state } from "../state.js";
import { api } from "../api.js";
import { tg, haptic, toast, confirmAction } from "../telegram.js";
import { todayISO, fmtMoney, escapeHtml, dayOfMonthLabel, categoryIcon } from "../format.js";
import { openSheet, closeSheet, useMainButtonFor, showFormError } from "../sheet.js";
import { categoryFieldsHtml, bindCategoryFields, readCategoryFields } from "../category-fields.js";
import { openPaymentSheet } from "./budgets.js";
import { openRecurringModal } from "./recurring.js";

/* ---------------- Вкладка «Кредиты» ----------------
   Кредитные карты и кредиты «Моих финансов» (app/shared/credits.py). У карты долг и
   плавающие даты: срок без процентов и минимальный платёж. Покупки по карте живут в самой
   карте и тратами не считаются («🛒 Покупка» здесь или «Чем оплачено» → карта в трате).
   Тратой становится погашение: «Внести» записывает трату «Кредиты», и она закрывает покупки
   по очереди, от самых старых. «Продлить срок» переносит даты при том же долге, «Начислили %» —
   покупка «Проценты» в карте, «Сверить с банком» правит долг руками. Кредиты — обязательные
   платежи с остатком долга: платёж записывается тратой и уменьшает остаток. */

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
  const meta = [`Платёж ${Number(l.amount) ? fmtMoney(l.amount) : "без суммы"} · ${dayOfMonthLabel(l.day_of_month)}`];
  if (l.end_month) meta.push(`до ${monthShort(l.end_month)}`);
  if (l.rate !== null) meta.push(`${Number(l.rate)}%${Number(l.interest) ? `, набежало ${fmtMoney(l.interest)}` : ""}`);
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

  // .dash — две колонки на широком экране (style.css): карты слева, кредиты справа
  content.innerHTML = `
    <div class="dash two">
    <div class="dash-col">
    ${empty ? "" : `
      <div class="card credit-summary">
        <div class="hint-text">Всего долг</div>
        <div class="credit-total">${fmtMoney(cache.total_debt)}</div>
        ${cache.next_deadline ? `<div class="hint-text">Ближайший срок: ${dateLabel(cache.next_deadline)} (${daysText(cache.next_deadline)})</div>` : ""}
      </div>`}
    <div class="section-title">💳 Кредитные карты</div>
    ${active.length ? active.map(cardHtml).join("") : '<div class="empty-state">Карт пока нет.<br>Нажмите «+», чтобы добавить кредитку.</div>'}
    </div>
    <div class="dash-col">
    <div class="section-title">🏦 Кредиты</div>
    ${loans.map(loanHtml).join("")}
    <button class="btn secondary" id="add-loan">＋ Добавить кредит</button>
    <div class="hint-text" style="margin: 10px 4px 0;">Платёж по кредиту записывается тратой целиком и уменьшает остаток. За 7, 3 и 1 день до срока бот напомнит в личке — и про карты, и про кредиты.</div>
    ${archived.length ? `<div class="section-title">Архив</div>${archived.map(cardHtml).join("")}` : ""}
    </div>
    </div>`;

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

/* Кредит: остаток с процентами, платёж этого месяца, транши. */
function loanSummary(l) {
  const lines = [];
  if (l.debt !== null) {
    const interest = Number(l.interest || 0);
    lines.push(`Остаток ${fmtMoney(l.debt)}${interest ? ` (тело ${fmtMoney(l.body)} + проценты ${fmtMoney(interest)})` : ""}`);
  }
  const terms = [];
  if (l.loan_amount !== null) terms.push(`Сумма кредита ${fmtMoney(l.loan_amount)}`);
  if (l.rate !== null) terms.push(`${Number(l.rate)}% годовых, +${fmtMoney(l.daily_interest)} в день`);
  if (terms.length) lines.push(terms.join(" · "));
  return lines;
}

function openLoan(l) {
  const base = `/chats/${creditsChatId()}/loans/${l.recurring_id}/tranches`;
  const hasPayment = l.payment_id && l.payment_status;
  const overlay = openSheet(`
    <div class="sheet-title">🏦 ${escapeHtml(l.title)}</div>
    <div class="hint-text" style="margin-bottom:12px;">${loanSummary(l).join("<br>") || "Остаток не указан"}</div>
    ${hasPayment ? '<button class="btn" id="lo-pay">💸 Платёж этого месяца</button>' : ""}
    ${l.debt !== null ? `
    <div class="section-title" style="margin-top:18px;">Новый транш</div>
    <div class="field-row">
      <div class="field">
        <label>Сумма (${escapeHtml(state.chat.currency)})</label>
        <input type="number" id="lo-amount" min="0" step="0.01">
      </div>
      <div class="field">
        <label>Дата выдачи</label>
        <input type="date" id="lo-date" value="${todayISO()}" max="${todayISO()}">
      </div>
    </div>
    <div class="error-text" id="lo-error" style="display:none;"></div>
    <button class="btn secondary" id="lo-tranche">＋ Транш</button>
    <div class="hint-text" style="margin-top:8px;">Транш увеличит тело и сумму кредита, проценты на него пойдут с даты выдачи.</div>
    ${l.tranches.length ? `
      <div class="section-title" style="margin-top:18px;">Транши</div>
      ${l.tranches.map((t) => `
        <div class="deposit-row">
          <span>${t.tranche_date.split("-").reverse().join(".")}</span>
          <span>+${fmtMoney(t.amount)}</span>
          <button class="link-btn" data-tranche="${t.id}" aria-label="Удалить">✕</button>
        </div>`).join("")}` : ""}` : ""}
    <button class="btn secondary" id="lo-edit" style="margin-top:18px;">✏️ Изменить кредит</button>
  `);
  const errorEl = overlay.querySelector("#lo-error");
  async function run(path, body, method) {
    try {
      await api(path, { method, body });
      haptic("notification", "success");
      closeSheet(overlay);
      await renderCreditsTab();
    } catch (e) {
      showFormError(errorEl, e.message);
    }
  }
  if (hasPayment) {
    overlay.querySelector("#lo-pay").addEventListener("click", () => {
      closeSheet(overlay);
      openPaymentSheet({
        kind: "loan", chat_id: creditsChatId(), payment_id: l.payment_id, title: l.title,
        due_date: l.payment_due, status: l.payment_status, amount: l.payment_amount, share: l.payment_amount,
        debt: l.debt, end_month: l.end_month, chat_title: null,
      }, renderCreditsTab, () => editLoan(l));
    });
  }
  const trancheBtn = overlay.querySelector("#lo-tranche");
  if (trancheBtn) {
    trancheBtn.addEventListener("click", () => {
      const amount = Number(overlay.querySelector("#lo-amount").value || 0);
      const date = overlay.querySelector("#lo-date").value;
      if (!amount || amount <= 0) { showFormError(errorEl, "Укажите сумму транша"); return; }
      if (!date) { showFormError(errorEl, "Укажите дату выдачи"); return; }
      run(base, { amount, tranche_date: date }, "POST");
    });
  }
  overlay.querySelectorAll("[data-tranche]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!(await confirmAction("Удалить транш? Тело и проценты пересчитаются."))) return;
      run(`${base}/${btn.dataset.tranche}`, undefined, "DELETE");
    });
  });
  overlay.querySelector("#lo-edit").addEventListener("click", () => { closeSheet(overlay); editLoan(l); });
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

function shortDay(iso) {
  return iso.split("-").reverse().slice(0, 2).join(".");
}

/* Что ещё не погашено: погашения закрывают долг по очереди — сначала стартовый долг, потом
   покупки от самых старых. */
function unpaidHtml(c) {
  const opening = Number(c.opening_left);
  if (!c.unpaid.length && !opening) return "";
  return `
    <div class="section-title" style="margin-top:18px;">Не погашено</div>
    ${opening ? `
      <div class="deposit-row">
        <span></span>
        <span>Долг на начало</span>
        <span>${fmtMoney(opening)}</span>
      </div>` : ""}
    ${c.unpaid.map((p) => {
      const partly = Number(p.left) < Number(p.amount);
      return `
      <div class="deposit-row">
        <span>${shortDay(p.expense_date)}</span>
        <span>${categoryIcon(p.category)} ${escapeHtml(p.title)}</span>
        <span>${fmtMoney(p.left)}${partly ? `<span class="hint-text"> из ${fmtMoney(p.amount)}</span>` : ""}</span>
      </div>`;
    }).join("")}`;
}

/* История карты: операции (их можно отменить), покупки и погашения (их можно удалить —
   семейные покупки правятся в своём чате). */
function historyHtml(c) {
  const own = (p) => p.chat_id === creditsChatId();
  const rows = [
    ...c.operations.map((o) => ({ date: o.op_date, id: o.id, text: operationText(o), attr: `data-undo="${o.id}"` })),
    ...c.purchases.map((p) => ({
      date: p.expense_date,
      id: -p.id,
      text: p.is_payment ? `💸 ${p.title} −${fmtMoney(p.amount)}` : `🛒 ${p.title} +${fmtMoney(p.amount)}`,
      attr: own(p) ? `data-expense="${p.id}"` : "",
    })),
  ].sort((a, b) => (a.date === b.date ? b.id - a.id : a.date < b.date ? 1 : -1));
  if (!rows.length) return "";
  return `
    <div class="section-title" style="margin-top:18px;">История</div>
    ${rows.map((r) => `
      <div class="deposit-row">
        <span>${r.date.split("-").reverse().join(".")}</span>
        <span>${escapeHtml(r.text)}</span>
        ${r.attr ? `<button class="link-btn" ${r.attr} aria-label="Отменить">✕</button>` : '<span class="link-btn" aria-hidden="true"></span>'}
      </div>`).join("")}`;
}

/* Покупка по карте: записывается в карту (трата с card_id), тратой станет погашение. */
function openPurchaseSheet(c) {
  const overlay = openSheet(`
    <div class="sheet-title">🛒 Покупка · ${escapeHtml(c.title)}</div>
    <div class="field">
      <label>Что купили</label>
      <input type="text" id="cp-title" placeholder="Продукты, кино…">
    </div>
    <div class="field-row">
      <div class="field">
        <label>Сумма (${escapeHtml(state.chat.currency)})</label>
        <input type="number" id="cp-amount" min="0" step="0.01">
      </div>
      <div class="field">
        <label>Дата</label>
        <input type="date" id="cp-date" value="${todayISO()}">
      </div>
    </div>
    ${categoryFieldsHtml("cp", "Продукты", null)}
    <div class="hint-text" style="margin-bottom:12px;">Покупка останется в карте, чтобы помнить, на что ушёл долг. В тратах месяца её не будет — тратой станет погашение.</div>
    <div class="error-text" id="cp-error" style="display:none;"></div>
    <button class="btn" id="cp-submit">Добавить покупку</button>
  `);
  bindCategoryFields(overlay, "cp", () => {});
  const submit = overlay.querySelector("#cp-submit");
  submit.addEventListener("click", async () => {
    const errorEl = overlay.querySelector("#cp-error");
    const title = overlay.querySelector("#cp-title").value.trim();
    const amount = Number(overlay.querySelector("#cp-amount").value || 0);
    if (!title) { showFormError(errorEl, "Укажите, что купили"); return; }
    if (!amount || amount <= 0) { showFormError(errorEl, "Укажите сумму больше нуля"); return; }
    const { category, subcategory } = readCategoryFields(overlay, "cp");
    try {
      const pid = creditsChatId();
      const me = (await api(`/chats/${pid}/me`)).member.id;
      const form = new FormData();
      Object.entries({
        title, amount: String(amount), category, subcategory: subcategory || "",
        expense_date: overlay.querySelector("#cp-date").value || todayISO(),
        payer_member_id: String(me), split_type: "equal", participant_ids: JSON.stringify([me]),
        card_id: String(c.id),
      }).forEach(([k, v]) => form.append(k, v));
      await api(`/chats/${pid}/expenses`, { method: "POST", body: form, isForm: true });
      haptic("notification", "success");
      closeSheet(overlay);
      await renderCreditsTab();
    } catch (err) {
      showFormError(errorEl, err.message);
    }
  });
  useMainButtonFor(overlay, submit, "Добавить покупку");
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
    <div class="credit-actions">
      <button class="btn secondary" id="cc-purchase">🛒 Покупка</button>
      <button class="btn secondary" id="cc-adjust">🔄 Сверить с банком</button>
    </div>
    <div class="hint-text" style="margin-top:8px;">Покупки живут в карте и тратами не считаются. «Внести» запишет трату-погашение — она закроет самые старые покупки. «Начислили %» добавит покупку «Проценты». «Продлить срок» — долг тот же, новые даты.</div>
    ${unpaidHtml(c)}
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
  overlay.querySelector("#cc-purchase").addEventListener("click", () => { closeSheet(overlay); openPurchaseSheet(c); });
  overlay.querySelectorAll("[data-expense]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!(await confirmAction("Удалить эту запись? Долг карты пересчитается."))) return;
      try {
        await api(`/chats/${creditsChatId()}/expenses/${btn.dataset.expense}`, { method: "DELETE" });
        haptic("notification", "success");
        closeSheet(overlay);
        await renderCreditsTab();
      } catch (e) {
        showFormError(errorEl, e.message);
      }
    });
  });
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
    <div class="hint-text" style="margin-bottom:12px;">Даты — из последней выписки. Бот напомнит о них за 7, 3 и 1 день. Погашения карты записываются тратами в категории ниже и попадут в эту статью «Плана».</div>
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
      if (!(await confirmAction("Удалить карту вместе с историей? Её покупки и проценты станут обычными тратами. Если карту просто закрыли, лучше отправить её в архив."))) return;
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
