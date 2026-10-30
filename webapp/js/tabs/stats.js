import { state, isPersonal } from "../state.js";
import { api } from "../api.js";
import { haptic, toast } from "../telegram.js";
import { categoryIcon, monthLabel, shiftMonth, todayMonth, todayISO, fmtMoney, escapeHtml } from "../format.js";
import { openSheet } from "../sheet.js";
import { shareMode, shareToggleHtml, bindShareToggle } from "./all.js";

/* ---------------- Вкладка «Статистика» ----------------
   Период — месяц, квартал, год или свой диапазон дат. Сверху итог периода и сравнение
   с прошлым периодом (для месяца — ещё и со средним за 3 месяца), в «Моих финансах» — доходы
   против расходов. Дальше пончик по категориям и список категорий с долей и изменением; тап
   по категории открывает шторку с её историей за 12 месяцев, подкатегориями и тратами.
   Ниже: для месяца — нарастающий итог по дням с прогнозом и календарь трат; дни недели,
   регулярные и разовые, крупнейшие траты и частые места; столбики по месяцам с разбивкой по
   категориям; в «Моих финансах» — доходы и расходы по месяцам. Данные — app/shared/stats.py. */

// Первые семь — как раньше в пончике; дальше — для категорий сверх семи и тех, что есть только
// в истории за 12 месяцев. Серый — только «Остальное».
const PALETTE = ["#2481cc", "#34c759", "#ff9500", "#ff3b30", "#af52de", "#5ac8fa", "#ffcc00",
  "#ff2d55", "#30b0c7", "#a2845e", "#5856d6", "#00c7be", "#ff6b3d", "#7d9c3a"];
const OTHER_COLOR = "#8e8e93";
const INCOME_COLOR = "#34c759";
const RATE_COLOR = "#ff9500";
const STACK_CATEGORIES = 7; // столько категорий в столбиках по месяцам, остальное — «Остальное»
const MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const QUARTERS = ["I", "II", "III", "IV"];
const PERIOD_MODES = [["month", "Месяц"], ["quarter", "Квартал"], ["year", "Год"], ["custom", "Период"]];
const charts = {};
let trendMonths = 6;
// Номер текущей отрисовки: если пока грузились данные, пользователь успел сменить месяц или
// пространство, устаревший ответ не должен перерисовать вкладку поверх нового
let renderSeq = 0;
// Траты периода для шторок (категория, день) — грузятся один раз на отрисовку
let expensesCache = null;

/* Настройки вкладки запоминаются на устройстве: период, свой диапазон, с чем сравнивать. */
function pref(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
}
function savePref(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* не критично */ }
}
let periodMode = pref("statsPeriod", "month");
let compareMode = pref("statsCompare", "prev"); // "prev" — к прошлому периоду, "avg" — к среднему за 3 мес
let custom = { from: pref("statsFrom", ""), to: pref("statsTo", "") };

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function monthShort(ym) {
  return MONTHS_SHORT[Number(ym.split("-")[1]) - 1];
}

function isoDate(y, m, d) {
  return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

function lastDay(y, m) {
  return new Date(y, m, 0).getDate();
}

function shortDate(iso) {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS_SHORT[m - 1]}`;
}

function longDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS_SHORT[m - 1]} ${y}`;
}

/* Выбранный период: даты с … по … включительно, подпись и параметры запроса. */
function currentPeriod() {
  const [y, m] = state.month.split("-").map(Number);
  if (periodMode === "quarter") {
    const q = Math.floor((m - 1) / 3);
    return {
      from: isoDate(y, q * 3 + 1, 1), to: isoDate(y, q * 3 + 3, lastDay(y, q * 3 + 3)),
      label: `${QUARTERS[q]} квартал ${y}`, step: 3, prevLabel: "к прошлому кварталу", sameLabel: () => "к тому же сроку прошлого квартала", prevIn: "в прошлом квартале",
    };
  }
  if (periodMode === "year") {
    return { from: isoDate(y, 1, 1), to: isoDate(y, 12, 31), label: `${y} год`, step: 12, prevLabel: "к прошлому году", sameLabel: () => "к тому же сроку прошлого года", prevIn: "в прошлом году" };
  }
  if (periodMode === "custom") {
    if (!custom.from || !custom.to || custom.from > custom.to) {
      custom = { from: `${state.month}-01`, to: todayISO() < `${state.month}-01` ? `${state.month}-01` : todayISO() };
    }
    const days = Math.round((new Date(custom.to) - new Date(custom.from)) / 86400000) + 1;
    return { from: custom.from, to: custom.to, label: "", step: 0, prevLabel: `к ${days} ${plural(days, "дню", "дням", "дням")} до этого`, sameLabel: () => "к тому же сроку до этого", prevIn: "за столько же дней до этого" };
  }
  const prevMonth = Number(shiftMonth(state.month, -1).split("-")[1]) - 1;
  return {
    from: isoDate(y, m, 1), to: isoDate(y, m, lastDay(y, m)), label: monthLabel(state.month), step: 1,
    prevLabel: `к ${MONTHS_DATIVE[prevMonth]}`, prevIn: "в прошлом месяце", month: state.month,
    sameLabel: (short) => `к 1–${new Date().getDate()} ${short ? MONTHS_SHORT[prevMonth] : MONTHS_GENITIVE[prevMonth]}`,
  };
}

function periodQuery(p) {
  return p.month ? `month=${p.month}` : `date_from=${p.from}&date_to=${p.to}`;
}

function inPeriod(ym, p) {
  return ym >= p.from.slice(0, 7) && ym <= p.to.slice(0, 7);
}

/* «к сентябрю» — дательный падеж прошлого месяца, «к 1–22 сентября» — родительный */
const MONTHS_DATIVE = ["январю", "февралю", "марту", "апрелю", "маю", "июню", "июлю", "августу", "сентябрю", "октябрю", "ноябрю", "декабрю"];
const MONTHS_GENITIVE = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];

/* Изменение к базе (прошлый период или среднее): рост трат — красным, снижение — зелёным. */
function deltaHtml(cur, base, suffix = "") {
  if (!base) return "";
  const pct = Math.round(((cur - base) / base) * 100);
  if (pct === 0) return `<span class="delta same">0%${suffix ? ` ${suffix}` : ""}</span>`;
  const cls = pct > 0 ? "up" : "down";
  return `<span class="delta ${cls}">${pct > 0 ? "▲" : "▼"} ${Math.abs(pct)}%${suffix ? ` ${suffix}` : ""}</span>`;
}

function destroyCharts() {
  for (const key of Object.keys(charts)) {
    charts[key].destroy();
    delete charts[key];
  }
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
}

/* Цвет категории — один во всех графиках: по месту в пончике, категориям, которых в периоде
   нет (но есть в истории за 12 месяцев), — свободные цвета палитры. */
function categoryColors(stats) {
  const map = new Map();
  stats.by_category.forEach((c, i) => map.set(c.category, PALETTE[i] || OTHER_COLOR));
  const used = new Set(map.values());
  const free = PALETTE.filter((c) => !used.has(c));
  for (const t of stats.category_trend) {
    if (!map.has(t.category)) map.set(t.category, free.shift() || OTHER_COLOR);
  }
  return map;
}

async function renderStatsTab() {
  const seq = ++renderSeq;
  const personal = isPersonal();
  const total = shareMode() === "total";
  const period = currentPeriod();
  const isCurrent = !!period.month && period.month === todayMonth();
  let stats, forecast = null, income = null;
  try {
    // В «Моих финансах» — личные траты и сколько потрачено в семейных (или их полные суммы).
    // Прогноз сервер считает по «Моим тратам», поэтому в режиме «Всего» его не показываем.
    const forecastChat = personal ? (total ? null : state.personalChatId) : state.chatId;
    const q = periodQuery(period);
    [stats, forecast, income] = await Promise.all([
      personal ? api(`/all/stats?${q}&full=${total}`) : api(`/chats/${state.chatId}/stats?${q}`),
      isCurrent && forecastChat ? api(`/chats/${forecastChat}/forecast`).catch(() => null) : null,
      personal && period.month ? api(`/chats/${state.personalChatId}/income?month=${period.month}`).catch(() => null) : null,
    ]);
  } catch (e) {
    const box = document.getElementById("content");
    if (seq === renderSeq && box) box.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    return;
  }
  // шапка могла перерисоваться (смена пространства) — берём #content уже после загрузки
  const content = document.getElementById("content");
  if (seq !== renderSeq || !content) return;

  expensesCache = null;
  const cur = Number(stats.total);
  const empty = stats.by_category.length === 0;
  const colors = categoryColors(stats);
  const hasAvg = stats.avg_total !== null && stats.avg_total !== undefined;
  const compare = hasAvg && compareMode === "avg" ? "avg" : "prev";
  // период ещё идёт — сравниваем с тем же сроком прошлого (сервер считает prev_to_date)
  const partial = stats.prev_to_date !== null && stats.prev_to_date !== undefined;
  // доходы за квартал/год/свой период — из статистики, против «Моих трат» (в «Всего» сравнивать не с чем)
  const incomeCard = period.month
    ? incomeHtml(income && { received: income.received, spent: income.spent })
    : (personal && !total ? incomeHtml({ received: stats.income_total, spent: stats.total }) : "");
  const headerDeltas = [
    deltaHtml(cur, partial ? Number(stats.prev_to_date) : Number(stats.prev_total), partial ? period.sameLabel() : period.prevLabel),
    hasAvg ? deltaHtml(cur, Number(stats.avg_total), "к среднему за 3 мес") : "",
  ].filter(Boolean).join('<span class="hint-text"> · </span>');

  destroyCharts();
  // .tab-toolbar и .stats-grid — раскладка широкого экрана (style.css): сверху строка
  // управления, ниже панели сеткой. На телефоне обёртки прозрачны (display: contents).
  content.innerHTML = `
    <div class="tab-toolbar">
    <div class="share-toggle period-toggle" id="period-toggle">
      ${PERIOD_MODES.map(([mode, label]) => `<button data-period="${mode}" class="${mode === periodMode ? "active" : ""}">${label}</button>`).join("")}
    </div>
    ${periodMode === "custom" ? `
      <div class="period-dates">
        <input type="date" id="period-from" value="${period.from}" aria-label="С">
        <span class="hint-text">—</span>
        <input type="date" id="period-to" value="${period.to}" aria-label="По">
      </div>` : `
      <div class="month-picker">
        <button data-dir="-1" aria-label="Предыдущий период">‹</button>
        <div class="label">${period.label}</div>
        <button data-dir="1" aria-label="Следующий период">›</button>
      </div>`}
    ${personal && period.month ? `<button class="link-btn pdf-btn" id="stats-pdf">📄 Выгрузить в PDF</button>` : ""}
    ${personal ? shareToggleHtml() : ""}
    <div class="total-line">${fmtMoney(cur)}</div>
    <div class="total-delta">${headerDeltas}</div>
    </div>
    <div class="stats-grid">
    ${incomeCard}
    ${empty ? '<div class="empty-state">Нет трат за этот период.</div>' : `
      <div class="stats-split">
      <div class="chart-wrap donut-wrap">
        <div class="chart-box donut-box"><canvas id="stats-donut"></canvas></div>
        <div class="donut-center">
          <div class="hint-text">${stats.by_category.length} ${plural(stats.by_category.length, "категория", "категории", "категорий")}</div>
          <b>${fmtMoney(cur)}</b>
        </div>
      </div>
      <div>
        ${hasAvg ? `
          <div class="share-toggle compare-toggle" id="compare-toggle">
            <button data-compare="prev" class="${compare === "prev" ? "active" : ""}">${partial ? period.sameLabel(true) : period.prevLabel}</button>
            <button data-compare="avg" class="${compare === "avg" ? "active" : ""}">к среднему за 3 мес</button>
          </div>` : ""}
        <div class="card stats-list">
          ${stats.by_category.map((c, i) => categoryRowHtml(c, i, cur, colors, compare)).join("")}
        </div>
      </div>
      </div>
      ${period.month ? `
        <div class="stats-pair">
          <div>
            <div class="section-title">По дням</div>
            <div class="chart-wrap">
              <div class="chart-legend">
                <span><i class="dot" style="background:${cssVar("--link")}"></i>${monthShort(period.month)}</span>
                <span><i class="dot dashed"></i>${monthShort(shiftMonth(period.month, -1))}</span>
                ${forecast ? `<span><i class="dot dashed accent"></i>прогноз ${fmtMoney(forecast.forecast)}</span>` : ""}
              </div>
              <div class="chart-box"><canvas id="stats-daily"></canvas></div>
            </div>
          </div>
          <div>
            <div class="section-title">Календарь</div>
            <div class="card calendar-card">${calendarHtml(stats, period.month)}</div>
          </div>
        </div>` : ""}
      <div class="stats-pair">
        <div>
          <div class="section-title">По дням недели</div>
          <div class="card">${weekdaysHtml(stats)}</div>
        </div>
        ${stats.recurring_count ? `
          <div>
            <div class="section-title">Регулярные и разовые</div>
            <div class="card">${recurringHtml(stats)}</div>
          </div>` : ""}
      </div>
      <div class="stats-pair">
        <div>
          <div class="section-title">Крупнейшие траты</div>
          <div class="card">${topExpensesHtml(stats)}</div>
        </div>
        ${stats.places.length ? `
          <div>
            <div class="section-title">Частые места</div>
            <div class="card">${placesHtml(stats)}</div>
          </div>` : ""}
      </div>`}
    <div class="stats-panel wide">
    <div class="section-title trend-title">
      <span>По месяцам</span>
      <div class="share-toggle trend-toggle">
        <button data-months="6" class="${trendMonths === 6 ? "active" : ""}">6 мес</button>
        <button data-months="12" class="${trendMonths === 12 ? "active" : ""}">12 мес</button>
      </div>
    </div>
    <div class="chart-wrap">
      <div class="chart-box"><canvas id="stats-trend"></canvas></div>
      <div class="chart-legend trend-legend" id="trend-legend"></div>
    </div>
    </div>
    ${showIncomeTrend(stats, personal, total) ? `
      <div class="stats-panel wide">
      <div class="section-title">Доходы и расходы</div>
      <div class="chart-wrap">
        <div class="chart-legend">
          <span><i class="dot" style="background:${INCOME_COLOR}"></i>доходы</span>
          <span><i class="dot" style="background:${cssVar("--link")}"></i>расходы</span>
          <span><i class="dot" style="background:${RATE_COLOR}"></i>отложено, %</span>
        </div>
        <div class="chart-box"><canvas id="stats-income"></canvas></div>
        <div class="hint-text income-trend-note" id="income-note"></div>
      </div>
      </div>` : ""}
    </div>
  `;

  const ctx = { stats, period, colors, personal, total };
  const bindCategoryRows = () => content.querySelectorAll(".stats-cat").forEach((row) => {
    row.addEventListener("click", () => {
      haptic("selection");
      openCategorySheet(ctx, stats.by_category[Number(row.dataset.idx)]);
    });
  });
  bindCategoryRows();
  content.querySelectorAll(".cal-day[data-day]").forEach((cell) => {
    cell.addEventListener("click", () => {
      haptic("selection");
      openDaySheet(ctx, cell.dataset.day);
    });
  });
  content.querySelectorAll(".trend-toggle button").forEach((b) => {
    b.addEventListener("click", () => {
      haptic("selection");
      trendMonths = Number(b.dataset.months);
      content.querySelectorAll(".trend-toggle button").forEach((x) => x.classList.toggle("active", x === b));
      drawTrend(ctx);
      drawIncomeTrend(ctx);
    });
  });
  content.querySelectorAll("#period-toggle button").forEach((b) => {
    b.addEventListener("click", async () => {
      if (b.dataset.period === periodMode) return;
      haptic("selection");
      periodMode = b.dataset.period;
      savePref("statsPeriod", periodMode);
      await renderStatsTab();
    });
  });
  content.querySelectorAll("#compare-toggle button").forEach((b) => {
    b.addEventListener("click", () => {
      if (b.dataset.compare === compareMode) return;
      haptic("selection");
      compareMode = b.dataset.compare;
      savePref("statsCompare", compareMode);
      content.querySelectorAll("#compare-toggle button").forEach((x) => x.classList.toggle("active", x === b));
      content.querySelector(".stats-list").innerHTML = stats.by_category
        .map((c, i) => categoryRowHtml(c, i, cur, colors, compareMode)).join("");
      bindCategoryRows();
    });
  });
  content.querySelectorAll(".period-dates input").forEach((input) => {
    input.addEventListener("change", async () => {
      const from = content.querySelector("#period-from").value;
      const to = content.querySelector("#period-to").value;
      if (!from || !to) return;
      custom = from <= to ? { from, to } : { from: to, to: from };
      savePref("statsFrom", custom.from);
      savePref("statsTo", custom.to);
      await renderStatsTab();
    });
  });
  bindShareToggle(content, () => renderStatsTab());
  const pdfBtn = content.querySelector("#stats-pdf");
  if (pdfBtn) pdfBtn.addEventListener("click", () => exportPdf(pdfBtn, period.month));
  content.querySelectorAll(".month-picker button").forEach((b) => {
    b.addEventListener("click", async () => {
      haptic("selection");
      state.month = shiftMonth(state.month, Number(b.dataset.dir) * period.step);
      await renderStatsTab();
    });
  });

  if (!window.Chart) return;
  if (!empty) {
    drawDonut(stats, colors);
    if (period.month) drawDaily(stats, forecast, isCurrent);
  }
  drawTrend(ctx);
  drawIncomeTrend(ctx);
}

/* PDF-отчёт за месяц плана (app/shared/pdf_report.py), в котором выбранный месяц: для текущего —
   месяц плана с сегодняшним днём, для прошлых — с последним днём месяца. В Telegram файл
   присылает бот в личку (мини-апп не умеет скачивать), в браузере он просто скачивается. */
async function exportPdf(btn, month) {
  const [y, m] = month.split("-").map(Number);
  const date = [todayISO(), isoDate(y, m, lastDay(y, m))].sort()[0];
  const theme = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "⏳ Готовлю PDF…";
  try {
    if (state.initData) {
      await api(`/chats/${state.personalChatId}/report/send`, { method: "POST", body: { date, theme } });
      haptic("notification", "success");
      btn.textContent = "✓ Отправил в чат с ботом";
    } else {
      const res = await api(`/chats/${state.personalChatId}/report.pdf?date=${date}&theme=${theme}`);
      const name = (/filename="([^"]+)"/.exec(res.headers.get("content-disposition") || "") || [])[1] || "SplitBudget.pdf";
      const url = URL.createObjectURL(await res.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: name });
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      btn.textContent = label;
    }
  } catch (e) {
    btn.textContent = label;
    toast(e.message);
  } finally {
    btn.disabled = false;
  }
}

/* Доходы против расходов (только «Мои финансы»; расходы — ваши траты, как в «Доходах»). */
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

function categoryRowHtml(c, idx, total, colors, compare) {
  const amount = Number(c.total);
  const share = total ? (amount / total) * 100 : 0;
  const color = colors.get(c.category);
  const prev = c.prev_to_date !== null && c.prev_to_date !== undefined ? c.prev_to_date : c.prev_total;
  const base = compare === "avg" ? Number(c.avg_total) : Number(prev);
  return `
    <div class="stats-cat" data-idx="${idx}">
      <div class="stats-cat-head">
        <span class="stats-cat-name"><i class="dot" style="background:${color}"></i>${categoryIcon(c.category)} ${escapeHtml(c.category)} <span class="hint-text">${Math.round(share)}%</span></span>
        <span class="stats-cat-amount">${fmtMoney(amount)}</span>
      </div>
      <div class="stats-cat-sub">
        <div class="stats-cat-bar"><div style="width:${share}%;background:${color}"></div></div>
        ${deltaHtml(amount, base)}
      </div>
    </div>`;
}

/* Календарь месяца: чем больше потрачено в день, тем насыщеннее клетка; тап — траты дня. */
function calendarHtml(stats, month) {
  const perDay = stats.daily.map((v, i) => Number(v) - (i ? Number(stats.daily[i - 1]) : 0));
  const max = Math.max(...perDay, 0);
  const [y, m] = month.split("-").map(Number);
  const lead = (new Date(y, m - 1, 1).getDay() + 6) % 7; // понедельник — первый
  const today = todayISO();
  const cells = WEEKDAYS.map((d) => `<div class="cal-head">${d}</div>`);
  for (let i = 0; i < lead; i++) cells.push('<div class="cal-day blank"></div>');
  perDay.forEach((amount, i) => {
    const iso = isoDate(y, m, i + 1);
    const future = iso > today;
    const level = amount > 0 && max > 0 ? Math.round(12 + (amount / max) * 78) : 0;
    const style = level ? `background:color-mix(in srgb, var(--link) ${level}%, var(--field-bg))` : "";
    const cls = ["cal-day", future ? "future" : "", iso === today ? "today" : "", level > 55 ? "strong" : ""].join(" ");
    cells.push(`
      <button class="${cls}" ${amount > 0 ? `data-day="${iso}"` : "disabled"} style="${style}"
        aria-label="${i + 1}: ${amount > 0 ? fmtMoney(amount) : "нет трат"}">
        <span>${i + 1}</span>${amount > 0 ? `<b>${compactMoney(amount)}</b>` : ""}
      </button>`);
  });
  const spentDays = perDay.filter((v) => v > 0).length;
  const passed = month === todayMonth() ? Number(today.slice(8)) : perDay.length;
  return `
    <div class="cal-grid">${cells.join("")}</div>
    <div class="hint-text cal-note">Дней с тратами: ${spentDays} из ${passed}${max > 0 ? ` · больше всего ${fmtMoney(max)}` : ""}</div>`;
}

/* Дни недели: доля трат периода по дням, выходные — отдельной строкой. */
function weekdaysHtml(stats) {
  const values = stats.weekdays.map(Number);
  const sum = values.reduce((a, b) => a + b, 0);
  const max = Math.max(...values, 0);
  const weekend = sum ? Math.round(((values[5] + values[6]) / sum) * 100) : 0;
  return `
    ${values.map((v, i) => `
      <div class="bar-row ${i >= 5 ? "weekend" : ""}">
        <span class="bar-row-label">${WEEKDAYS[i]}</span>
        <div class="bar-row-track"><div style="width:${max ? (v / max) * 100 : 0}%"></div></div>
        <span class="bar-row-value">${sum ? Math.round((v / sum) * 100) : 0}%</span>
      </div>`).join("")}
    <div class="hint-text bar-note">На выходные — ${weekend}% трат</div>`;
}

/* Регулярные платежи (подписки, кредиты, коммуналка) против остальных трат. */
function recurringHtml(stats) {
  const total = Number(stats.total);
  const regular = Number(stats.recurring_total);
  const other = total - regular;
  const pct = total ? Math.round((regular / total) * 100) : 0;
  const n = stats.recurring_count;
  return `
    <div class="split-bar"><div style="width:${total ? (regular / total) * 100 : 0}%"></div></div>
    <div class="balance-row">
      <span><i class="dot" style="background:var(--link)"></i>Регулярные <span class="hint-text">${n} ${plural(n, "платёж", "платежа", "платежей")}</span></span>
      <span>${fmtMoney(regular)} <span class="hint-text">${pct}%</span></span>
    </div>
    <div class="balance-row">
      <span><i class="dot" style="background:var(--hint)"></i>Разовые <span class="hint-text">${stats.count - n} ${plural(stats.count - n, "трата", "траты", "трат")}</span></span>
      <span>${fmtMoney(other)} <span class="hint-text">${100 - pct}%</span></span>
    </div>`;
}

function topExpensesHtml(stats) {
  return stats.top_expenses.map((e) => `
    <div class="balance-row cat-expense">
      <span class="cat-expense-title">
        <span class="hint-text">${shortDate(e.expense_date)}</span>${categoryIcon(e.category)} ${escapeHtml(e.title)}
      </span>
      <span>${fmtMoney(e.amount)}</span>
    </div>`).join("") || '<div class="hint-text">Нет трат.</div>';
}

function placesHtml(stats) {
  return stats.places.map((p) => `
    <div class="balance-row cat-expense">
      <span class="cat-expense-title">${escapeHtml(p.title)} <span class="hint-text">· ${p.count} ${plural(p.count, "раз", "раза", "раз")}</span></span>
      <span>${fmtMoney(p.total)}</span>
    </div>`).join("");
}

function showIncomeTrend(stats, personal, total) {
  return personal && !total && stats.income_trend && stats.income_trend.some((t) => Number(t.total) > 0);
}

/* Траты периода для шторок: один запрос на отрисовку вкладки. */
async function periodExpenses(ctx) {
  if (!expensesCache) {
    const q = periodQuery(ctx.period);
    expensesCache = ctx.personal
      ? api(`/all/expenses?${q}`)
      : api(`/chats/${state.chatId}/expenses?${q}&limit=1000`);
    expensesCache.catch(() => { expensesCache = null; });
  }
  return expensesCache;
}

function amountOf(ctx, e) {
  return ctx.personal && !ctx.total ? Number(e.my_share) : Number(e.amount);
}

async function fillExpenses(ctx, box, filter, { showDate = true } = {}) {
  try {
    const items = await periodExpenses(ctx);
    const rows = items.filter((e) => filter(e) && amountOf(ctx, e) !== 0);
    box.innerHTML = rows.length ? rows.map((e) => `
      <div class="balance-row cat-expense">
        <span class="cat-expense-title">
          ${showDate ? `<span class="hint-text">${shortDate(e.expense_date)}</span>` : `${categoryIcon(e.category)} `}
          ${escapeHtml(e.title)}${e.subcategory ? ` <span class="hint-text">· ${escapeHtml(e.subcategory)}</span>` : ""}
        </span>
        <span>${fmtMoney(amountOf(ctx, e))}</span>
      </div>`).join("") : '<div class="hint-text">Нет трат.</div>';
    return rows;
  } catch (e) {
    box.innerHTML = `<div class="error-text">${escapeHtml(e.message)}</div>`;
    return [];
  }
}

/* Шторка категории: история за 12 месяцев, подкатегории и траты периода в ней. */
function openCategorySheet(ctx, c) {
  const { stats, period, colors } = ctx;
  const color = colors.get(c.category);
  const subs = c.subcategories.filter((s) => s.subcategory);
  const history = (stats.category_trend.find((t) => t.category === c.category) || { totals: [] }).totals.map(Number);
  const overlay = openSheet(`
    <div class="sheet-title"><i class="dot big" style="background:${color}"></i>${categoryIcon(c.category)} ${escapeHtml(c.category)}</div>
    <div class="hint-text">${fmtMoney(c.total)} · ${c.count} ${plural(c.count, "трата", "траты", "трат")}
      ${Number(c.prev_total) ? ` · ${period.prevIn} ${fmtMoney(c.prev_total)}` : ""}</div>
    <div class="section-title">За 12 месяцев</div>
    <div class="chart-wrap sheet-chart">
      <div class="chart-box short"><canvas id="cat-history"></canvas></div>
      <div class="hint-text" id="cat-history-note"></div>
    </div>
    ${subs.length ? `
      <div class="section-title">Подкатегории</div>
      <div class="card">${c.subcategories.map((s) => `
        <div class="balance-row">
          <span>${escapeHtml(s.subcategory || "Без подкатегории")} <span class="hint-text">(${s.count})</span></span>
          <span>${fmtMoney(s.total)}</span>
        </div>`).join("")}</div>` : ""}
    <div class="section-title">Траты</div>
    <div class="card" id="cat-expenses"><div class="loading">Загрузка…</div></div>`);

  drawCategoryHistory(overlay, ctx, history, color);
  fillExpenses(ctx, overlay.querySelector("#cat-expenses"), (e) => e.category === c.category);
}

/* Шторка дня из календаря: все траты этого дня. */
function openDaySheet(ctx, iso) {
  const overlay = openSheet(`
    <div class="sheet-title">${longDate(iso)}</div>
    <div class="hint-text" id="day-sum"></div>
    <div class="card" id="day-expenses"><div class="loading">Загрузка…</div></div>`);
  fillExpenses(ctx, overlay.querySelector("#day-expenses"), (e) => e.expense_date === iso, { showDate: false }).then((rows) => {
    if (!rows.length) return;
    const sum = rows.reduce((a, e) => a + amountOf(ctx, e), 0);
    overlay.querySelector("#day-sum").textContent = `${fmtMoney(sum)} · ${rows.length} ${plural(rows.length, "трата", "траты", "трат")}`;
  });
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
  const abs = Math.abs(v);
  if (abs >= 1000000) return `${+(v / 1000000).toFixed(1)}M`;
  if (abs >= 1000) return `${+(v / 1000).toFixed(1)}k`;
  return String(Math.round(v));
}

function drawDonut(stats, colors) {
  charts.donut = new Chart(document.getElementById("stats-donut"), {
    type: "doughnut",
    data: {
      labels: stats.by_category.map((c) => `${categoryIcon(c.category)} ${c.category}`),
      datasets: [{
        data: stats.by_category.map((c) => Number(c.total)),
        backgroundColor: stats.by_category.map((c) => colors.get(c.category)),
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

/* Столбики за 6 или 12 месяцев, разбитые по категориям: месяцы периода — ярко, остальные —
   бледнее; тап по столбику открывает этот месяц. */
function drawTrend(ctx) {
  if (!window.Chart) return;
  if (charts.trend) charts.trend.destroy();
  const { stats, period, colors } = ctx;
  const months = stats.trend.slice(-trendMonths).map((t) => t.month);
  const cut = (totals) => totals.slice(-trendMonths).map(Number);
  const series = stats.category_trend
    .map((t) => ({ category: t.category, data: cut(t.totals) }))
    .filter((s) => s.data.some((v) => v > 0));
  const shown = series.slice(0, STACK_CATEGORIES);
  const rest = series.slice(STACK_CATEGORIES);
  if (rest.length) {
    shown.push({ category: "Остальное", data: months.map((_, i) => rest.reduce((a, s) => a + s.data[i], 0)), other: true });
  }
  const colorOf = (s) => (s.other ? OTHER_COLOR : colors.get(s.category) || OTHER_COLOR);
  const options = baseOptions();
  options.scales.x.stacked = true;
  options.scales.y.stacked = true;
  options.interaction = { mode: "index", intersect: false };
  options.plugins.tooltip = {
    filter: (item) => item.parsed.y > 0,
    itemSort: (a, b) => b.parsed.y - a.parsed.y,
    callbacks: {
      label: (item) => ` ${item.dataset.label}: ${fmtMoney(item.parsed.y)}`,
      footer: (items) => `Всего: ${fmtMoney(stats.trend.slice(-trendMonths)[items[0].dataIndex].total)}`,
    },
  };
  options.onClick = async (_, elements) => {
    if (!elements.length) return;
    const month = months[elements[0].index];
    if (periodMode === "month" && month === state.month) return;
    haptic("selection");
    state.month = month;
    periodMode = "month";
    savePref("statsPeriod", periodMode);
    await renderStatsTab();
  };
  charts.trend = new Chart(document.getElementById("stats-trend"), {
    type: "bar",
    data: {
      labels: months.map(monthShort),
      datasets: shown.map((s) => ({
        label: s.other ? s.category : `${categoryIcon(s.category)} ${s.category}`,
        data: s.data,
        backgroundColor: months.map((m) => (inPeriod(m, period) ? colorOf(s) : `${colorOf(s)}66`)),
        borderRadius: 4,
        borderSkipped: false,
        maxBarThickness: 34,
      })),
    },
    options,
  });
  const legend = document.getElementById("trend-legend");
  if (legend) {
    legend.innerHTML = shown.map((s) => `
      <span><i class="dot round" style="background:${colorOf(s)}"></i>${escapeHtml(s.category)}</span>`).join("");
  }
}

/* Доходы и расходы по месяцам и доля отложенного (только «Мои финансы», «Мои траты»). */
function drawIncomeTrend(ctx) {
  if (!window.Chart) return;
  if (charts.income) charts.income.destroy();
  const canvas = document.getElementById("stats-income");
  if (!canvas) return;
  const { stats } = ctx;
  const months = stats.trend.slice(-trendMonths).map((t) => t.month);
  const spent = stats.trend.slice(-trendMonths).map((t) => Number(t.total));
  const income = stats.income_trend.slice(-trendMonths).map((t) => Number(t.total));
  const rate = income.map((v, i) => (v > 0 ? Math.round(((v - spent[i]) / v) * 100) : null));
  const link = cssVar("--link");
  const options = baseOptions();
  options.interaction = { mode: "index", intersect: false };
  options.plugins.tooltip = {
    callbacks: {
      label: (item) => (item.dataset.yAxisID === "rate"
        ? ` отложено: ${item.parsed.y}%`
        : ` ${item.dataset.label}: ${fmtMoney(item.parsed.y)}`),
    },
  };
  options.scales.rate = {
    position: "right", grid: { display: false }, border: { display: false },
    ticks: { color: cssVar("--hint"), maxTicksLimit: 4, font: { size: 11 }, callback: (v) => `${v}%` },
  };
  charts.income = new Chart(canvas, {
    type: "bar",
    data: {
      labels: months.map(monthShort),
      datasets: [
        { type: "line", label: "отложено", yAxisID: "rate", data: rate, borderColor: RATE_COLOR, backgroundColor: RATE_COLOR,
          borderWidth: 2, pointRadius: 3, tension: 0.25, spanGaps: true, order: 0 },
        { type: "bar", label: "доходы", data: income, backgroundColor: INCOME_COLOR, borderRadius: 6, borderSkipped: false, maxBarThickness: 22, order: 1 },
        { type: "bar", label: "расходы", data: spent, backgroundColor: link, borderRadius: 6, borderSkipped: false, maxBarThickness: 22, order: 1 },
      ],
    },
    options,
  });
  const withIncome = rate.filter((v) => v !== null);
  const note = document.getElementById("income-note");
  if (note && withIncome.length) {
    const avg = Math.round(withIncome.reduce((a, b) => a + b, 0) / withIncome.length);
    note.textContent = avg >= 0
      ? `В среднем откладываете ${avg}% дохода (по месяцам, где были доходы)`
      : `В среднем тратите на ${-avg}% больше дохода (по месяцам, где были доходы)`;
  }
}

/* История категории за 12 месяцев в её шторке: месяцы периода — цветом категории,
   пунктир — среднее по месяцам, где были траты. */
function drawCategoryHistory(overlay, ctx, history, color) {
  const canvas = overlay.querySelector("#cat-history");
  if (!window.Chart || !canvas) return;
  if (charts.history) charts.history.destroy();
  const months = ctx.stats.trend.map((t) => t.month);
  const active = history.filter((v) => v > 0);
  const avg = active.length ? active.reduce((a, b) => a + b, 0) / active.length : 0;
  const options = baseOptions();
  options.interaction = { mode: "index", intersect: false };
  options.plugins.tooltip = {
    filter: (item) => item.datasetIndex === 0,
    callbacks: { label: (item) => ` ${fmtMoney(item.parsed.y)}` },
  };
  charts.history = new Chart(canvas, {
    type: "bar",
    data: {
      labels: months.map(monthShort),
      datasets: [
        { type: "bar", data: history, backgroundColor: months.map((m) => (inPeriod(m, ctx.period) ? color : `${color}59`)),
          borderRadius: 6, borderSkipped: false, maxBarThickness: 24 },
        { type: "line", data: months.map(() => avg), borderColor: cssVar("--hint"), borderWidth: 1.5, borderDash: [4, 4], pointRadius: 0 },
      ],
    },
    options,
  });
  const note = overlay.querySelector("#cat-history-note");
  if (note && active.length) {
    note.textContent = `В среднем ${fmtMoney(avg)} в месяц · максимум ${fmtMoney(Math.max(...active))}`;
  }
}

export { renderStatsTab, PALETTE, OTHER_COLOR, deltaHtml };
