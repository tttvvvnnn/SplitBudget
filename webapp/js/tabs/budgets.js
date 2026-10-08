import { state, isAll, isPersonal } from "../state.js";
import { api } from "../api.js";
import { tg, haptic, toast, confirmAction } from "../telegram.js";
import { categoryIcon, monthLabel, shiftMonth, todayMonth, fmtMoney, escapeHtml } from "../format.js";
import { openSheet, closeSheet, useMainButtonFor, showFormError } from "../sheet.js";

/* ---------------- Вкладка «Лимиты» ----------------
   Месячные лимиты на весь месяц, категорию или подкатегорию (app/shared/budgets.py). В
   семейном чате — общие лимиты чата по полным суммам трат. В «Моих финансах» и «Все траты»
   — личные лимиты (хранятся в личном пространстве): считается доля пользователя во всех его
   тратах, и личных, и семейных. При 80% и 100% бот присылает уведомление. */

function budgetsChatId() {
  return isAll() || isPersonal() ? state.personalChatId : state.chatId;
}

async function renderBudgetsTab() {
  const content = document.getElementById("content");
  let budgets;
  try {
    budgets = await api(`/chats/${budgetsChatId()}/budgets?month=${state.month}`);
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
  content.querySelectorAll(".budget-card").forEach((el) => {
    el.addEventListener("click", () => openBudgetModal(budgets.find((b) => b.id === Number(el.dataset.id))));
  });
}

function budgetCardHtml(b) {
  const amount = Number(b.amount);
  const spent = Number(b.spent);
  const pct = amount > 0 ? (spent / amount) * 100 : 0;
  const level = pct >= 100 ? "over" : pct >= 80 ? "warn" : "ok";
  const left = amount - spent;
  let sub = left >= 0 ? `осталось ${fmtMoney(left)}` : `превышен на ${fmtMoney(-left)}`;
  // Для текущего месяца — сколько можно тратить в день до конца месяца
  if (left > 0 && state.month === todayMonth()) {
    const now = new Date();
    const daysLeft = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate() - now.getDate() + 1;
    sub += ` · ~${fmtMoney(left / daysLeft)} в день`;
  }
  const icon = b.category ? categoryIcon(b.category) : "📅";
  return `
    <div class="card budget-card" data-id="${b.id}">
      <div class="budget-head">
        <span>${icon} ${escapeHtml(b.label)}</span>
        <span class="budget-pct ${level}">${Math.round(pct)}%</span>
      </div>
      <div class="budget-bar"><div class="budget-fill ${level}" style="width:${Math.min(pct, 100)}%"></div></div>
      <div class="budget-sub">
        <span>${fmtMoney(spent)} из ${fmtMoney(amount)}</span>
        <span class="hint-text">${escapeHtml(sub)}</span>
      </div>
    </div>`;
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
