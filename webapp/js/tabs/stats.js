import { state, isPersonal } from "../state.js";
import { api } from "../api.js";
import { haptic } from "../telegram.js";
import { categoryIcon, monthLabel, shiftMonth, todayMonth, fmtMoney, escapeHtml } from "../format.js";
import { openSheet } from "../sheet.js";
import { shareMode, shareToggleHtml, bindShareToggle } from "./all.js";

/* ---------------- Вкладка «Статистика» ----------------
   Сверху итог месяца и сравнение с прошлым, в «Моих финансах» — доходы против расходов.
   Дальше пончик по категориям (итог в центре) и список категорий с долей и изменением к
   прошлому месяцу; тап по категории открывает шторку с подкатегориями и самими тратами.
   Ниже — траты нарастающим итогом по дням против прошлого месяца с прогнозом и столбики
   итогов за 6 или 12 месяцев. Данные — app/shared/stats.py. */

const PALETTE = ["#2481cc", "#34c759", "#ff9500", "#ff3b30", "#af52de", "#5ac8fa", "#ffcc00", "#8e8e93"];
const MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
const charts = {};
let trendMonths = 6;
// Номер текущей отрисовки: если пока грузились данные, пользователь успел сменить месяц или
// пространство, устаревший ответ не должен перерисовать вкладку поверх нового
let renderSeq = 0;

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function monthShort(ym) {
  return MONTHS_SHORT[Number(ym.split("-")[1]) - 1];
}

/* «к сентябрю» — дательный падеж прошлого месяца */
const MONTHS_DATIVE = ["январю", "февралю", "марту", "апрелю", "маю", "июню", "июлю", "августу", "сентябрю", "октябрю", "ноябрю", "декабрю"];

/* Изменение к прошлому месяцу: рост трат — красным, снижение — зелёным. */
function deltaHtml(cur, prev, { long = false } = {}) {
  if (!prev) return "";
  const pct = Math.round(((cur - prev) / prev) * 100);
  if (pct === 0) return `<span class="delta same">${long ? "как в прошлом месяце" : "0%"}</span>`;
  const cls = pct > 0 ? "up" : "down";
  const label = `${pct > 0 ? "▲" : "▼"} ${Math.abs(pct)}%`;
  const to = long ? ` к ${MONTHS_DATIVE[Number(shiftMonth(state.month, -1).split("-")[1]) - 1]}` : "";
  return `<span class="delta ${cls}">${label}${to}</span>`;
}

function destroyCharts() {
  for (const key of Object.keys(charts)) {
    charts[key].destroy();
    delete charts[key];
  }
}

async function renderStatsTab() {
  const seq = ++renderSeq;
  const personal = isPersonal();
  const total = shareMode() === "total";
  const isCurrent = state.month === todayMonth();
  let stats, forecast = null, income = null;
  try {
    // В «Моих финансах» — личные траты и сколько потрачено в семейных (или их полные суммы).
    // Прогноз сервер считает по «Моим тратам», поэтому в режиме «Всего» его не показываем.
    const forecastChat = personal ? (total ? null : state.personalChatId) : state.chatId;
    [stats, forecast, income] = await Promise.all([
      personal
        ? api(`/all/stats?month=${state.month}&full=${total}`)
        : api(`/chats/${state.chatId}/stats?month=${state.month}`),
      isCurrent && forecastChat ? api(`/chats/${forecastChat}/forecast`).catch(() => null) : null,
      personal ? api(`/chats/${state.personalChatId}/income?month=${state.month}`).catch(() => null) : null,
    ]);
  } catch (e) {
    const box = document.getElementById("content");
    if (seq === renderSeq && box) box.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    return;
  }
  // шапка могла перерисоваться (смена пространства) — берём #content уже после загрузки
  const content = document.getElementById("content");
  if (seq !== renderSeq || !content) return;

  const cur = Number(stats.total);
  const empty = stats.by_category.length === 0;
  destroyCharts();
  content.innerHTML = `
    <div class="month-picker">
      <button data-dir="-1" aria-label="Предыдущий месяц">‹</button>
      <div class="label">${monthLabel(state.month)}</div>
      <button data-dir="1" aria-label="Следующий месяц">›</button>
    </div>
    ${personal ? shareToggleHtml() : ""}
    <div class="total-line">${fmtMoney(cur)}</div>
    <div class="total-delta">${deltaHtml(cur, Number(stats.prev_total), { long: true })}</div>
    ${incomeHtml(income)}
    ${empty ? '<div class="empty-state">Нет трат за этот месяц.</div>' : `
      <div class="stats-split">
      <div class="chart-wrap donut-wrap">
        <div class="chart-box donut-box"><canvas id="stats-donut"></canvas></div>
        <div class="donut-center">
          <div class="hint-text">${stats.by_category.length} ${plural(stats.by_category.length, "категория", "категории", "категорий")}</div>
          <b>${fmtMoney(cur)}</b>
        </div>
      </div>
      <div class="card stats-list">
        ${stats.by_category.map((c, i) => categoryRowHtml(c, i, cur)).join("")}
      </div>
      </div>
      <div class="section-title">По дням</div>
      <div class="chart-wrap">
        <div class="chart-legend">
          <span><i class="dot" style="background:${cssVar("--link")}"></i>${monthShort(state.month)}</span>
          <span><i class="dot dashed"></i>${monthShort(shiftMonth(state.month, -1))}</span>
          ${forecast ? `<span><i class="dot dashed accent"></i>прогноз ${fmtMoney(forecast.forecast)}</span>` : ""}
        </div>
        <div class="chart-box"><canvas id="stats-daily"></canvas></div>
      </div>`}
    <div class="section-title trend-title">
      <span>По месяцам</span>
      <div class="share-toggle trend-toggle">
        <button data-months="6" class="${trendMonths === 6 ? "active" : ""}">6 мес</button>
        <button data-months="12" class="${trendMonths === 12 ? "active" : ""}">12 мес</button>
      </div>
    </div>
    <div class="chart-wrap"><div class="chart-box short"><canvas id="stats-trend"></canvas></div></div>
  `;

  content.querySelectorAll(".stats-cat").forEach((row) => {
    row.addEventListener("click", () => {
      haptic("selection");
      openCategorySheet(stats.by_category[Number(row.dataset.idx)], Number(row.dataset.idx));
    });
  });
  content.querySelectorAll(".trend-toggle button").forEach((b) => {
    b.addEventListener("click", () => {
      haptic("selection");
      trendMonths = Number(b.dataset.months);
      content.querySelectorAll(".trend-toggle button").forEach((x) => x.classList.toggle("active", x === b));
      drawTrend(stats);
    });
  });
  bindShareToggle(content, () => renderStatsTab());
  content.querySelectorAll(".month-picker button").forEach((b) => {
    b.addEventListener("click", async () => {
      haptic("selection");
      state.month = shiftMonth(state.month, Number(b.dataset.dir));
      await renderStatsTab();
    });
  });

  if (!window.Chart) return;
  if (!empty) {
    drawDonut(stats);
    drawDaily(stats, forecast, isCurrent);
  }
  drawTrend(stats);
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
}

/* Доходы против расходов месяца (только «Мои финансы»; расходы — ваши траты, как в «Доходах»). */
function incomeHtml(income) {
  if (!income || Number(income.received) <= 0) return "";
  const received = Number(income.received);
  const spent = Number(income.spent);
  const left = received - spent;
  const pct = Math.round((left / received) * 100);
  return `
    <div class="card income-vs">
      <div class="income-vs-bar">
        <div style="width:${Math.min(100, (spent / received) * 100)}%"></div>
      </div>
      <div class="income-vs-rows">
        <div><span class="hint-text">Доходы</span><b>${fmtMoney(received)}</b></div>
        <div><span class="hint-text">Расходы</span><b>${fmtMoney(spent)}</b></div>
        <div><span class="hint-text">${left >= 0 ? `Осталось · ${pct}%` : "Перерасход"}</span>
          <b class="${left >= 0 ? "positive" : "negative"}">${fmtMoney(Math.abs(left))}</b></div>
      </div>
    </div>`;
}

function categoryRowHtml(c, idx, total) {
  const amount = Number(c.total);
  const share = total ? (amount / total) * 100 : 0;
  const color = PALETTE[idx % PALETTE.length];
  return `
    <div class="stats-cat" data-idx="${idx}">
      <div class="stats-cat-head">
        <span class="stats-cat-name"><i class="dot" style="background:${color}"></i>${categoryIcon(c.category)} ${escapeHtml(c.category)} <span class="hint-text">${Math.round(share)}%</span></span>
        <span class="stats-cat-amount">${fmtMoney(amount)}</span>
      </div>
      <div class="stats-cat-sub">
        <div class="stats-cat-bar"><div style="width:${share}%;background:${color}"></div></div>
        ${deltaHtml(amount, Number(c.prev_total))}
      </div>
    </div>`;
}

/* Шторка категории: подкатегории и траты месяца в ней. */
async function openCategorySheet(c, idx) {
  const color = PALETTE[idx % PALETTE.length];
  const subs = c.subcategories.filter((s) => s.subcategory);
  const overlay = openSheet(`
    <div class="sheet-title"><i class="dot big" style="background:${color}"></i>${categoryIcon(c.category)} ${escapeHtml(c.category)}</div>
    <div class="hint-text">${fmtMoney(c.total)} · ${c.count} ${plural(c.count, "трата", "траты", "трат")}
      ${Number(c.prev_total) ? ` · в прошлом месяце ${fmtMoney(c.prev_total)}` : ""}</div>
    ${subs.length ? `
      <div class="section-title">Подкатегории</div>
      <div class="card">${c.subcategories.map((s) => `
        <div class="balance-row">
          <span>${escapeHtml(s.subcategory || "Без подкатегории")} <span class="hint-text">(${s.count})</span></span>
          <span>${fmtMoney(s.total)}</span>
        </div>`).join("")}</div>` : ""}
    <div class="section-title">Траты</div>
    <div class="card" id="cat-expenses"><div class="loading">Загрузка…</div></div>`);

  const box = overlay.querySelector("#cat-expenses");
  try {
    const personal = isPersonal();
    const items = personal
      ? await api(`/all/expenses?month=${state.month}`)
      : await api(`/chats/${state.chatId}/expenses?month=${state.month}`);
    const amountOf = (e) => (personal && shareMode() !== "total" ? Number(e.my_share) : Number(e.amount));
    const rows = items.filter((e) => e.category === c.category && amountOf(e) !== 0);
    box.innerHTML = rows.length ? rows.map((e) => `
      <div class="balance-row cat-expense">
        <span class="cat-expense-title">
          <span class="hint-text">${shortDate(e.expense_date)}</span>
          ${escapeHtml(e.title)}${e.subcategory ? ` <span class="hint-text">· ${escapeHtml(e.subcategory)}</span>` : ""}
        </span>
        <span>${fmtMoney(amountOf(e))}</span>
      </div>`).join("") : '<div class="hint-text">Нет трат.</div>';
  } catch (e) {
    box.innerHTML = `<div class="error-text">${escapeHtml(e.message)}</div>`;
  }
}

function shortDate(iso) {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS_SHORT[m - 1]}`;
}

function baseOptions() {
  const hint = cssVar("--hint");
  const grid = cssVar("--border");
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 300 },
    plugins: {
      legend: { display: false },
      tooltip: { callbacks: { label: (ctx) => ` ${fmtMoney(ctx.parsed.y ?? ctx.parsed)}` } },
    },
    scales: {
      x: { grid: { display: false }, border: { display: false }, ticks: { color: hint, maxRotation: 0, autoSkipPadding: 10, font: { size: 11 } } },
      y: {
        grid: { color: grid }, border: { display: false }, beginAtZero: true,
        ticks: { color: hint, maxTicksLimit: 4, font: { size: 11 }, callback: (v) => compactMoney(v) },
      },
    },
  };
}

function compactMoney(v) {
  if (v >= 1000000) return `${+(v / 1000000).toFixed(1)}M`;
  if (v >= 1000) return `${+(v / 1000).toFixed(1)}k`;
  return String(v);
}

function drawDonut(stats) {
  charts.donut = new Chart(document.getElementById("stats-donut"), {
    type: "doughnut",
    data: {
      labels: stats.by_category.map((c) => `${categoryIcon(c.category)} ${c.category}`),
      datasets: [{
        data: stats.by_category.map((c) => Number(c.total)),
        backgroundColor: stats.by_category.map((_, i) => PALETTE[i % PALETTE.length]),
        // зазоры между секторами — промежутком, а не обводкой цветом карточки:
        // карточка полупрозрачная, и обводка её цветом легла бы поверх фона пятнами
        borderWidth: 0,
        spacing: 3,
        borderRadius: 6,
        hoverOffset: 6,
      }],
    },
    options: {
      cutout: "70%",
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => ` ${fmtMoney(ctx.parsed)}` } },
      },
    },
  });
}

/* Нарастающий итог по дням. Текущий месяц — только до сегодняшнего дня, дальше пунктиром
   прогноз до конца месяца (прямая от «сегодня» к прогнозу сервера). */
function drawDaily(stats, forecast, isCurrent) {
  const link = cssVar("--link");
  const hint = cssVar("--hint");
  const days = Math.max(stats.daily.length, stats.prev_daily.length);
  const today = new Date().getDate();
  const cur = stats.daily.map((v, i) => (isCurrent && i >= today ? null : Number(v)));
  const datasets = [
    {
      data: cur, borderColor: link, borderWidth: 2.5, pointRadius: 0, tension: 0.25, fill: true,
      backgroundColor: (ctx) => {
        const { chart } = ctx;
        if (!chart.chartArea) return "transparent";
        const g = chart.ctx.createLinearGradient(0, chart.chartArea.top, 0, chart.chartArea.bottom);
        g.addColorStop(0, `${link}55`);
        g.addColorStop(1, `${link}00`);
        return g;
      },
    },
    {
      data: stats.prev_daily.map(Number), borderColor: hint, borderWidth: 1.5, borderDash: [4, 4],
      pointRadius: 0, tension: 0.25, fill: false,
    },
  ];
  if (forecast && isCurrent) {
    const end = Number(forecast.forecast);
    const from = Number(stats.daily[today - 1] || 0);
    const steps = stats.daily.length - today;
    datasets.push({
      data: stats.daily.map((_, i) => (i < today - 1 ? null : from + ((end - from) * (i - (today - 1))) / Math.max(steps, 1))),
      borderColor: link, borderWidth: 2, borderDash: [6, 5], pointRadius: 0, fill: false,
    });
  }
  charts.daily = new Chart(document.getElementById("stats-daily"), {
    type: "line",
    data: { labels: Array.from({ length: days }, (_, i) => String(i + 1)), datasets },
    options: { ...baseOptions(), interaction: { mode: "index", intersect: false } },
  });
}

/* Столбики итогов за 6 или 12 месяцев; выбранный месяц — цветом бренда, тап открывает месяц. */
function drawTrend(stats) {
  if (!window.Chart) return;
  if (charts.trend) charts.trend.destroy();
  const items = stats.trend.slice(-trendMonths);
  const link = cssVar("--link");
  charts.trend = new Chart(document.getElementById("stats-trend"), {
    type: "bar",
    data: {
      labels: items.map((t) => monthShort(t.month)),
      datasets: [{
        data: items.map((t) => Number(t.total)),
        backgroundColor: items.map((t) => (t.month === state.month ? link : `${link}40`)),
        borderRadius: 8,
        borderSkipped: false,
        maxBarThickness: 34,
      }],
    },
    options: {
      ...baseOptions(),
      onClick: async (_, elements) => {
        if (!elements.length) return;
        const month = items[elements[0].index].month;
        if (month === state.month) return;
        haptic("selection");
        state.month = month;
        await renderStatsTab();
      },
    },
  });
}

export { renderStatsTab };
