import { state } from "../state.js";
import { api } from "../api.js";
import { tg, haptic, toast } from "../telegram.js";
import { fmtMoney, escapeHtml } from "../format.js";
import { loadAvatarsIn, memberLabel, memberInlineHtml } from "../members.js";
import { openSheet, closeSheet, useMainButtonFor, showFormError } from "../sheet.js";
import { openManageMembersModal } from "../member-modals.js";
import { hideCurrentChat } from "../shell.js";

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
          <div class="who">${memberInlineHtml(d.from_member_id)} → ${memberInlineHtml(d.to_member_id)}</div>
          <div style="display:flex; align-items:center; gap:10px;">
            <b>${fmtMoney(d.amount)}</b>
            <button class="btn small settle-btn" data-idx="${i}">Погасить</button>
          </div>
        </div>`).join("")}
    ${debts.length > 0 ? '<button type="button" class="btn secondary small" id="settle-up-btn" style="margin-top:4px;">🤝 Позвать рассчитаться в чат</button>' : ""}

    <div class="section-title">Баланс участников</div>
    <div class="card">
      ${state.balances.balances.map((b) => `
        <div class="balance-row">
          ${memberInlineHtml(b.member_id)}
          <span class="${Number(b.net) >= 0 ? "positive" : "negative"}">${Number(b.net) >= 0 ? "+" : ""}${fmtMoney(b.net)}</span>
        </div>`).join("")}
    </div>
    <button type="button" class="btn secondary small" id="manage-members-btn" style="margin-top:8px;">Участники чата</button>

    ${state.settlements.length > 0 ? `
      <div class="section-title">Последние платежи</div>
      <div class="card">
        ${state.settlements.slice(0, 10).map((s) => `
          <div class="balance-row">
            <span>${memberInlineHtml(s.from_member_id)} → ${memberInlineHtml(s.to_member_id)}</span>
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
  const settleUpBtn = content.querySelector("#settle-up-btn");
  if (settleUpBtn) {
    settleUpBtn.addEventListener("click", async () => {
      haptic("impact", "light");
      settleUpBtn.disabled = true;
      try {
        await api(`/chats/${state.chatId}/settle-up`, { method: "POST" });
        haptic("notification", "success");
        const done = "Отправил в чат список переводов с кнопками «Перевёл(а)»";
        if (tg && tg.showAlert) tg.showAlert(done); else alert(done);
      } catch (e) {
        toast(e.message);
      }
      settleUpBtn.disabled = false;
    });
  }
  content.querySelector("#manage-members-btn").addEventListener("click", () => {
    haptic("impact", "light");
    openManageMembersModal();
  });
  loadAvatarsIn(content);
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
      // Долгов не осталось — спрашиваем, нужен ли ещё чат в списке (сами не скрываем:
      // семейный чат обычно оставляют, разовый с друзьями — убирают)
      if (state.balances.simplified_debts.length === 0) {
        await hideCurrentChat(state.switchSpace, "Все в расчёте 🎉 Убрать этот чат из списка?");
      }
    } catch (e) {
      showFormError(errorEl, e.message);
    } finally {
      if (tg && tg.MainButton) tg.MainButton.hideProgress();
    }
  });
  useMainButtonFor(overlay, overlay.querySelector("#s-submit"), "Подтвердить");
}

export { renderBalanceTab };
