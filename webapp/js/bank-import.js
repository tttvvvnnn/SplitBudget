import { state } from "./state.js";
import { api } from "./api.js";
import { haptic, toast } from "./telegram.js";
import { categoryIcon, fmtMoney, escapeHtml } from "./format.js";
import { openSheet, closeSheet } from "./sheet.js";

/* «📥 Выписка» в «Моих финансах»: PDF-справка о движении средств Т-Банка → предпросмотр →
   траты и доходы прошлых месяцев. Сервер пропускает переводы, копилку и наличные, вычитает
   возвраты и находит совпадения с уже внесённым (сумма с разбросом, дата, название или
   категория). Совпадения показываем парами: оставить моё, взять из выписки (моя трата получит
   сумму, дату и название из выписки) или оставить обе. Неопознанные покупки по умолчанию не
   отмечены — можно выбрать категорию самому. Сервер файл не хранит. */

const SKIPPED = {
  savings: "копилка",
  self: "переводы себе",
  contract: "переводы между договорами",
  transfer: "переводы по телефону",
  cash: "наличные",
  refund: "возвраты без покупки",
  unknown: "непонятные",
};

const LEVEL = {
  exact: "та же сумма и дата",
  same_sum: "та же сумма и дата, название другое",
  likely: "похожая сумма и название",
  maybe: "почти та же сумма в тот же день",
};

function dateLabel(iso) {
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

/* Все варианты «категория» и «категория › подкатегория» одним списком */
function categoryChoices() {
  const out = [];
  for (const c of state.categoryTree) {
    out.push({ category: c.name, subcategory: null });
    for (const s of c.subcategories) out.push({ category: c.name, subcategory: s });
  }
  return out;
}

function openBankImportSheet(onDone) {
  const sheet = openSheet(`
    <div class="sheet-title">📥 Импорт выписки</div>
    <div id="bank-content"></div>`);
  const content = sheet.querySelector("#bank-content");
  const choices = categoryChoices();

  function pickStep(error) {
    content.innerHTML = `
      <div class="hint-text" style="margin-bottom:12px;">
        Справка о движении средств Т-Банка в PDF (в приложении банка: счёт → Выписки и справки →
        Справка о движении средств), можно сразу за несколько месяцев. Возьму покупки и
        поступления, переводы, копилку и наличные пропущу. Если что-то уже внесено, покажу пары —
        решишь, что оставить. Файл нигде не сохраняется.
      </div>
      ${error ? `<div class="error-text" style="margin-bottom:10px;">${escapeHtml(error)}</div>` : ""}
      <label class="btn" style="display:block; text-align:center;">
        Выбрать PDF<input type="file" id="bank-file" accept="application/pdf,.pdf" style="display:none;">
      </label>`;
    content.querySelector("#bank-file").addEventListener("change", (ev) => {
      const file = ev.target.files[0];
      if (file) upload(file);
    });
  }

  async function upload(file) {
    content.innerHTML = `<div class="hint-text">✨ Разбираю выписку, ищу совпадения и подбираю категории…</div>`;
    const form = new FormData();
    form.append("file", file);
    try {
      const preview = await api(`/chats/${state.personalChatId}/bank-import/preview`, { method: "POST", body: form, isForm: true });
      previewStep(preview);
    } catch (e) {
      pickStep(e.message);
    }
  }

  function previewStep(preview) {
    const groups = preview.groups.map((g) => {
      const choice = g.category ? choices.findIndex((c) => c.category === g.category && c.subcategory === (g.subcategory || null)) : -1;
      const purchases = g.purchases.map((p) => ({ ...p, choice: p.match ? p.match.default : null }));
      return { ...g, purchases, choice, checked: choice >= 0 };
    });
    const incomes = preview.incomes.map((i) => ({ ...i, choice: i.match ? i.match.default : null }));
    // Пары «из выписки ⇄ моё» — сначала уверенные
    const matches = [
      ...groups.flatMap((g) => g.purchases.filter((p) => p.match).map((p) => ({ kind: "expense", item: p, group: g }))),
      ...incomes.filter((i) => i.match).map((i) => ({ kind: "income", item: i })),
    ];
    const order = { exact: 0, same_sum: 1, likely: 2, maybe: 3 };
    matches.sort((a, b) => order[a.item.match.level] - order[b.item.match.level] || a.item.date.localeCompare(b.item.date));

    const fresh = (list) => list.filter((x) => !x.match || x.choice === "both");
    const allPurchases = groups.flatMap((g) => g.purchases);
    const total = allPurchases.reduce((s, p) => s + Number(p.amount), 0);
    const incomeTotal = incomes.reduce((s, i) => s + Number(i.amount), 0);
    const skipped = preview.skipped.filter((s) => s.count).map((s) => `${SKIPPED[s.kind] || s.kind} ${s.count}`).join(", ");

    function optionsHtml(selected) {
      return `<option value="-1" ${selected < 0 ? "selected" : ""}>— не добавлять —</option>` + choices.map((c, i) =>
        `<option value="${i}" ${i === selected ? "selected" : ""}>${c.subcategory
          ? `&nbsp;&nbsp;&nbsp;${escapeHtml(c.category)} › ${escapeHtml(c.subcategory)}`
          : `${categoryIcon(c.category)} ${escapeHtml(c.category)}`}</option>`).join("");
    }

    function choiceButtons(n, m) {
      const opts = [["mine", "Моё"], ["statement", "Из выписки"], ["both", "Обе"]];
      return `<div class="segmented" style="display:flex; gap:6px; margin-top:6px;">${opts.map(([v, label]) => `
        <button type="button" class="btn small ${m.item.choice === v ? "" : "secondary"}" data-m="${n}" data-v="${v}"
          ${v === "statement" && !m.item.match.editable ? "disabled title=\"Трата из семейного чата — её меняют там\"" : ""}
          style="flex:1;">${label}</button>`).join("")}</div>`;
    }

    function matchHtml(m, n) {
      const it = m.item;
      const mine = it.match;
      const title = m.kind === "expense" ? m.group.title : it.title;
      return `
        <div class="cat-row" style="flex-direction:column; align-items:stretch; gap:2px;">
          <div><span class="hint-text">${m.kind === "expense" ? "🛒" : "💰"} Выписка:</span> <b>${escapeHtml(title)}</b> · ${fmtMoney(it.amount)} · ${dateLabel(it.date)}</div>
          <div><span class="hint-text">У тебя:</span> ${escapeHtml(mine.title)} · ${fmtMoney(mine.amount)} · ${dateLabel(mine.date)}${mine.space ? ` · ${escapeHtml(mine.space)}` : ""}</div>
          <div class="hint-text">${LEVEL[mine.level] || ""}</div>
          ${choiceButtons(n, m)}
        </div>`;
    }

    function groupHtml(g, n) {
      const list = fresh(g.purchases);
      const sum = list.reduce((s, p) => s + Number(p.amount), 0);
      return `
        <div class="cat-row" style="flex-direction:column; align-items:stretch; gap:6px;" data-group="${n}">
          <label style="display:flex; gap:8px; align-items:flex-start;">
            <input type="checkbox" data-n="${n}" ${g.checked && list.length ? "checked" : ""} ${list.length ? "" : "disabled"}>
            <span style="flex:1;"><b>${escapeHtml(g.title)}</b>${list.length > 1 ? ` ×${list.length}` : ""} · ${fmtMoney(sum)}
              ${g.source === "ai" ? `<span class="hint-text"> · ✨ ИИ</span>` : ""}
              ${list.length ? "" : `<br><span class="hint-text">всё уже внесено</span>`}</span>
          </label>
          <select data-cat="${n}">${optionsHtml(g.choice)}</select>
          <details><summary class="hint-text">Операции</summary>
            ${g.purchases.map((p) => `<div class="hint-text">${dateLabel(p.date)} · ${fmtMoney(p.amount)} · ${escapeHtml(p.description)}${p.match && p.choice !== "both"
              ? ` — <b>${p.choice === "mine" ? "оставлю твою" : "обновлю твою"}</b> «${escapeHtml(p.match.title)}»` : ""}</div>`).join("")}
          </details>
        </div>`;
    }

    function incomeHtml(i, n) {
      if (i.match && i.choice !== "both") return "";
      return `
        <div class="cat-row" style="gap:8px; align-items:center;">
          <input type="checkbox" data-inc="${n}" ${i.checked ? "checked" : ""}>
          <div style="flex:1; min-width:0;">
            <input type="text" data-inc-title="${n}" value="${escapeHtml(i.title)}" style="width:100%;">
            <div class="hint-text">${dateLabel(i.date)} · ${escapeHtml(i.description)}${i.source_id ? " · 🔁 регулярный доход" : ""}</div>
          </div>
          <b style="white-space:nowrap;">${fmtMoney(i.amount)}</b>
        </div>`;
    }

    function render() {
      const period = preview.period_from ? `${dateLabel(preview.period_from)} — ${dateLabel(preview.period_to)}` : "";
      content.innerHTML = `
        <div style="margin-bottom:8px;"><b>${period}</b><br>
          ${allPurchases.length} ${plural(allPurchases.length, "покупка", "покупки", "покупок")} на ${fmtMoney(total)},
          ${incomes.length} ${plural(incomes.length, "поступление", "поступления", "поступлений")} на ${fmtMoney(incomeTotal)}.</div>
        ${skipped ? `<div class="hint-text" style="margin-bottom:8px;">Не беру: ${escapeHtml(skipped)}.</div>` : ""}
        ${matches.length ? `
          <div class="sheet-subtitle" style="margin-top:14px; font-weight:600;">Уже внесено? ${matches.length}</div>
          <div class="hint-text" style="margin-bottom:6px;">Похоже на то, что ты уже записал. «Моё» — оставить твою запись, «Из выписки» — обновить её суммой, датой и названием из выписки, «Обе» — добавить ещё и из выписки.</div>
          <button type="button" class="btn small secondary" id="all-mine">Везде оставить моё</button>
          ${matches.map(matchHtml).join("")}` : ""}
        <div class="sheet-subtitle" style="margin-top:14px; font-weight:600;">Траты</div>
        <div class="hint-text" style="margin-bottom:6px;">Отмечено то, что удалось опознать. Категорию можно поменять — запомню на будущее.</div>
        ${groups.length ? groups.map(groupHtml).join("") : `<div class="hint-text">Покупок в выписке нет.</div>`}
        ${incomes.length ? `
          <div class="sheet-subtitle" style="margin-top:14px; font-weight:600;">Доходы</div>
          <div class="hint-text" style="margin-bottom:6px;">Зарплата и кэшбэк отмечены. Входящие переводы — нет: это могут быть и твои деньги из другого банка. Название можно поправить.</div>
          ${incomes.map(incomeHtml).join("")}` : ""}
        <button class="btn" id="bank-apply" style="margin-top:12px;"></button>`;
      bind();
      refresh();
    }

    function payload() {
      const rows = groups.filter((g) => g.checked && g.choice >= 0).flatMap((g) => fresh(g.purchases).map((p) => ({
        date: p.date,
        amount: p.amount,
        title: g.title,
        category: choices[g.choice].category,
        subcategory: choices[g.choice].subcategory,
      })));
      const updates = groups.flatMap((g) => g.purchases.filter((p) => p.match && p.choice === "statement").map((p) => ({
        expense_id: p.match.id,
        date: p.date,
        amount: p.amount,
        title: g.title,
        category: g.choice >= 0 ? choices[g.choice].category : null,
        subcategory: g.choice >= 0 ? choices[g.choice].subcategory : null,
      })));
      const newIncomes = fresh(incomes).filter((i) => i.checked).map((i) => ({
        date: i.date, amount: i.amount, title: i.title.trim() || "Доход", source_id: i.source_id,
      }));
      const incomeUpdates = incomes.filter((i) => i.match && i.choice === "statement").map((i) => ({
        income_id: i.match.id, date: i.date, amount: i.amount,
      }));
      return { rows, updates, incomes: newIncomes, income_updates: incomeUpdates };
    }

    function refresh() {
      const p = payload();
      const parts = [];
      if (p.rows.length) parts.push(`${p.rows.length} ${plural(p.rows.length, "трату", "траты", "трат")}`);
      if (p.incomes.length) parts.push(`${p.incomes.length} ${plural(p.incomes.length, "доход", "дохода", "доходов")}`);
      const upd = p.updates.length + p.income_updates.length;
      const btn = content.querySelector("#bank-apply");
      btn.disabled = !parts.length && !upd;
      btn.textContent = !parts.length && !upd ? "Нечего добавлять"
        : `${parts.length ? `Добавить ${parts.join(" и ")}` : ""}${upd ? `${parts.length ? ", о" : "О"}бновить ${upd}` : ""}`;
    }

    // Перерисовка не должна прыгать в начало длинного списка
    function keepScroll(fn) {
      const scroller = sheet.querySelector(".sheet") || sheet;
      const top = scroller.scrollTop;
      fn();
      scroller.scrollTop = top;
    }

    function bind() {
      content.querySelectorAll("button[data-m]").forEach((el) => el.addEventListener("click", () => {
        haptic("selection");
        matches[Number(el.dataset.m)].item.choice = el.dataset.v;
        keepScroll(render);
      }));
      const allMine = content.querySelector("#all-mine");
      if (allMine) allMine.addEventListener("click", () => {
        for (const m of matches) m.item.choice = "mine";
        keepScroll(render);
      });
      content.querySelectorAll("input[data-n]").forEach((el) => el.addEventListener("change", () => {
        const g = groups[Number(el.dataset.n)];
        g.checked = el.checked;
        if (g.checked && g.choice < 0) {
          g.checked = false; // отметили неопознанное — сначала категория
          el.checked = false;
          toast("Выберите категорию");
        }
        refresh();
      }));
      content.querySelectorAll("select[data-cat]").forEach((el) => el.addEventListener("change", () => {
        const n = Number(el.dataset.cat);
        const g = groups[n];
        g.choice = Number(el.value);
        g.checked = g.choice >= 0;
        const box = content.querySelector(`input[data-n="${n}"]`);
        if (box && !box.disabled) box.checked = g.checked;
        refresh();
      }));
      content.querySelectorAll("input[data-inc]").forEach((el) => el.addEventListener("change", () => {
        incomes[Number(el.dataset.inc)].checked = el.checked;
        refresh();
      }));
      content.querySelectorAll("input[data-inc-title]").forEach((el) => el.addEventListener("input", () => {
        incomes[Number(el.dataset.incTitle)].title = el.value;
      }));
      content.querySelector("#bank-apply").addEventListener("click", (ev) => apply(payload(), ev.target));
    }

    render();
  }

  async function apply(body, btn) {
    btn.disabled = true;
    btn.textContent = "Записываю…";
    try {
      const r = await api(`/chats/${state.personalChatId}/bank-import`, { method: "POST", body });
      closeSheet(sheet);
      haptic("notification", "success");
      const parts = [];
      if (r.created) parts.push(`трат: ${r.created}`);
      if (r.incomes) parts.push(`доходов: ${r.incomes}`);
      if (r.updated) parts.push(`обновлено: ${r.updated}`);
      if (r.payments) parts.push(`платежей отмечено оплаченными: ${r.payments}`);
      toast(`Готово. ${parts.join(", ")}`);
      const dates = [...body.rows, ...body.incomes].map((x) => x.date).sort();
      if (onDone) onDone(dates);
    } catch (e) {
      btn.disabled = false;
      toast(e.message);
    }
  }

  pickStep();
}

export { openBankImportSheet };
