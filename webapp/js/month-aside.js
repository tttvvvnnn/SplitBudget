/* Колонка «Сводка месяца» справа от списка трат — только на широком экране (веб-версия,
   см. @media (min-width: 1000px) в style.css). На телефоне её нет, и запросы не делаются. */

import { state, isPersonal } from "./state.js";
import { api } from "./api.js";
import { categoryIcon, escapeHtml, fmtMoney, todayMonth } from "./format.js";
import { memberInlineHtml } from "./members.js";
import { shareMode } from "./tabs/all.js";
import { PALETTE, OTHER_COLOR, deltaHtml } from "./tabs/stats.js";

const WIDE = window.matchMedia("(min-width: 1000px)");
const TOP_CATEGORIES = 6;

function isWide() {
  return WIDE.matches;
}

let renderSeq = 0;

/* el — пустой <aside>, onOpenTab(tab) — переход на вкладку («Статистика», «Баланс»). */
async function renderMonthAside(el, onOpenTab) {
  if (!el || !isWide()) return;
  const seq = ++renderSeq;
  const personal = isPersonal();
  const total = personal && shareMode() === "total";
  const isCurrent = state.month === todayMonth();
  const forecastChat = personal ? (total ? null : state.personalChatId) : state.chatId;
  let stats, forecast;
  try {
    [stats, forecast] = await Promise.all([
      personal
        ? api(`/all/stats?month=${state.month}&full=${total}`)
        : api(`/chats/${state.chatId}/stats?month=${state.month}`),
      isCurrent && forecastChat ? api(`/chats/${forecastChat}/forecast`).catch(() => null) : null,
    ]);
  } catch (e) {
    if (seq === renderSeq) el.innerHTML = "";
    return;
  }
  if (seq !== renderSeq || !el.isConnected) return;

  const cur = Number(stats.total);
  const base = stats.prev_to_date !== null ? Number(stats.prev_to_date) : Number(stats.prev_total);
  const cats = stats.by_category.slice(0, TOP_CATEGORIES);
  const max = cats.length ? Number(cats[0].total) : 0;

  el.innerHTML = `
    <div class="card aside-card">
      <div class="aside-title">Итог месяца</div>
      <div class="aside-total">${fmtMoney(cur)}</div>
      <div class="aside-sub">
        ${stats.count} ${plural(stats.count, "трата", "траты", "трат")}
        ${base ? ` · ${deltaHtml(cur, base, stats.prev_to_date !== null ? "к тому же дню" : "к прошлому месяцу")}` : ""}
      </div>
      ${forecast ? `
        <div class="aside-forecast">
          <span>Прогноз на конец месяца</span>
          <b>${fmtMoney(forecast.forecast)}</b>
        </div>` : ""}
    </div>
    ${cats.length ? `
      <div class="card aside-card aside-link" data-open="stats">
        <div class="aside-title">По категориям <span class="chevron">›</span></div>
        ${cats.map((c, i) => `
          <div class="aside-cat">
            <div class="aside-cat-head">
              <span>${categoryIcon(c.category)} ${escapeHtml(c.category)}</span>
              <b>${fmtMoney(c.total)}</b>
            </div>
            <div class="aside-bar"><div style="width:${max ? (Number(c.total) / max) * 100 : 0}%;background:${PALETTE[i] || OTHER_COLOR}"></div></div>
          </div>`).join("")}
      </div>` : ""}
    ${!personal && state.balances ? balanceHtml() : ""}`;

  el.querySelectorAll("[data-open]").forEach((card) => {
    card.addEventListener("click", () => onOpenTab && onOpenTab(card.dataset.open));
  });
}

function balanceHtml() {
  const rows = state.balances.balances.filter((b) => Math.abs(Number(b.net)) > 0.005);
  return `
    <div class="card aside-card aside-link" data-open="balance">
      <div class="aside-title">Баланс <span class="chevron">›</span></div>
      ${rows.length ? rows.map((b) => `
        <div class="balance-row">
          ${memberInlineHtml(b.member_id)}
          <span class="${Number(b.net) >= 0 ? "positive" : "negative"}">${Number(b.net) >= 0 ? "+" : ""}${fmtMoney(b.net)}</span>
        </div>`).join("") : '<div class="hint-text">Все в расчёте 🎉</div>'}
    </div>`;
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

export { renderMonthAside, isWide };
