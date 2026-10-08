import { state } from "./state.js";
import { escapeHtml } from "./format.js";
import { loadAuthedImage } from "./api.js";

/* Аватарки участников — настоящее фото профиля Telegram, если бот успел его синхронизировать
   (member.avatar_url), иначе кружок с первой буквой имени. Цвет кружка стабильно зависит от
   id участника — просто чтобы разных людей было легче отличить друг от друга на глаз. */
const AVATAR_PALETTE = ["#2481cc", "#34c759", "#ff9500", "#ff3b30", "#af52de", "#5ac8fa", "#ffcc00", "#8e8e93"];

function avatarHtml(member) {
  if (member && member.avatar_url) {
    return `<img class="avatar" data-avatar="${escapeHtml(member.avatar_url)}">`;
  }
  const name = (member && member.full_name) || "?";
  const initial = (name.trim().charAt(0) || "?").toUpperCase();
  const color = AVATAR_PALETTE[Math.abs((member && member.id) || 0) % AVATAR_PALETTE.length];
  return `<div class="avatar-fallback" style="background:${color}">${escapeHtml(initial)}</div>`;
}

function loadAvatarsIn(container) {
  container.querySelectorAll(".avatar[data-avatar]").forEach((img) => {
    loadAuthedImage(img, `/chats/${state.chatId}/${img.dataset.avatar}`);
  });
}

function memberLabel(memberId) {
  const m = state.members.find((x) => x.id === memberId);
  if (!m) return "—";
  return m.username ? `@${m.username}` : m.full_name;
}

function memberById(memberId) {
  return state.members.find((x) => x.id === memberId) || null;
}

/* Маленькая аватарка/инициал + имя вместе, одной строкой — используется везде, где раньше
   был просто escapeHtml(memberLabel(id)): в списке трат («кто оплатил»), на вкладке
   «Баланс» (долги, баланс участников, платежи). Сам escapeHtml уже внутри. */
function memberInlineHtml(memberId) {
  return `<span class="member-inline">${avatarHtml(memberById(memberId))}${escapeHtml(memberLabel(memberId))}</span>`;
}

export { avatarHtml, loadAvatarsIn, memberLabel, memberById, memberInlineHtml };
