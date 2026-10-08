import { state } from "./state.js";
import { api } from "./api.js";
import { haptic, toast, confirmAction } from "./telegram.js";
import { escapeHtml } from "./format.js";
import { avatarHtml, loadAvatarsIn } from "./members.js";
import { openSheet, openTextPromptModal } from "./sheet.js";

/* Добавление участника без Telegram-аккаунта (например, ребёнка или родственника без
   своего профиля) — открывается прямо из выбора участников в форме траты/повтора, а также
   с вкладки «Баланс» (управление участниками). Такой участник появляется в общем списке
   (state.members) сразу и виден во всех формах чата, но сам открыть мини-апп не сможет — за
   него отмечает кто-то другой из чата. onAdded(member) вызывается один раз при успехе. */
function openAddMemberModal(onAdded) {
  openTextPromptModal({
    title: "Добавить участника",
    hint: "Для тех, у кого нет Telegram — например, ребёнка или родственника. Он появится в списке участников и трат, но не сможет открыть приложение сам — вносить траты за него будет кто-то другой из чата.",
    label: "Имя",
    placeholder: "Например, Мама",
    submitLabel: "Добавить",
    onSubmit: async (fullName) => {
      const member = await api(`/chats/${state.chatId}/members`, {
        method: "POST",
        body: { full_name: fullName },
      });
      state.members.push(member);
      state.members.sort((a, b) => a.full_name.localeCompare(b.full_name, "ru"));
      if (onAdded) onAdded(member);
    },
  });
}

/* Переименование участника без Telegram-аккаунта (опечатка в имени и т.п.) — только для
   таких участников, у Telegram-участников full_name синхронизируется из профиля и бэкенд
   отклонит попытку. onRenamed(member) вызывается один раз при успехе. */
function openRenameMemberModal(member, onRenamed) {
  openTextPromptModal({
    title: "Переименовать участника",
    label: "Имя",
    initialValue: member.full_name,
    submitLabel: "Сохранить",
    onSubmit: async (fullName) => {
      const updated = await api(`/chats/${state.chatId}/members/${member.id}`, {
        method: "PATCH",
        body: { full_name: fullName },
      });
      const idx = state.members.findIndex((m) => m.id === member.id);
      if (idx !== -1) state.members[idx] = updated;
      state.members.sort((a, b) => a.full_name.localeCompare(b.full_name, "ru"));
      if (onRenamed) onRenamed(updated);
    },
  });
}

/* Список всех участников чата с управлением ручными (без Telegram-аккаунта): переименовать
   или удалить (если за ними ещё не числится ни одной траты — иначе бэкенд откажет явным
   сообщением). Настоящие Telegram-участники показаны только для справки, без действий —
   их имя и активность синхронизируются из профиля/событий чата автоматически. */
function openManageMembersModal() {
  const overlay = openSheet(`
    <div class="sheet-title">Участники</div>
    <div id="manage-members-list"></div>
    <button type="button" class="btn secondary" id="manage-add-btn" style="margin-top:12px;">+ Добавить участника без Telegram</button>
  `);

  function renderList() {
    const list = overlay.querySelector("#manage-members-list");
    const sorted = [...state.members].sort((a, b) => a.full_name.localeCompare(b.full_name, "ru"));
    list.innerHTML = sorted.map((m) => `
      <div class="member-manage-row" data-id="${m.id}">
        <div class="who">${avatarHtml(m)}<span>${escapeHtml(m.full_name)}</span>${m.is_manual ? '<span class="manual-tag">без Telegram</span>' : ""}</div>
        ${m.is_manual ? `
          <div class="member-manage-actions">
            <button type="button" class="icon-btn rename-btn" title="Переименовать">✎</button>
            <button type="button" class="icon-btn delete-btn" title="Удалить">🗑</button>
          </div>` : ""}
      </div>`).join("");
    loadAvatarsIn(list);

    list.querySelectorAll(".rename-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = Number(btn.closest(".member-manage-row").dataset.id);
        const member = state.members.find((x) => x.id === id);
        haptic("impact", "light");
        openRenameMemberModal(member, () => renderList());
      });
    });
    list.querySelectorAll(".delete-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = Number(btn.closest(".member-manage-row").dataset.id);
        const member = state.members.find((x) => x.id === id);
        const ok = await confirmAction(`Удалить участника «${member.full_name}»?`);
        if (!ok) return;
        try {
          await api(`/chats/${state.chatId}/members/${id}`, { method: "DELETE" });
          state.members = state.members.filter((x) => x.id !== id);
          haptic("notification", "success");
          renderList();
        } catch (e) {
          toast(e.message);
        }
      });
    });
  }
  renderList();

  overlay.querySelector("#manage-add-btn").addEventListener("click", () => {
    haptic("impact", "light");
    openAddMemberModal(() => renderList());
  });
}

export { openAddMemberModal, openRenameMemberModal, openManageMembersModal };
