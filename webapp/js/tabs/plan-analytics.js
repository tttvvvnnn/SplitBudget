import { state } from "../state.js";
import { api } from "../api.js";
import { toast } from "../telegram.js";
import { fmtMoney, escapeHtml } from "../format.js";
import { openSheet } from "../sheet.js";

/* ---------------- Аналитика «Плана» ----------------
   Две шторки из сводки плана (app/shared/plan_analytics.py):
   «🌊 Куда ушли деньги» — диаграмма Санки месяца плана: доходы и перенос → статьи → категории
   трат, отложенное и остаток; перерасход статей — красный источник слева.
   «📊 План за полгода» — каждая статья по месяцам: сколько выделенного потрачено, сколько раз
   ушла в минус и, если это обычное дело, какой процент покрыл бы её траты. */

const MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
const ITEM_COLORS = ["#2481cc", "#af52de", "#ff9500", "#5ac8fa", "#ff2d55", "#30b0c7", "#a2845e", "#5856d6", "#00c7be", "#7d9c3a"];
const KIND_COLORS = { income: "#34c759", over: "#ff3b30", free: "#8e8e93", goal: "#ff9500", left: "#34c759", category: "#8e8e93" };
const NODE_W = 8;
const NODE_PAD = 6;

function planChatId() {
  return state.personalChatId;
}

function compact(v) {
  const abs = Math.abs(v);
  if (abs >= 1000000) return `${+(v / 1000000).toFixed(1)}M`;
  if (abs >= 1000) return `${+(v / 1000).toFixed(1)}k`;
  return String(Math.round(v));
}

function shortDay(iso) {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS_SHORT[m - 1]}`;
}

function svgText(s) {
  return escapeHtml(s);
}

/* Раскладка Санки: три колонки, высота узла — его сумма (вход или выход, что больше), связи —
   полосы между узлами в порядке узлов. Подпись узла — две строки (название и сумма), поэтому
   у каждого узла место не меньше LABEL_SLOT: мелкие статьи не налезают друг на друга.
   Без библиотек: SVG рисуется строкой. */
const LABEL_SLOT = 28;
const TITLE_MAX = 16;

function shortTitle(title) {
  return title.length > TITLE_MAX ? `${title.slice(0, TITLE_MAX - 1)}…` : title;
}

function sankeySvg(data, width) {
  const nodes = data.nodes.map((n) => ({ ...n, in: 0, out: 0 }));
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const links = data.links
    .map((l) => ({ ...l, value: Number(l.value), s: byId.get(l.source), t: byId.get(l.target) }))
    .filter((l) => l.s && l.t && l.value > 0);
  links.forEach((l) => { l.s.out += l.value; l.t.in += l.value; });
  nodes.forEach((n) => { n.value = Math.max(n.in, n.out); });
  const columns = [0, 1, 2].map((c) => nodes.filter((n) => n.column === c && n.value > 0));
  let itemIndex = 0;
  nodes.forEach((n) => {
    n.color = n.column === 1 && n.kind === "spend" ? ITEM_COLORS[itemIndex++ % ITEM_COLORS.length] : KIND_COLORS[n.kind] || "#8e8e93";
    if (n.id === "carried") n.color = "#30b0c7";
  });
  // Масштаб — по самой «полной» колонке, чтобы суммы во всех колонках были в одном масштабе
  const target = 240;
  const scale = Math.min(...columns.filter((c) => c.length).map((c) => {
    const total = c.reduce((a, n) => a + n.value, 0);
    return Math.max(target - NODE_PAD * (c.length - 1), 40) / total;
  }));
  const xs = [0, Math.round(width * 0.33), Math.round(width * 0.62)];
  let height = 0;
  columns.forEach((col, ci) => {
    let y = 0;
    col.forEach((n) => {
      n.x = xs[ci];
      n.h = Math.max(n.value * scale, 2);
      const slot = Math.max(n.h, LABEL_SLOT);
      n.y = y + (slot - n.h) / 2;
      y += slot + NODE_PAD;
    });
    height = Math.max(height, y - NODE_PAD);
  });
  // Порядок связей у узла — по положению узла на другом конце, чтобы полосы не перекрещивались
  nodes.forEach((n) => { n.sy = n.y; n.ty = n.y; });
  [...links].sort((a, b) => a.t.y - b.t.y).forEach((l) => { l.y0 = l.s.sy; l.s.sy += l.value * scale; });
  [...links].sort((a, b) => a.s.y - b.s.y).forEach((l) => { l.y1 = l.t.ty; l.t.ty += l.value * scale; });

  const paths = links.map((l) => {
    const h = Math.max(l.value * scale, 1);
    const x0 = l.s.x + NODE_W;
    const x1 = l.t.x;
    const xm = (x0 + x1) / 2;
    const d = `M${x0},${l.y0} C${xm},${l.y0} ${xm},${l.y1} ${x1},${l.y1} L${x1},${l.y1 + h} C${xm},${l.y1 + h} ${xm},${l.y0 + h} ${x0},${l.y0 + h} Z`;
    const color = l.s.column === 0 ? l.t.color : l.s.color;
    return `<path d="${d}" fill="${color}" fill-opacity="0.26"><title>${svgText(`${l.s.title} → ${l.t.title}: ${fmtMoney(l.value)}`)}</title></path>`;
  }).join("");
  const rects = nodes.filter((n) => n.value > 0).map((n) => {
    const tx = n.x + NODE_W + 5;
    const cy = n.y + n.h / 2;
    return `
      <rect x="${n.x}" y="${n.y}" width="${NODE_W}" height="${n.h}" rx="2" fill="${n.color}"><title>${svgText(`${n.title}: ${fmtMoney(n.value)}`)}</title></rect>
      <text x="${tx}" y="${cy - 6}" dominant-baseline="middle" class="sankey-label">${svgText(shortTitle(n.title))}</text>
      <text x="${tx}" y="${cy + 7}" dominant-baseline="middle" class="sankey-label sankey-value">${compact(n.value)}</text>`;
  }).join("");
  return `<svg class="sankey" viewBox="0 -4 ${width} ${height + 8}" width="100%" height="${height + 8}" role="img"
    aria-label="Куда ушли деньги">${paths}${rects}</svg>`;
}

async function openPlanFlow(plan) {
  const overlay = openSheet(`
    <div class="sheet-title">🌊 Куда ушли деньги</div>
    <div class="hint-text">${shortDay(plan.start)} – ${shortDay(plan.end)}</div>
    <div class="card sankey-card" id="flow-box"><div class="loading">Загрузка…</div></div>`);
  const box = overlay.querySelector("#flow-box");
  try {
    const data = await api(`/chats/${planChatId()}/plan/flow?date=${plan.start}`);
    if (!data.links.length) {
      box.innerHTML = '<div class="hint-text">В этом месяце плана ещё не было ни доходов, ни трат.</div>';
      return;
    }
    const width = Math.max(box.clientWidth - 24, 280);
    const income = Number(data.income) + Math.max(Number(data.carried), 0);
    box.innerHTML = `
      ${sankeySvg(data, width)}
      <div class="hint-text sankey-note">Доходы${Number(data.carried) > 0 ? " и перенос" : ""}: ${fmtMoney(income)} · потрачено и отложено ${fmtMoney(data.spent)}.
      Наведите или нажмите на полосу — покажет сумму.</div>`;
  } catch (e) {
    box.innerHTML = `<div class="error-text">${escapeHtml(e.message)}</div>`;
  }
}

/* Ячейка месяца статьи: потрачено из выделенного. Зелёная — в рамках, жёлтая — почти всё,
   красная — минус, бледная — месяц ещё идёт, пустая — статьи не было. */
function cellHtml(allocated, spent, period) {
  if (allocated === null || allocated === undefined) return '<span class="ph-cell none"></span>';
  const a = Number(allocated);
  const s = Number(spent);
  const pct = a > 0 ? Math.round((s / a) * 100) : (s > 0 ? 999 : 0);
  const level = pct > 100 ? "over" : pct >= 85 ? "warn" : "ok";
  return `<span class="ph-cell ${level} ${period.finished ? "" : "partial"}" title="${fmtMoney(s)} из ${fmtMoney(a)}">${pct > 999 ? "∞" : pct}%</span>`;
}

async function openPlanHistory(plan) {
  const overlay = openSheet(`
    <div class="sheet-title">📊 План за полгода</div>
    <div class="hint-text">Сколько выделенного на статью потрачено в каждом месяце плана</div>
    <div class="card ph-card" id="ph-box"><div class="loading">Загрузка…</div></div>`);
  const box = overlay.querySelector("#ph-box");
  try {
    const data = await api(`/chats/${planChatId()}/plan/history?date=${plan.start}`);
    const periods = data.periods;
    const shown = periods.map((p, i) => i).filter((i) => periods[i].active);
    if (!shown.length || !data.items.length) {
      box.innerHTML = '<div class="hint-text">Истории пока нет — план появился недавно.</div>';
      return;
    }
    const head = shown.map((i) => `<span class="ph-month">${MONTHS_SHORT[Number(periods[i].start.slice(5, 7)) - 1]}</span>`).join("");
    box.innerHTML = `
      <div class="ph-row ph-head" style="--n:${shown.length}"><span></span>${head}</div>
      ${data.items.map((it) => `
        <div class="ph-item">
          <div class="ph-row" style="--n:${shown.length}">
            <span class="ph-title">${escapeHtml(it.title)}</span>
            ${shown.map((i) => cellHtml(it.allocated[i], it.spent[i], periods[i])).join("")}
          </div>
          ${it.months && it.kind === "spend" ? `<div class="hint-text ph-note">${it.over ? `в минус ${it.over} из ${it.months}` : `в рамках все ${it.months}`}${it.suggested_percent !== null && it.suggested_percent !== undefined
            ? ` · обычно нужно ~${Number(it.suggested_percent).toLocaleString("ru-RU")}% вместо ${Number(it.percent).toLocaleString("ru-RU")}%` : ""}</div>` : ""}
        </div>`).join("")}
      <div class="hint-text ph-legend"><span class="ph-cell ok">%</span> в рамках · <span class="ph-cell warn">%</span> почти всё · <span class="ph-cell over">%</span> минус · бледные — месяц ещё идёт</div>`;
  } catch (e) {
    box.innerHTML = `<div class="error-text">${escapeHtml(e.message)}</div>`;
    toast(e.message);
  }
}

export { openPlanFlow, openPlanHistory };
