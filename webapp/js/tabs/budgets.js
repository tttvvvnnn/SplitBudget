import { state, isAll, isPersonal } from "../state.js";
import { api } from "../api.js";
import { tg, haptic, toast, confirmAction } from "../telegram.js";
import { categoryIcon, monthLabel, shiftMonth, todayMonth, fmtMoney, escapeHtml } from "../format.js";
import { openSheet, closeSheet, useMainButtonFor, showFormError } from "../sheet.js";
import { KIND_INFO } from "./recurring.js";

/* ---------------- Вкладка «Лимиты» ----------------
   Месячные лимиты на весь месяц, категорию или подкатегорию (app/shared/budgets.py). В
   семейном чате — общие лимиты чата по полным суммам трат. В «Моих финансах» и «Все траты»
   — личные лимиты (хранятся в личном пространстве): считается доля пользователя во всех его
   тратах, и личных, и семейных. При 80% и 100% бот присылает уведомление.

   Сверху — обязательные платежи месяца (app/shared/obligations.py): неоплаченные
   «резервируют» деньги в лимитах, поэтому видно, сколько на самом деле свободно. */

function budgetsChatId() {
  return isAll() || isPersonal() ? state.personalChatId : state.chatId;
}

async function renderBudgetsTab() {
  const content = document.getElementById("content");
  let budgets, obligations;
  try {
    [budgets, obligations] = await Promise.all([
      api(`/chats/${budgetsChatId()}/budgets?month=${state.month}`),
      api(`/chats/${budgetsChatId()}/obligations?month=${state.month}`),
    ]);
  } catch (e) {
    content.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    return;
  }

  const hint = isAll() || isPersonal()
    ? "Личные лимиты: считается ваша доля во всех тратах — личных и семейных. Уведомления при 80% и 100% придут вам в личку от бота."
    : "Лимиты семьи: считаются все траты этого чата. Уведомления при 80% и 100% придут в чат.";

  content.innerHTML = `
    <div class="month-picker">
      <button data-dir="-1">‹</button>
      <div class="label">${monthLabel(state.month)}</div>
      <button data-dir="1">›</button>
    </div>
    ${obligationsHtml(obligations)}
    <div class="section-title">🎯 Лимиты</div>
    <div class="hint-text" style="margin: 0 4px 12px;">${escapeHtml(hint)}</div>
    ${budgets.length === 0
      ? '<div class="empty-state">Лимитов пока нет.<br>Нажмите «+», чтобы задать лимит на месяц или категорию.</div>'
      : budgets.map(budgetCardHtml).join("")}`;

  content.querySelectorAll(".month-picker button").forEach((b) => {
    b.addEventListener("click", async () => {
      haptic("selection");
      state.month = shiftMonth(state.month, Number(b.dataset.dir));
      await renderBudgetsTab();
    });
  });
  content.querySelectorAll(".obligation-row").forEach((el) => {
    el.addEventListener("click", () => {
      const item = obligations.items.find((o) => o.payment_id === Number(el.dataset.id));
      openPaymentSheet(item);
    });
  });
  content.querySelectorAll(".budget-card").forEach((el) => {
    el.addEventListener("click", () => openBudgetModal(budgets.find((b) => b.id === Number(el.dataset.id))));
  });
}

function budgetCardHtml(b) {
  const amount = Number(b.amount);
  const spent = Number(b.spent);
  const reserved = Number(b.reserved || 0);
  const used = spent + reserved;
  const pct = amount > 0 ? (used / amount) * 100 : 0;
  const level = pct >= 100 ? "over" : pct >= 80 ? "warn" : "ok";
  const spentWidth = amount > 0 ? Math.min((spent / amount) * 100, 100) : 0;
  const reservedWidth = amount > 0 ? Math.min((reserved / amount) * 100, 100 - spentWidth) : 0;
  const left = amount - used;
  const word = reserved > 0 ? "свободно" : "осталось";
  let sub = left >= 0 ? `${word} ${fmtMoney(left)}` : `превышен на ${fmtMoney(-left)}`;
  // Для текущего месяца — сколько можно тратить в день до конца месяца
  if (left > 0 && state.month === todayMonth()) {
    const now = new Date();
    const daysLeft = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate() - now.getDate() + 1;
    sub += ` · ~${fmtMoney(left / daysLeft)} в день`;
  }
  const icon = b.category ? categoryIcon(b.category) : "📅";
  const usedText = reserved > 0
    ? `${fmtMoney(spent)} + 📌 ${fmtMoney(reserved)} из ${fmtMoney(amount)}`
    : `${fmtMoney(spent)} из ${fmtMoney(amount)}`;
  return `
    <div class="card budget-card" data-id="${b.id}">
      <div class="budget-head">
        <span>${icon} ${escapeHtml(b.label)}</span>
        <span class="budget-pct ${level}">${Math.round(pct)}%</span>
      </div>
      <div class="budget-bar">
        <div class="budget-fill ${level}" style="width:${spentWidth}%"></div>
        ${reservedWidth > 0 ? `<div class="budget-fill reserved" style="width:${reservedWidth}%"></div>` : ""}
      </div>
      <div class="budget-sub">
        <span>${usedText}</span>
        <span class="hint-text">${escapeHtml(sub)}</span>
      </div>
    </div>`;
}

/* ---------------- Обязательные платежи ---------------- */

const MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

function shortDate(iso) {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS_SHORT[m - 1]}`;
}

function daysUntil(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((new Date(y, m - 1, d) - today) / 86400000);
}

function statusHtml(o) {
  if (o.status === "paid") return '<span class="ob-status paid">✅ оплачено</span>';
  if (o.status === "skipped") return '<span class="ob-status skipped">⏭ пропущен</span>';
  const days = daysUntil(o.due_date);
  if (days < 0) return '<span class="ob-status overdue">❗ просрочен</span>';
  if (days === 0) return '<span class="ob-status soon">сегодня</span>';
  if (days === 1) return '<span class="ob-status soon">завтра</span>';
  return `<span class="ob-status ${days <= 3 ? "soon" : ""}">через ${days} дн.</span>`;
}

function obligationsHtml(data) {
  if (!data.items.length) {
    const where = isAll() ? "в чатах или «Моих финансах»" : "во вкладке «📌 Платежи»";
    return `
      <div class="section-title">📌 Обязательные платежи</div>
      <div class="hint-text" style="margin: 0 4px 16px;">Аренду, кредиты, карты и подписки можно добавить ${where} — тогда здесь будет видно, сколько денег уже занято.</div>`;
  }
  const total = Number(data.total);
  const paid = Number(data.paid);
  const pending = Number(data.pending);
  return `
    <div class="section-title">📌 Обязательные платежи</div>
    <div class="card obligations-card">
      <div class="ob-summary">
        <div><div class="hint-text">Всего</div><b>${fmtMoney(total)}</b></div>
        <div><div class="hint-text">Оплачено</div><b>${fmtMoney(paid)}</b></div>
        <div><div class="hint-text">Впереди</div><b>${fmtMoney(pending)}</b></div>
      </div>
      ${data.items.map((o) => {
        const [icon] = KIND_INFO[o.kind] || KIND_INFO.other;
        const share = Number(o.share);
        const amount = Number(o.amount);
        const meta = [shortDate(o.due_date)];
        if (o.chat_title) meta.push(escapeHtml(o.chat_title));
        if (o.kind === "card" && o.debt !== null) meta.push(`долг ${fmtMoney(o.debt)}`);
        return `
          <div class="obligation-row ${o.status}" data-id="${o.payment_id}">
            <div class="ob-icon">${icon}</div>
            <div class="ob-main">
              <div class="ob-title">${escapeHtml(o.title)}</div>
              <div class="hint-text">${meta.join(" · ")}</div>
            </div>
            <div class="ob-right">
              <div class="ob-amount">${fmtMoney(share)}</div>
              ${share !== amount ? `<div class="hint-text">из ${fmtMoney(amount)}</div>` : ""}
              ${statusHtml(o)}
            </div>
          </div>`;
      }).join("")}
    </div>`;
}

function openPaymentSheet(o) {
  const [icon, kindLabel] = KIND_INFO[o.kind] || KIND_INFO.other;
  const base = `/chats/${o.chat_id}/payments/${o.payment_id}`;
  const lines = [`${kindLabel} · ${shortDate(o.due_date)}`];
  if (o.chat_title) lines.push(`Семейный чат «${escapeHtml(o.chat_title)}», ваша доля ${fmtMoney(o.share)}`);
  if (o.kind === "card") {
    lines.push("Платёж по карте не считается тратой: покупки по карте записываются отдельно.");
    if (o.debt !== null) lines.push(`Долг по карте: ${fmtMoney(o.debt)}`);
  }
  if (o.end_month) lines.push(`Последний платёж: ${monthLabel(o.end_month).toLowerCase()}`);

  let actions = "";
  if (o.status === "pending") {
    actions = `
      <div class="field">
        <label>Сумма (${escapeHtml(state.chat.currency)})</label>
        <input type="number" id="p-amount" min="0" step="0.01" value="${Number(o.amount)}">
      </div>
      <div class="error-text" id="p-error" style="display:none;"></div>
      <button class="btn" id="p-pay">✅ Оплачено</button>
      <button class="btn secondary" id="p-skip" style="margin-top:10px;">⏭ Не в этом месяце</button>`;
  } else {
    const what = o.status === "paid" ? `Оплачено ${fmtMoney(o.amount)}` : "Пропущен в этом месяце";
    actions = `
      <div class="hint-text" style="margin-bottom:12px;">${what}</div>
      <div class="error-text" id="p-error" style="display:none;"></div>
      <button class="btn secondary" id="p-reset">${o.status === "paid" ? "↩️ Отменить оплату" : "↩️ Вернуть платёж"}</button>`;
  }

  const overlay = openSheet(`
    <div class="sheet-title">${icon} ${escapeHtml(o.title)}</div>
    <div class="hint-text" style="margin-bottom:14px;">${lines.join("<br>")}</div>
    ${actions}
  `);

  async function run(path, body) {
    const errorEl = overlay.querySelector("#p-error");
    try {
      await api(`${base}/${path}`, { method: "POST", body });
      haptic("notification", "success");
      closeSheet(overlay);
      await renderBudgetsTab();
    } catch (e) {
      showFormError(errorEl, e.message);
    }
  }
  const payBtn = overlay.querySelector("#p-pay");
  if (payBtn) {
    payBtn.addEventListener("click", () => {
      const amount = Number(overlay.querySelector("#p-amount").value || 0);
      if (!amount || amount <= 0) { showFormError(overlay.querySelector("#p-error"), "Укажите сумму больше нуля"); return; }
      run("pay", { amount });
    });
    useMainButtonFor(overlay, payBtn, "✅ Оплачено");
    overlay.querySelector("#p-skip").addEventListener("click", () => run("skip"));
  }
  const resetBtn = overlay.querySelector("#p-reset");
  if (resetBtn) {
    resetBtn.addEventListener("click", async () => {
      if (o.status === "paid" && !(await confirmAction("Отменить оплату? Записанная трата удалится."))) return;
      run("reset");
    });
  }
}

/* ---------------- Шторка лимита ---------------- */

function subcategoryOptions(category, selected) {
  const node = state.categoryTree.find((c) => c.name === category);
  const subs = node ? node.subcategories : [];
  return `<option value="">Вся категория</option>` + subs
    .map((s) => `<option value="${escapeHtml(s)}" ${s === selected ? "selected" : ""}>${escapeHtml(s)}</option>`)
    .join("");
}

function openBudgetModal(existing) {
  const isEdit = !!existing;
  const category = existing ? existing.category : "";
  const overlay = openSheet(`
    <div class="sheet-title">${isEdit ? "Лимит" : "Новый лимит"}</div>
    <div class="field">
      <label>На что</label>
      <select id="b-category" ${isEdit ? "disabled" : ""}>
        <option value="">📅 Весь месяц (все траты)</option>
        ${state.categoryTree.map((c) => `<option value="${escapeHtml(c.name)}" ${c.name === category ? "selected" : ""}>${c.icon} ${escapeHtml(c.name)}</option>`).join("")}
      </select>
    </div>
    <div class="field" id="b-sub-field" style="display:none;">
      <label>Подкатегория</label>
      <select id="b-subcategory" ${isEdit ? "disabled" : ""}></select>
    </div>
    <div class="field">
      <label>Лимит на месяц (${escapeHtml(state.chat.currency)})</label>
      <input type="number" id="b-amount" min="0" step="1" value="${existing ? Number(existing.amount) : ""}">
    </div>
    <div class="error-text" id="b-error" style="display:none;"></div>
    <button class="btn" id="b-submit">${isEdit ? "Сохранить" : "Добавить лимит"}</button>
    ${isEdit ? '<button class="btn danger" id="b-delete" style="margin-top:10px;">Удалить лимит</button>' : ""}
  `);

  const catSelect = overlay.querySelector("#b-category");
  const subSelect = overlay.querySelector("#b-subcategory");
  function refreshSubs(selected) {
    const node = state.categoryTree.find((c) => c.name === catSelect.value);
    const hasSubs = !!(node && node.subcategories.length);
    overlay.querySelector("#b-sub-field").style.display = hasSubs ? "block" : "none";
    subSelect.innerHTML = subcategoryOptions(catSelect.value, selected);
  }
  refreshSubs(existing ? existing.subcategory : "");
  catSelect.addEventListener("change", () => refreshSubs(""));

  overlay.querySelector("#b-submit").addEventListener("click", async () => {
    const errorEl = overlay.querySelector("#b-error");
    const amount = Number(overlay.querySelector("#b-amount").value || 0);
    if (!amount || amount <= 0) { showFormError(errorEl, "Укажите лимит больше нуля"); return; }
    if (tg && tg.MainButton) tg.MainButton.showProgress(false);
    try {
      await api(`/chats/${budgetsChatId()}/budgets`, {
        method: "POST",
        body: { category: catSelect.value, subcategory: subSelect.value || null, amount },
      });
      haptic("notification", "success");
      closeSheet(overlay);
      await renderBudgetsTab();
    } catch (e) {
      showFormError(errorEl, e.message);
    } finally {
      if (tg && tg.MainButton) tg.MainButton.hideProgress();
    }
  });
  useMainButtonFor(overlay, overlay.querySelector("#b-submit"), isEdit ? "Сохранить" : "Добавить лимит");

  const deleteBtn = overlay.querySelector("#b-delete");
  if (deleteBtn) {
    deleteBtn.addEventListener("click", async () => {
      const ok = await confirmAction("Удалить этот лимит?");
      if (!ok) return;
      try {
        await api(`/chats/${budgetsChatId()}/budgets/${existing.id}`, { method: "DELETE" });
        haptic("notification", "success");
        closeSheet(overlay);
        await renderBudgetsTab();
      } catch (e) {
        toast(e.message);
      }
    });
  }
}

export { renderBudgetsTab, openBudgetModal };
