import { state } from "../state.js";
import { api } from "../api.js";
import { tg, haptic, toast, confirmAction } from "../telegram.js";
import { monthLabel, todayISO, fmtMoney, escapeHtml } from "../format.js";
import { openSheet, closeSheet, useMainButtonFor, showFormError } from "../sheet.js";

/* ---------------- Вкладка «Цели» ----------------
   Цели накоплений «Моих финансов» (app/shared/goals.py): накоплено из нужного, сколько
   откладывать в месяц до срока. Отложенное в месяце уменьшает «свободно» во вкладке
   «Доходы». */

function goalsChatId() {
  return state.personalChatId;
}

function goalCardHtml(g) {
  const target = Number(g.target);
  const saved = Number(g.saved);
  const pct = target > 0 ? Math.min((saved / target) * 100, 100) : 0;
  const done = saved >= target;
  let plan = "";
  if (done) plan = "🎉 Цель достигнута";
  else if (g.monthly_needed !== null) {
    const thisMonth = Number(g.saved_this_month);
    const needed = Number(g.monthly_needed);
    plan = `Срок — ${monthLabel(g.deadline.slice(0, 7)).toLowerCase()}: откладывать ~${fmtMoney(needed)} в месяц`;
    if (thisMonth > 0) plan += thisMonth >= needed ? " · ✅ в этом месяце отложено" : ` · в этом месяце ${fmtMoney(thisMonth)}`;
  } else if (Number(g.saved_this_month) > 0) {
    plan = `В этом месяце отложено ${fmtMoney(g.saved_this_month)}`;
  }
  return `
    <div class="card budget-card goal-card ${g.is_archived ? "archived" : ""}" data-id="${g.id}">
      <div class="budget-head">
        <span>🐷 ${escapeHtml(g.title)}</span>
        <span class="budget-pct ${done ? "" : ""}">${Math.round(target > 0 ? (saved / target) * 100 : 0)}%</span>
      </div>
      <div class="budget-bar"><div class="budget-fill ok" style="width:${pct}%"></div></div>
      <div class="budget-sub">
        <span>${fmtMoney(saved)} из ${fmtMoney(target)}</span>
        ${g.is_archived ? '<span class="hint-text">в архиве</span>' : ""}
      </div>
      ${plan ? `<div class="hint-text" style="margin-top:6px;">${escapeHtml(plan)}</div>` : ""}
    </div>`;
}

async function renderGoalsTab() {
  const content = document.getElementById("content");
  let goals;
  try {
    goals = await api(`/chats/${goalsChatId()}/goals`);
  } catch (e) {
    content.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    return;
  }
  content.innerHTML = `
    <div class="hint-text" style="margin: 8px 4px 14px;">
      Отложенное на цели в этом месяце вычитается из «свободно» во вкладке «💰 Доходы».
    </div>
    ${goals.length === 0
      ? '<div class="empty-state">Целей пока нет.<br>Нажмите «+», например: «Отпуск 200 000 к июлю».</div>'
      : goals.map(goalCardHtml).join("")}`;
  content.querySelectorAll(".goal-card").forEach((el) => {
    el.addEventListener("click", () => openGoalSheet(goals.find((g) => g.id === Number(el.dataset.id))));
  });
}

/* Шторка цели: отложить / снять, последние пополнения, изменить или удалить. */
function openGoalSheet(g) {
  const overlay = openSheet(`
    <div class="sheet-title">🐷 ${escapeHtml(g.title)}</div>
    <div class="hint-text" style="margin-bottom:12px;">Накоплено ${fmtMoney(g.saved)} из ${fmtMoney(g.target)}</div>
    <div class="field">
      <label>Сумма (${escapeHtml(state.chat.currency)})</label>
      <input type="number" id="g-amount" min="0" step="0.01" value="${g.monthly_needed !== null && Number(g.monthly_needed) > 0 ? Number(g.monthly_needed) : ""}">
    </div>
    <div class="error-text" id="g-error" style="display:none;"></div>
    <button class="btn" id="g-deposit">➕ Отложить</button>
    <button class="btn secondary" id="g-withdraw" style="margin-top:10px;">➖ Снять</button>
    ${g.deposits.length ? `
      <div class="section-title" style="margin-top:18px;">Пополнения</div>
      ${g.deposits.map((d) => `
        <div class="deposit-row" data-id="${d.id}">
          <span>${d.deposit_date.split("-").reverse().join(".")}</span>
          <span class="${Number(d.amount) >= 0 ? "income-plus" : ""}">${Number(d.amount) >= 0 ? "+" : "−"}${fmtMoney(Math.abs(Number(d.amount)))}</span>
          <button class="link-btn" data-del="${d.id}">✕</button>
        </div>`).join("")}` : ""}
    <button class="btn secondary" id="g-edit" style="margin-top:18px;">✏️ Изменить цель</button>
  `);

  async function move(sign) {
    const errorEl = overlay.querySelector("#g-error");
    const amount = Number(overlay.querySelector("#g-amount").value || 0);
    if (!amount || amount <= 0) { showFormError(errorEl, "Укажите сумму больше нуля"); return; }
    try {
      await api(`/chats/${goalsChatId()}/goals/${g.id}/deposits`, {
        method: "POST", body: { amount: sign * amount, deposit_date: todayISO() },
      });
      haptic("notification", "success");
      closeSheet(overlay);
      await renderGoalsTab();
    } catch (e) {
      showFormError(errorEl, e.message);
    }
  }
  const depositBtn = overlay.querySelector("#g-deposit");
  depositBtn.addEventListener("click", () => move(1));
  useMainButtonFor(overlay, depositBtn, "➕ Отложить");
  overlay.querySelector("#g-withdraw").addEventListener("click", () => move(-1));
  overlay.querySelectorAll("[data-del]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!(await confirmAction("Удалить это пополнение?"))) return;
      try {
        await api(`/chats/${goalsChatId()}/goals/${g.id}/deposits/${btn.dataset.del}`, { method: "DELETE" });
        closeSheet(overlay);
        await renderGoalsTab();
      } catch (e) {
        toast(e.message);
      }
    });
  });
  overlay.querySelector("#g-edit").addEventListener("click", () => {
    closeSheet(overlay);
    openGoalModal(g);
  });
}

function openGoalModal(existing) {
  const isEdit = !!existing;
  const overlay = openSheet(`
    <div class="sheet-title">${isEdit ? "Цель" : "Новая цель"}</div>
    <div class="field">
      <label>На что копим</label>
      <input type="text" id="gm-title" value="${existing ? escapeHtml(existing.title) : ""}" placeholder="Отпуск, подушка безопасности, машина…">
    </div>
    <div class="field-row">
      <div class="field">
        <label>Сколько нужно (${escapeHtml(state.chat.currency)})</label>
        <input type="number" id="gm-target" min="0" step="1" value="${existing ? Number(existing.target) : ""}">
      </div>
      <div class="field">
        <label>К какому месяцу</label>
        <input type="month" id="gm-deadline" value="${existing && existing.deadline ? existing.deadline.slice(0, 7) : ""}">
      </div>
    </div>
    ${isEdit ? `<div class="field"><label><input type="checkbox" id="gm-archived" ${existing.is_archived ? "checked" : ""}> В архиве</label></div>` : ""}
    <div class="hint-text" style="margin-bottom:12px;">Срок необязателен. Если указать — посчитаю, сколько откладывать в месяц.</div>
    <div class="error-text" id="gm-error" style="display:none;"></div>
    <button class="btn" id="gm-submit">${isEdit ? "Сохранить" : "Создать цель"}</button>
    ${isEdit ? '<button class="btn danger" id="gm-delete" style="margin-top:10px;">Удалить цель</button>' : ""}
  `);
  const submit = overlay.querySelector("#gm-submit");
  submit.addEventListener("click", async () => {
    const errorEl = overlay.querySelector("#gm-error");
    const title = overlay.querySelector("#gm-title").value.trim();
    const target = Number(overlay.querySelector("#gm-target").value || 0);
    const month = overlay.querySelector("#gm-deadline").value;
    if (!title) { showFormError(errorEl, "Укажите, на что копим"); return; }
    if (!target || target <= 0) { showFormError(errorEl, "Укажите сумму больше нуля"); return; }
    const body = {
      title, target,
      // Срок — последний день выбранного месяца
      deadline: month ? new Date(Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0)).toISOString().slice(0, 10) : null,
      is_archived: isEdit ? overlay.querySelector("#gm-archived").checked : false,
    };
    if (tg && tg.MainButton) tg.MainButton.showProgress(false);
    try {
      if (isEdit) await api(`/chats/${goalsChatId()}/goals/${existing.id}`, { method: "PATCH", body });
      else await api(`/chats/${goalsChatId()}/goals`, { method: "POST", body });
      haptic("notification", "success");
      closeSheet(overlay);
      await renderGoalsTab();
    } catch (e) {
      showFormError(errorEl, e.message);
    } finally {
      if (tg && tg.MainButton) tg.MainButton.hideProgress();
    }
  });
  useMainButtonFor(overlay, submit, isEdit ? "Сохранить" : "Создать цель");
  const del = overlay.querySelector("#gm-delete");
  if (del) {
    del.addEventListener("click", async () => {
      if (!(await confirmAction("Удалить цель вместе с историей пополнений?"))) return;
      try {
        await api(`/chats/${goalsChatId()}/goals/${existing.id}`, { method: "DELETE" });
        closeSheet(overlay);
        await renderGoalsTab();
      } catch (e) {
        toast(e.message);
      }
    });
  }
}

export { renderGoalsTab, openGoalModal };
