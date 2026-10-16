import { state } from "../state.js";
import { api } from "../api.js";
import { tg, haptic, toast, confirmAction } from "../telegram.js";
import { categoryIcon, todayISO, fmtMoney, escapeHtml } from "../format.js";
import { openSheet, closeSheet, useMainButtonFor, showFormError } from "../sheet.js";
import { obligationsHtml, openPaymentSheet } from "./budgets.js";

/* ---------------- Вкладка «План» ----------------
   План распределения дохода «Моих финансов» (app/shared/plan.py): каждое поступление
   делится по процентам статей, «Свободно» получает остаток процентов. По статье видно,
   сколько выделено, потрачено и осталось. В конце месяца плана статьи обнуляются, а их
   остатки (и минусы) переходят в «Свободно». */

const KIND_LABELS = {
  spend: ["💸", "Расходы", "Траты выбранных категорий списываются с этой статьи."],
  goal: ["🐷", "Накопления", "Выделенное откладывается на цель по кнопке «Отложил»."],
  debt: ["💳", "Долги", "Статью закрывают платежи по выбранным кредиткам и кредитам."],
};

// «Категория › Подкатегория» в списке категорий статьи (app/shared/plan.py, SUB_SEP)
const SUB_SEP = " › ";

const MONTHS_GEN = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

function shortDate(iso) {
  const [, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS_GEN[m - 1]}`;
}

function shiftISO(iso, days) {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d + days));
  return date.toISOString().slice(0, 10);
}

function planChatId() {
  return state.personalChatId;
}

function pctText(value) {
  return `${Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 2 })}%`;
}

function itemIcon(it) {
  if (it.kind === "free") return "🪙";
  if (it.kind === "spend" && it.categories.length === 1) return categoryIcon(it.categories[0].split(SUB_SEP)[0]);
  return KIND_LABELS[it.kind][0];
}

function itemCardHtml(it, isCurrent) {
  const allocated = Number(it.allocated);
  const spent = Number(it.spent);
  const remaining = Number(it.remaining);
  const used = allocated > 0 ? (spent / allocated) * 100 : spent > 0 ? 100 : 0;
  const level = remaining < 0 ? "over" : used >= 80 && it.kind !== "goal" ? "warn" : "ok";
  const width = Math.min(Math.max(used, 0), 100);
  const verb = { spend: "Потрачено", goal: "Отложено", debt: "Оплачено", free: "Потрачено" }[it.kind];
  const left = remaining >= 0
    ? `${it.kind === "goal" ? "отложить ещё" : "осталось"} <b>${fmtMoney(remaining)}</b>`
    : `<span class="plan-minus">минус ${fmtMoney(-remaining)}</span>`;

  const notes = [];
  if (it.kind === "spend") notes.push(it.categories.map(escapeHtml).join(", "));
  if (it.kind === "goal" && it.goal_title) {
    notes.push(`🐷 ${escapeHtml(it.goal_title)}: ${fmtMoney(it.goal_saved)} из ${fmtMoney(it.goal_target)}`);
  }
  if (it.kind === "goal" && !it.goal_title) notes.push("Цель удалена — выберите другую");
  if (it.kind === "debt" && it.debt_left !== null) notes.push(`Долг по карте: ${fmtMoney(it.debt_left)}`);
  if (it.kind === "free") {
    const carried = Number(it.carried);
    if (carried) notes.push(`${carried > 0 ? "Перенесено" : "Перерасход"} с прошлых месяцев: ${fmtMoney(Math.abs(carried))}`);
    notes.push("Сюда же списываются траты без статьи");
  }
  const saveBtn = it.kind === "goal" && it.goal_title && remaining > 0 && isCurrent
    ? `<button class="btn small plan-save" data-save="${it.id}">✅ Отложил ${fmtMoney(remaining)}</button>`
    : "";
  return `
    <div class="card budget-card plan-item ${it.kind === "free" ? "plan-free" : ""}" data-id="${it.id ?? ""}">
      <div class="budget-head">
        <span>${itemIcon(it)} ${escapeHtml(it.title)}</span>
        <span class="plan-pct">${pctText(it.percent)}</span>
      </div>
      <div class="budget-bar"><div class="budget-fill ${level}" style="width:${width}%"></div></div>
      <div class="budget-sub">
        <span>${verb} ${fmtMoney(spent)} из ${fmtMoney(allocated)}</span>
        <span class="hint-text">${left}</span>
      </div>
      ${notes.map((n) => `<div class="hint-text plan-note">${n}</div>`).join("")}
      ${saveBtn}
    </div>`;
}

async function renderPlanTab() {
  const content = document.getElementById("content");
  const date = state.planDate || todayISO();
  let plan, obligations;
  try {
    plan = await api(`/chats/${planChatId()}/plan?date=${date}`);
    obligations = await api(`/chats/${planChatId()}/obligations?month=${plan.start.slice(0, 7)}`);
  } catch (e) {
    content.innerHTML = `<div class="empty-state">${escapeHtml(e.message)}</div>`;
    return;
  }
  const today = todayISO();
  const isCurrent = plan.start <= today && today <= plan.end;
  const items = plan.items.filter((i) => i.kind !== "free");
  const free = plan.items.find((i) => i.kind === "free");
  const income = Number(plan.income);
  const count = plan.incomes_count;
  const incomeLine = count
    ? `Поступило <b>${fmtMoney(income)}</b> · ${count} ${count === 1 ? "поступление" : count < 5 ? "поступления" : "поступлений"}`
    : "Поступлений пока не было — добавьте их во вкладке «💰 Доходы»";
  // Можно тратить — остатки расходных статей и «Свободно» (накопления и долги не трогаем)
  const totalLeft = plan.items.reduce((s, i) => s + (i.kind === "spend" || i.kind === "free" ? Number(i.remaining) : 0), 0);

  content.innerHTML = `
    <div class="month-picker">
      <button data-dir="-1" aria-label="Предыдущий месяц">‹</button>
      <div class="label">${shortDate(plan.start)} – ${shortDate(plan.end)}</div>
      <button data-dir="1" aria-label="Следующий месяц">›</button>
    </div>
    <div class="card plan-summary">
      <div>${incomeLine}</div>
      <div class="hint-text">Распределено ${pctText(plan.percent_total)} · свободно ${pctText(free.percent)} · можно тратить ${fmtMoney(totalLeft)}</div>
      <button class="link-btn" id="plan-settings">Месяц плана начинается ${plan.start_day}-го числа</button>
    </div>
    ${items.length === 0
      ? '<div class="empty-state">Статей пока нет.<br>Нажмите «+» и задайте, какой процент от каждого поступления идёт на еду, отпуск, кредитки и т.д. Остаток достаётся «Свободно».</div>'
      : ""}
    ${items.map((i) => itemCardHtml(i, isCurrent)).join("")}
    ${itemCardHtml(free, isCurrent)}
    <div class="hint-text" style="margin: 4px 4px 16px;">В конце месяца плана статьи обнуляются: остатки и перерасходы переходят в «Свободно».</div>
    ${obligationsHtml(obligations)}`;

  content.querySelectorAll(".month-picker button").forEach((b) => {
    b.addEventListener("click", async () => {
      haptic("selection");
      state.planDate = Number(b.dataset.dir) < 0 ? shiftISO(plan.start, -1) : shiftISO(plan.end, 1);
      await renderPlanTab();
    });
  });
  content.querySelector("#plan-settings").addEventListener("click", () => openPlanSettings(plan));
  content.querySelectorAll(".plan-item[data-id]").forEach((el) => {
    if (!el.dataset.id) return;
    el.addEventListener("click", () => openPlanItemModal(items.find((i) => i.id === Number(el.dataset.id)), plan));
  });
  content.querySelectorAll("[data-save]").forEach((btn) => {
    btn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      btn.disabled = true;
      try {
        await api(`/chats/${planChatId()}/plan/items/${btn.dataset.save}/save`, { method: "POST" });
        haptic("notification", "success");
        await renderPlanTab();
      } catch (e) {
        toast(e.message);
        btn.disabled = false;
      }
    });
  });
  content.querySelectorAll(".obligation-row").forEach((el) => {
    el.addEventListener("click", () => {
      const item = obligations.items.find((o) => o.payment_id === Number(el.dataset.id));
      openPaymentSheet(item, renderPlanTab);
    });
  });
}

function openPlanSettings(plan) {
  const overlay = openSheet(`
    <div class="sheet-title">Месяц плана</div>
    <div class="hint-text" style="margin-bottom:12px;">Если зарплата приходит, например, 25-го, удобно начинать месяц плана с этого дня: тогда статьи обнуляются перед новой зарплатой, а не 1-го числа.</div>
    <div class="field">
      <label>Начинается с числа</label>
      <input type="number" id="ps-day" min="1" max="28" step="1" value="${plan.start_day}">
    </div>
    <div class="error-text" id="ps-error" style="display:none;"></div>
    <button class="btn" id="ps-submit">Сохранить</button>`);
  const submit = overlay.querySelector("#ps-submit");
  submit.addEventListener("click", async () => {
    const day = Number(overlay.querySelector("#ps-day").value);
    if (!Number.isInteger(day) || day < 1 || day > 28) {
      showFormError(overlay.querySelector("#ps-error"), "Число от 1 до 28");
      return;
    }
    try {
      await api(`/chats/${planChatId()}/plan/settings`, { method: "PUT", body: { start_day: day } });
      haptic("notification", "success");
      closeSheet(overlay);
      state.planDate = null;
      await renderPlanTab();
    } catch (e) {
      showFormError(overlay.querySelector("#ps-error"), e.message);
    }
  });
  useMainButtonFor(overlay, submit, "Сохранить");
}

async function openPlanItemModal(existing, plan) {
  const isEdit = !!existing;
  let goals = [], recurring = [];
  try {
    [goals, recurring] = await Promise.all([
      api(`/chats/${planChatId()}/goals`),
      api(`/chats/${planChatId()}/recurring`),
    ]);
  } catch (e) {
    toast(e.message);
    return;
  }
  goals = goals.filter((g) => !g.is_archived || (existing && existing.goal_id === g.id));
  const debts = recurring.filter((r) => r.kind === "card" || r.kind === "loan");
  const otherItems = (plan ? plan.items : []).filter((i) => i.kind !== "free" && (!existing || i.id !== existing.id));
  const takenCats = new Map();
  otherItems.filter((i) => i.kind === "spend").forEach((i) => i.categories.forEach((c) => takenCats.set(c, i.title)));
  function catChipHtml(key, label) {
    const owner = takenCats.get(key);
    return `<span class="chip ${cats.has(key) ? "selected" : ""} ${owner ? "disabled" : ""}" data-cat="${escapeHtml(key)}" style="padding:6px 12px;${owner ? "opacity:.45;" : ""}" ${owner ? `title="Уже в «${escapeHtml(owner)}»"` : ""}>${label}</span>`;
  }
  const freePct = 100 - otherItems.reduce((s, i) => s + Number(i.percent), 0);
  const kind = existing ? existing.kind : "spend";
  const cats = new Set(existing ? existing.categories : []);
  const recIds = new Set(existing ? existing.recurring_ids : []);

  const overlay = openSheet(`
    <div class="sheet-title">${isEdit ? "Статья плана" : "Новая статья"}</div>
    <div class="field">
      <label>Вид</label>
      <div class="chip-row" id="pi-kind">
        ${Object.entries(KIND_LABELS).map(([k, [icon, label]]) => `
          <span class="chip ${k === kind ? "selected" : ""}" data-kind="${k}" style="padding:6px 12px;">${icon} ${label}</span>`).join("")}
      </div>
      <div class="hint-text" id="pi-kind-hint" style="margin-top:6px;"></div>
    </div>
    <div class="field-row">
      <div class="field">
        <label>Название</label>
        <input type="text" id="pi-title" value="${existing ? escapeHtml(existing.title) : ""}" placeholder="Еда, Отпуск, Кредитки…">
      </div>
      <div class="field" style="max-width:110px;">
        <label>% дохода</label>
        <input type="number" id="pi-percent" min="0" max="100" step="0.5" value="${existing ? Number(existing.percent) : ""}">
      </div>
    </div>
    <div class="hint-text" style="margin:-4px 0 12px;">Можно распределить ещё ${pctText(freePct)}.</div>
    <div class="field" data-for="spend">
      <label>Категории трат</label>
      <div class="hint-text" style="margin-bottom:8px;">Отметьте категорию целиком или нажмите ▸ и выберите только нужные подкатегории.</div>
      <div id="pi-cats">
        ${state.categoryTree.map((c) => {
          const open = c.subcategories.some((sub) => cats.has(c.name + SUB_SEP + sub));
          return `
            <div class="plan-cat-row">
              <div class="plan-cat-head">
                ${catChipHtml(c.name, `${c.icon} ${escapeHtml(c.name)}`)}
                ${c.subcategories.length ? `<button class="link-btn plan-cat-toggle" data-toggle="${escapeHtml(c.name)}" aria-label="Подкатегории">${open ? "▾" : "▸"}</button>` : ""}
              </div>
              ${c.subcategories.length ? `
                <div class="chip-row plan-subs" data-subs="${escapeHtml(c.name)}" style="${open ? "" : "display:none;"}">
                  ${c.subcategories.map((sub) => catChipHtml(c.name + SUB_SEP + sub, escapeHtml(sub))).join("")}
                </div>` : ""}
            </div>`;
        }).join("")}
      </div>
    </div>
    <div class="field" data-for="goal">
      <label>Цель</label>
      ${goals.length
        ? `<select id="pi-goal">${goals.map((g) => `<option value="${g.id}" ${existing && existing.goal_id === g.id ? "selected" : ""}>${escapeHtml(g.title)}</option>`).join("")}</select>`
        : '<div class="hint-text">Целей пока нет — сначала создайте цель во вкладке «Цели».</div>'}
    </div>
    <div class="field" data-for="debt">
      <label>Кредитки и кредиты</label>
      ${debts.length
        ? `<div class="chip-row" id="pi-debts">${debts.map((r) => `
            <span class="chip ${recIds.has(r.id) ? "selected" : ""}" data-rec="${r.id}" style="padding:6px 12px;">${r.kind === "card" ? "💳" : "🏦"} ${escapeHtml(r.title)}</span>`).join("")}</div>`
        : '<div class="hint-text">Добавьте кредитку или кредит во вкладке «Платежи».</div>'}
    </div>
    <div class="error-text" id="pi-error" style="display:none;"></div>
    <button class="btn" id="pi-submit">${isEdit ? "Сохранить" : "Добавить статью"}</button>
    ${isEdit ? '<button class="btn danger" id="pi-delete" style="margin-top:10px;">Удалить статью</button>' : ""}
  `);

  let currentKind = kind;
  function showKind() {
    overlay.querySelectorAll("#pi-kind .chip").forEach((c) => c.classList.toggle("selected", c.dataset.kind === currentKind));
    overlay.querySelectorAll("[data-for]").forEach((el) => { el.style.display = el.dataset.for === currentKind ? "" : "none"; });
    overlay.querySelector("#pi-kind-hint").textContent = KIND_LABELS[currentKind][2];
  }
  showKind();
  overlay.querySelectorAll("#pi-kind .chip").forEach((chip) => {
    chip.addEventListener("click", () => { haptic("selection"); currentKind = chip.dataset.kind; showKind(); });
  });
  overlay.querySelectorAll("#pi-cats .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      if (chip.classList.contains("disabled")) { toast(chip.getAttribute("title")); return; }
      haptic("selection");
      const name = chip.dataset.cat;
      if (cats.has(name)) cats.delete(name); else cats.add(name);
      chip.classList.toggle("selected", cats.has(name));
      // Одна категория — подставим её название, если название ещё не задано
      const titleEl = overlay.querySelector("#pi-title");
      if (!titleEl.value.trim() && cats.size === 1) titleEl.value = [...cats][0].split(SUB_SEP).pop();
    });
  });
  overlay.querySelectorAll("[data-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const subs = overlay.querySelector(`[data-subs="${CSS.escape(btn.dataset.toggle)}"]`);
      const open = subs.style.display === "none";
      subs.style.display = open ? "" : "none";
      btn.textContent = open ? "▾" : "▸";
    });
  });
  overlay.querySelectorAll("#pi-debts .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      haptic("selection");
      const id = Number(chip.dataset.rec);
      if (recIds.has(id)) recIds.delete(id); else recIds.add(id);
      chip.classList.toggle("selected", recIds.has(id));
    });
  });

  const submit = overlay.querySelector("#pi-submit");
  submit.addEventListener("click", async () => {
    const errorEl = overlay.querySelector("#pi-error");
    const title = overlay.querySelector("#pi-title").value.trim();
    const percent = Number(overlay.querySelector("#pi-percent").value || 0);
    const goalEl = overlay.querySelector("#pi-goal");
    if (!title) { showFormError(errorEl, "Укажите название статьи"); return; }
    if (!percent || percent <= 0) { showFormError(errorEl, "Укажите процент больше нуля"); return; }
    const body = {
      title, kind: currentKind, percent,
      categories: [...cats],
      goal_id: goalEl ? Number(goalEl.value) : null,
      recurring_ids: [...recIds],
    };
    if (tg && tg.MainButton) tg.MainButton.showProgress(false);
    try {
      if (isEdit) await api(`/chats/${planChatId()}/plan/items/${existing.id}`, { method: "PATCH", body });
      else await api(`/chats/${planChatId()}/plan/items`, { method: "POST", body });
      haptic("notification", "success");
      closeSheet(overlay);
      await renderPlanTab();
    } catch (e) {
      showFormError(errorEl, e.message);
    } finally {
      if (tg && tg.MainButton) tg.MainButton.hideProgress();
    }
  });
  useMainButtonFor(overlay, submit, isEdit ? "Сохранить" : "Добавить статью");
  const del = overlay.querySelector("#pi-delete");
  if (del) {
    del.addEventListener("click", async () => {
      if (!(await confirmAction(`Удалить статью «${existing.title}»? Её доля перейдёт в «Свободно».`))) return;
      try {
        await api(`/chats/${planChatId()}/plan/items/${existing.id}`, { method: "DELETE" });
        closeSheet(overlay);
        await renderPlanTab();
      } catch (e) {
        toast(e.message);
      }
    });
  }
}

/* «+» на вкладке: новая статья. План нужен, чтобы знать, сколько процентов ещё свободно
   и какие категории уже заняты. */
async function openNewPlanItem() {
  let plan = null;
  try {
    plan = await api(`/chats/${planChatId()}/plan?date=${state.planDate || todayISO()}`);
  } catch (e) {
    toast(e.message);
    return;
  }
  await openPlanItemModal(null, plan);
}

export { renderPlanTab, openNewPlanItem };
