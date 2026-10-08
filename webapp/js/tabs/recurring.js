import { state, isPersonal, hideFamilyOnly } from "../state.js";
import { api } from "../api.js";
import { tg, haptic, toast, confirmAction } from "../telegram.js";
import { fmtMoney, escapeHtml } from "../format.js";
import { memberLabel } from "../members.js";
import { openSheet, closeSheet, useMainButtonFor, showFormError } from "../sheet.js";
import { openAddMemberModal } from "../member-modals.js";
import { categoryFieldsHtml, bindCategoryFields, readCategoryFields, categoryBadgeHtml } from "../category-fields.js";

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
      Раз в месяц, в указанный день, такая трата добавляется автоматически${isPersonal() ? "." : ", и в чат приходит уведомление."}
    </div>
    ${state.recurring.length === 0 ? '<div class="empty-state">Повторяющихся трат пока нет.<br>Нажмите «+», чтобы добавить (например, аренду).</div>' : state.recurring.map((r) => `
      <div class="card expense-card" data-id="${r.id}">
        <div class="expense-thumb placeholder">${r.is_active ? "🔁" : "⏸"}</div>
        <div class="expense-main">
          <div class="expense-title">${escapeHtml(r.title)}</div>
          <div class="expense-meta">
            ${categoryBadgeHtml(r.category, r.subcategory)}
            каждое ${r.day_of_month} число${isPersonal() ? "" : ` · ${escapeHtml(memberLabel(r.payer_member_id))}`}
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
    ${categoryFieldsHtml("r", existing && existing.category, existing && existing.subcategory)}
    <div class="field">
      <label>День месяца (1–28)</label>
      <input type="number" id="r-day" min="1" max="28" value="${existing ? existing.day_of_month : 1}">
    </div>
    <div class="field" data-family-only>
      <label>Кто платит</label>
      <select id="r-payer">
        ${state.members.map((m) => `<option value="${m.id}" ${(existing ? existing.payer_member_id : state.member.id) === m.id ? "selected" : ""}>${escapeHtml(m.full_name)}</option>`).join("")}
      </select>
    </div>
    <div class="field" data-family-only>
      <label>Как делить</label>
      <div class="split-toggle">
        <div data-v="equal" class="${splitType === "equal" ? "active" : ""}">Поровну</div>
        <div data-v="custom" class="${splitType === "custom" ? "active" : ""}">Вручную</div>
      </div>
    </div>
    <div class="field" id="r-participants-block" data-family-only></div>
    ${isEdit ? `<div class="field"><label><input type="checkbox" id="r-active" ${existing.is_active ? "checked" : ""}> Активна</label></div>` : ""}
    <div class="error-text" id="r-error" style="display:none;"></div>
    <button class="btn" id="r-submit">${isEdit ? "Сохранить" : "Добавить"}</button>
    ${isEdit ? '<button class="btn danger" id="r-delete" style="margin-top:10px;">Удалить шаблон</button>' : ""}
  `);

  hideFamilyOnly(overlay);
  bindCategoryFields(overlay, "r");

  function currentAmount() { return Number(overlay.querySelector("#r-amount").value || 0); }

  function refreshPayerOptions() {
    const select = overlay.querySelector("#r-payer");
    const current = select.value;
    select.innerHTML = state.members.map((m) => `<option value="${m.id}" ${String(m.id) === current ? "selected" : ""}>${escapeHtml(m.full_name)}</option>`).join("");
  }

  function renderParticipants() {
    const block = overlay.querySelector("#r-participants-block");
    if (splitType === "equal") {
      block.innerHTML = `
        <label>Участники</label>
        <div class="chip-row">
          ${state.members.map((m) => `<div class="chip ${selectedIds.has(m.id) ? "selected" : ""}" data-id="${m.id}">${escapeHtml(m.full_name)}</div>`).join("")}
          <div class="chip add-chip" id="r-add-participant-chip">+ Добавить</div>
        </div>`;
      block.querySelectorAll(".chip[data-id]").forEach((chip) => {
        chip.addEventListener("click", () => {
          haptic("selection");
          const id = Number(chip.dataset.id);
          if (selectedIds.has(id)) selectedIds.delete(id); else selectedIds.add(id);
          chip.classList.toggle("selected");
        });
      });
      block.querySelector("#r-add-participant-chip").addEventListener("click", () => {
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
            <div class="name">${escapeHtml(m.full_name)}</div>
            <input type="number" min="0" step="0.01" data-id="${m.id}" class="r-custom-amount" value="${customAmounts[m.id] || ""}">
          </div>`).join("")}
        <div class="hint-text" id="r-sum-hint">Указано: ${sum.toFixed(2)} из ${amount.toFixed(2)} ${state.chat.currency}</div>
        <button type="button" class="btn secondary small" id="r-add-participant-btn" style="margin-top:8px;">+ Добавить участника</button>`;
      block.querySelectorAll(".r-custom-amount").forEach((inp) => {
        inp.addEventListener("input", () => {
          customAmounts[Number(inp.dataset.id)] = Number(inp.value || 0);
          const s = state.members.reduce((acc, m) => acc + (customAmounts[m.id] || 0), 0);
          block.querySelector("#r-sum-hint").textContent = `Указано: ${s.toFixed(2)} из ${currentAmount().toFixed(2)} ${state.chat.currency}`;
        });
      });
      block.querySelector("#r-add-participant-btn").addEventListener("click", () => {
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
    const { category, subcategory } = readCategoryFields(overlay, "r");
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
      title, amount, category, subcategory, payer_member_id: payerId,
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

export { renderRecurringTab, openRecurringModal };
