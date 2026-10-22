import { state } from "../state.js";
import { api } from "../api.js";
import { tg, haptic, toast, confirmAction } from "../telegram.js";
import { categoryIcon, todayISO, fmtMoney, escapeHtml } from "../format.js";
import { openSheet, closeSheet, useMainButtonFor, showFormError } from "../sheet.js";

/* ---------------- Вкладка «План» ----------------
   План распределения дохода «Моих финансов» (app/shared/plan.py): каждое поступление
   делится по процентам статей, «Свободно» получает остаток процентов. По статье видно,
   сколько выделено, потрачено и осталось. В конце месяца плана статьи обнуляются, а их
   остатки (и минусы) переходят в «Свободно».

   Список компактный: статья — одна строка, те, что ушли в минус или близки к лимиту, —
   сверху. Нажатие открывает статью: цифры, «Отложил», на что ушли деньги, изменение. */

const KIND_LABELS = {
  spend: ["💸", "Расходы", "Траты выбранных категорий списываются с этой статьи. Для кредитов и кредиток выберите «Кредиты» — туда же попадут платежи по картам."],
  goal: ["🐷", "Накопления", "Выделенное откладывается на цель по кнопке «Отложил»."],
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

/* Что можно положить в статью: видимые категории «Моих финансов» и свои категории семейных
   чатов (траты по ним тоже попадают в личный бюджет вашей долей). */
function planCategories() {
  return state.categoryNodes
    .filter((n) => !n.hidden)
    .map((n) => ({ name: n.name, icon: n.icon, family: n.family, subcategories: [...n.subcategories, ...n.family_subcategories] }));
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

// ok | warn | over — как близко статья к лимиту (у накоплений «близко» не бывает)
function itemLevel(it) {
  const allocated = Number(it.allocated);
  const spent = Number(it.spent);
  if (Number(it.remaining) < 0) return "over";
  const used = allocated > 0 ? (spent / allocated) * 100 : 0;
  return used >= 80 && it.kind !== "goal" ? "warn" : "ok";
}

function usedWidth(it) {
  const allocated = Number(it.allocated);
  const spent = Number(it.spent);
  const used = allocated > 0 ? (spent / allocated) * 100 : spent > 0 ? 100 : 0;
  return Math.min(Math.max(used, 0), 100);
}

function leftHtml(it) {
  const remaining = Number(it.remaining);
  if (remaining < 0) return `<span class="plan-minus">−${fmtMoney(-remaining)}</span>`;
  return fmtMoney(remaining);
}

function itemRowHtml(it) {
  const level = itemLevel(it);
  const verb = it.kind === "goal" ? "отложено" : "потрачено";
  return `
    <div class="plan-row ${it.kind === "free" ? "plan-row-free" : ""}" data-key="${it.id ?? "free"}">
      <div class="plan-row-icon">${itemIcon(it)}</div>
      <div class="plan-row-main">
        <div class="plan-row-head">
          <span class="plan-row-title">${escapeHtml(it.title)}</span>
          <span class="plan-row-left ${level}">${leftHtml(it)}</span>
        </div>
        <div class="budget-bar plan-row-bar"><div class="budget-fill ${level}" style="width:${usedWidth(it)}%"></div></div>
        <div class="plan-row-sub hint-text">${pctText(it.percent)} · ${verb} ${fmtMoney(it.spent)} из ${fmtMoney(it.allocated)}</div>
      </div>
    </div>`;
}

// Сверху — ушедшие в минус, затем близкие к лимиту; дальше — в порядке плана
function sortedItems(items) {
  const rank = { over: 0, warn: 1, ok: 2 };
  return items.map((it, n) => ({ it, n })).sort((a, b) => rank[itemLevel(a.it)] - rank[itemLevel(b.it)] || a.n - b.n).map((x) => x.it);
}

async function renderPlanTab() {
  const content = document.getElementById("content");
  const date = state.planDate || todayISO();
  let plan;
  try {
    plan = await api(`/chats/${planChatId()}/plan?date=${date}`);
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
  // Можно тратить — остатки расходных статей и «Свободно» (накопления не трогаем)
  const totalLeft = plan.items.reduce((s, i) => s + (i.kind === "spend" || i.kind === "free" ? Number(i.remaining) : 0), 0);

  // .tab-toolbar и .dash — раскладка широкого экрана (style.css): сводка — колонкой справа
  content.innerHTML = `
    <div class="tab-toolbar">
    <div class="month-picker">
      <button data-dir="-1" aria-label="Предыдущий месяц">‹</button>
      <div class="label">${shortDate(plan.start)} – ${shortDate(plan.end)}</div>
      <button data-dir="1" aria-label="Следующий месяц">›</button>
    </div>
    </div>
    <div class="dash main-aside aside-first">
    <div class="dash-col dash-aside">
    <div class="card plan-summary">
      <div>${incomeLine}</div>
      <div class="hint-text">Можно тратить <b>${fmtMoney(totalLeft)}</b> · распределено ${pctText(plan.percent_total)}</div>
      <button class="link-btn" id="plan-settings">Месяц плана начинается ${plan.start_day}-го числа</button>
    </div>
    </div>
    <div class="dash-col">
    ${items.length === 0
      ? '<div class="empty-state">Статей пока нет.<br>Нажмите «+» и задайте, какой процент от каждого поступления идёт на еду, отпуск, кредитки и т.д. Остаток достаётся «Свободно».</div>'
      : ""}
    <div class="card plan-list">
      ${sortedItems(items).map(itemRowHtml).join("")}
      ${itemRowHtml(free)}
    </div>
    <div class="hint-text" style="margin: 4px 4px 16px;">В конце месяца плана статьи обнуляются: остатки и перерасходы переходят в «Свободно».</div>
    </div>
    </div>`;

  content.querySelectorAll(".month-picker button").forEach((b) => {
    b.addEventListener("click", async () => {
      haptic("selection");
      state.planDate = Number(b.dataset.dir) < 0 ? shiftISO(plan.start, -1) : shiftISO(plan.end, 1);
      await renderPlanTab();
    });
  });
  content.querySelector("#plan-settings").addEventListener("click", () => openPlanSettings(plan));
  content.querySelectorAll(".plan-row").forEach((el) => {
    el.addEventListener("click", () => {
      haptic("selection");
      const it = el.dataset.key === "free" ? free : items.find((i) => i.id === Number(el.dataset.key));
      openItemSheet(it, plan, isCurrent);
    });
  });
}

/* Статья: цифры, «Отложил», на что ушли деньги, изменить. */
function openItemSheet(it, plan, isCurrent) {
  const level = itemLevel(it);
  const remaining = Number(it.remaining);
  const notes = [];
  if (it.kind === "spend") notes.push(it.categories.map(escapeHtml).join(", "));
  if (it.kind === "goal" && it.goal_title) {
    notes.push(`🐷 ${escapeHtml(it.goal_title)}: ${fmtMoney(it.goal_saved)} из ${fmtMoney(it.goal_target)}`);
  }
  if (it.kind === "goal" && !it.goal_title) notes.push("Цель удалена — выберите другую");
  if (it.kind === "free") {
    const carried = Number(it.carried);
    if (carried) notes.push(`${carried > 0 ? "Перенесено" : "Перерасход"} с прошлых месяцев: ${fmtMoney(Math.abs(carried))}`);
    notes.push("Сюда списываются траты без статьи");
  }
  const canSave = it.kind === "goal" && it.goal_title && remaining > 0 && isCurrent;
  const overlay = openSheet(`
    <div class="sheet-title">${itemIcon(it)} ${escapeHtml(it.title)} <span class="plan-pct">${pctText(it.percent)}</span></div>
    <div class="ob-summary plan-figures">
      <div><div class="hint-text">Выделено</div><b>${fmtMoney(it.allocated)}</b></div>
      <div><div class="hint-text">${it.kind === "goal" ? "Отложено" : "Потрачено"}</div><b>${fmtMoney(it.spent)}</b></div>
      <div><div class="hint-text">${remaining < 0 ? "Минус" : "Осталось"}</div><b class="${remaining < 0 ? "plan-minus" : ""}">${fmtMoney(Math.abs(remaining))}</b></div>
    </div>
    <div class="budget-bar"><div class="budget-fill ${level}" style="width:${usedWidth(it)}%"></div></div>
    ${notes.map((n) => `<div class="hint-text plan-note">${n}</div>`).join("")}
    ${canSave ? `<button class="btn" id="pi-save" style="margin-top:14px;">✅ Отложил ${fmtMoney(remaining)}</button>` : ""}
    <button class="btn secondary" id="pi-entries" style="margin-top:${canSave ? 10 : 14}px;">🧾 На что ушли деньги</button>
    <div id="pi-entries-list"></div>
    ${it.kind === "free" ? "" : '<button class="btn secondary" id="pi-edit" style="margin-top:10px;">✏️ Изменить статью</button>'}`);

  const saveBtn = overlay.querySelector("#pi-save");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      saveBtn.disabled = true;
      try {
        await api(`/chats/${planChatId()}/plan/items/${it.id}/save`, { method: "POST" });
        haptic("notification", "success");
        closeSheet(overlay);
        await renderPlanTab();
      } catch (e) {
        toast(e.message);
        saveBtn.disabled = false;
      }
    });
  }
  const entriesBtn = overlay.querySelector("#pi-entries");
  const list = overlay.querySelector("#pi-entries-list");
  entriesBtn.addEventListener("click", async () => {
    haptic("selection");
    if (list.dataset.loaded) {
      const open = list.style.display === "none";
      list.style.display = open ? "" : "none";
      entriesBtn.textContent = open ? "🧾 Скрыть траты" : "🧾 На что ушли деньги";
      return;
    }
    entriesBtn.disabled = true;
    try {
      const query = `date=${plan.start}${it.id ? `&item_id=${it.id}` : ""}`;
      list.innerHTML = entriesHtml(await api(`/chats/${planChatId()}/plan/entries?${query}`));
      list.dataset.loaded = "1";
      entriesBtn.textContent = "🧾 Скрыть траты";
    } catch (e) {
      toast(e.message);
    } finally {
      entriesBtn.disabled = false;
    }
  });
  const editBtn = overlay.querySelector("#pi-edit");
  if (editBtn) {
    editBtn.addEventListener("click", () => {
      closeSheet(overlay);
      openPlanItemModal(it, plan);
    });
  }
}

const SOURCE_NOTE = { card: "платёж по карте", deposit: "отложено" };

function entriesHtml(entries) {
  if (!entries.length) return '<div class="hint-text plan-entries-empty">В этом месяце плана списаний не было.</div>';
  return `
    <div class="plan-entries">
      ${entries.map((e) => {
        const note = SOURCE_NOTE[e.source] || (e.subcategory ? e.subcategory : e.category !== e.title ? e.category : "");
        return `
          <div class="plan-entry">
            <span class="plan-entry-date hint-text">${shortDate(e.date)}</span>
            <span class="plan-entry-title">${escapeHtml(e.title)}${note ? ` <span class="hint-text">· ${escapeHtml(note)}</span>` : ""}</span>
            <span class="plan-entry-amount">${fmtMoney(e.amount)}</span>
          </div>`;
      }).join("")}
    </div>`;
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
  let goals = [];
  try {
    goals = await api(`/chats/${planChatId()}/goals`);
  } catch (e) {
    toast(e.message);
    return;
  }
  goals = goals.filter((g) => !g.is_archived || (existing && existing.goal_id === g.id));
  const otherItems = (plan ? plan.items : []).filter((i) => i.kind !== "free" && (!existing || i.id !== existing.id));
  const takenCats = new Map();
  otherItems.filter((i) => i.kind === "spend").forEach((i) => i.categories.forEach((c) => takenCats.set(c, i.title)));
  function catChipHtml(key, label) {
    const owner = takenCats.get(key);
    return `<span class="chip ${owner ? "disabled" : ""}" data-cat="${escapeHtml(key)}" style="padding:6px 12px;${owner ? "opacity:.45;" : ""}" ${owner ? `title="Уже в «${escapeHtml(owner)}»"` : ""}>${label}</span>`;
  }
  const freePct = 100 - otherItems.reduce((s, i) => s + Number(i.percent), 0);
  const kind = existing && KIND_LABELS[existing.kind] ? existing.kind : "spend";
  const cats = new Set(existing ? existing.categories : []);
  const catList = planCategories();

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
      <div class="hint-text" style="margin-bottom:8px;">Категория отмечается со всеми подкатегориями, в том числе с будущими. Нажмите ▸ и снимите лишние, если нужны не все.</div>
      <div id="pi-cats">
        ${catList.map((c) => {
          const open = cats.has(c.name) || c.subcategories.some((sub) => cats.has(c.name + SUB_SEP + sub));
          return `
            <div class="plan-cat-row">
              <div class="plan-cat-head">
                ${catChipHtml(c.name, `${c.icon} ${escapeHtml(c.name)}${c.family ? ` <span class="hint-text">🏠 ${escapeHtml(c.family)}</span>` : ""}`)}
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
  /* Категория целиком = все её подкатегории отмечены. Сняли одну подкатегорию — в статье
     остаются остальные; отметили все — снова вся категория. Подкатегории, занятые другой
     статьёй, не трогаем: трата по ним всё равно идёт в ту статью. */
  function freeSubs(c) {
    return c.subcategories.filter((sub) => !takenCats.has(c.name + SUB_SEP + sub));
  }
  function refreshChips() {
    catList.forEach((c) => {
      const whole = cats.has(c.name);
      overlay.querySelectorAll(`[data-cat="${CSS.escape(c.name)}"], [data-cat^="${CSS.escape(c.name + SUB_SEP)}"]`).forEach((chip) => {
        const key = chip.dataset.cat;
        const isSub = key !== c.name;
        const on = !chip.classList.contains("disabled") && (whole || (isSub && cats.has(key)));
        chip.classList.toggle("selected", on);
      });
    });
  }
  function toggleCategory(c) {
    const subsOn = c.subcategories.some((sub) => cats.has(c.name + SUB_SEP + sub));
    const on = !cats.has(c.name) && !subsOn;
    c.subcategories.forEach((sub) => cats.delete(c.name + SUB_SEP + sub));
    if (on) cats.add(c.name); else cats.delete(c.name);
    if (on && c.subcategories.length) {
      overlay.querySelector(`[data-subs="${CSS.escape(c.name)}"]`).style.display = "";
      overlay.querySelector(`[data-toggle="${CSS.escape(c.name)}"]`).textContent = "▾";
    }
  }
  function toggleSub(c, key) {
    if (cats.has(c.name)) {
      cats.delete(c.name);
      freeSubs(c).forEach((sub) => { if (c.name + SUB_SEP + sub !== key) cats.add(c.name + SUB_SEP + sub); });
    } else if (cats.has(key)) {
      cats.delete(key);
    } else {
      cats.add(key);
      const all = freeSubs(c).every((sub) => cats.has(c.name + SUB_SEP + sub));
      if (all && !takenCats.has(c.name)) {
        c.subcategories.forEach((sub) => cats.delete(c.name + SUB_SEP + sub));
        cats.add(c.name);
      }
    }
  }
  refreshChips();
  overlay.querySelectorAll("#pi-cats .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      if (chip.classList.contains("disabled")) { toast(chip.getAttribute("title")); return; }
      haptic("selection");
      const key = chip.dataset.cat;
      const c = catList.find((x) => x.name === key || key.startsWith(x.name + SUB_SEP));
      if (key === c.name) toggleCategory(c); else toggleSub(c, key);
      refreshChips();
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
