import { state, isAll } from "../state.js";
import { api } from "../api.js";
import { haptic } from "../telegram.js";
import { categoryIcon, monthLabel, shiftMonth, fmtMoney, escapeHtml } from "../format.js";

/* ---------------- Вкладка «Статистика» ---------------- */

let statsChart = null;

async function renderStatsTab() {
  const content = document.getElementById("content");
  let stats;
  try {
    // В сводке «Все траты» — статистика по доле пользователя во всех пространствах.
    const path = isAll() ? "/all/stats" : `/chats/${state.chatId}/stats`;
    stats = await api(`${path}?month=${state.month}`);
  } catch (e) {
    content.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    return;
  }

  content.innerHTML = `
    <div class="month-picker">
      <button data-dir="-1">‹</button>
      <div class="label">${monthLabel(state.month)}</div>
      <button data-dir="1">›</button>
    </div>
    <div class="total-line">${fmtMoney(stats.total)}</div>
    ${stats.by_category.length === 0 ? '<div class="empty-state">Нет трат за этот месяц.</div>' : `
      <div class="chart-wrap"><canvas id="stats-chart" height="220"></canvas></div>
      <div class="card">
        ${stats.by_category.map((c, i) => categoryRowHtml(c, i)).join("")}
      </div>`}
  `;

  // Тап по категории раскрывает её подкатегории (если они есть)
  content.querySelectorAll(".stats-cat[data-expandable]").forEach((row) => {
    row.addEventListener("click", () => {
      haptic("selection");
      const subs = content.querySelector(`.stats-subs[data-for="${row.dataset.idx}"]`);
      const open = subs.style.display !== "none";
      subs.style.display = open ? "none" : "block";
      row.classList.toggle("open", !open);
    });
  });

  content.querySelectorAll(".month-picker button").forEach((b) => {
    b.addEventListener("click", async () => {
      haptic("selection");
      state.month = shiftMonth(state.month, Number(b.dataset.dir));
      await renderStatsTab();
    });
  });

  if (stats.by_category.length > 0 && window.Chart) {
    const ctx = document.getElementById("stats-chart");
    if (statsChart) statsChart.destroy();
    const palette = ["#2481cc", "#34c759", "#ff9500", "#ff3b30", "#af52de", "#5ac8fa", "#ffcc00", "#8e8e93"];
    statsChart = new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: stats.by_category.map((c) => `${categoryIcon(c.category)} ${c.category}`),
        datasets: [{
          data: stats.by_category.map((c) => Number(c.total)),
          backgroundColor: stats.by_category.map((_, i) => palette[i % palette.length]),
          borderWidth: 0,
        }],
      },
      options: {
        plugins: { legend: { position: "bottom", labels: { color: getComputedStyle(document.body).color } } },
      },
    });
  }
}

/* Строка категории + (скрытая) разбивка по подкатегориям. Разбивку показываем, только
   если в категории есть хоть одна подкатегория — иначе раскрывать нечего. */
function categoryRowHtml(c, idx) {
  const expandable = c.subcategories.some((s) => s.subcategory);
  return `
    <div class="balance-row stats-cat" data-idx="${idx}" ${expandable ? "data-expandable" : ""}>
      <span>${expandable ? '<span class="chevron">›</span>' : ""}${categoryIcon(c.category)} ${escapeHtml(c.category)} <span class="hint-text">(${c.count})</span></span>
      <span>${fmtMoney(c.total)}</span>
    </div>
    ${expandable ? `
      <div class="stats-subs" data-for="${idx}" style="display:none;">
        ${c.subcategories.map((s) => `
          <div class="balance-row stats-sub">
            <span>${escapeHtml(s.subcategory || "Без подкатегории")} <span class="hint-text">(${s.count})</span></span>
            <span>${fmtMoney(s.total)}</span>
          </div>`).join("")}
      </div>` : ""}`;
}

export { renderStatsTab };
