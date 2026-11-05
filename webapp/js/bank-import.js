import { state } from "./state.js";
import { api } from "./api.js";
import { haptic, toast } from "./telegram.js";
import { categoryIcon, fmtMoney, escapeHtml } from "./format.js";
import { openSheet, closeSheet } from "./sheet.js";

/* «📥 Выписка» в «Моих финансах»: PDF-справка о движении средств Т-Банка → предпросмотр →
   траты прошлых месяцев для статистики. Берём только покупки: переводы, копилку, наличные и
   поступления сервер пропускает, возвраты вычитает. Покупки, уже внесённые вручную (та же
   сумма, дата ±2 дня), не импортируются. Что не опознали — по умолчанию не отмечено, можно
   выбрать категорию самому. Сервер файл не хранит. */

const SKIPPED = {
  savings: "копилка",
  self: "переводы себе",
  contract: "переводы между договорами",
  transfer: "переводы по телефону",
  cash: "наличные",
  income: "поступления",
  refund: "возвраты без покупки",
  unknown: "непонятные",
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
        Справка о движении средств). Возьму только покупки: переводы, копилку и наличные пропущу.
        Перед добавлением покажу, что получилось. Файл нигде не сохраняется.
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
    content.innerHTML = `<div class="hint-text">✨ Разбираю выписку и подбираю категории…</div>`;
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
    // Дубли не импортируем никогда; группа — только её новые покупки
    const groups = preview.groups.map((g) => {
      const fresh = g.purchases.filter((p) => !p.duplicate_title);
      const choice = g.category ? choices.findIndex((c) => c.category === g.category && c.subcategory === (g.subcategory || null)) : -1;
      return { ...g, fresh, choice, checked: choice >= 0 && fresh.length > 0 };
    });
    const all = groups.flatMap((g) => g.purchases);
    const dupes = all.filter((p) => p.duplicate_title);
    const total = all.reduce((s, p) => s + Number(p.amount), 0);
    const skipped = preview.skipped.filter((s) => s.count)
      .map((s) => `${SKIPPED[s.kind] || s.kind} ${s.count}`).join(", ");

    function optionsHtml(selected) {
      return `<option value="-1" ${selected < 0 ? "selected" : ""}>— не добавлять —</option>` + choices.map((c, i) =>
        `<option value="${i}" ${i === selected ? "selected" : ""}>${c.subcategory
          ? `&nbsp;&nbsp;&nbsp;${escapeHtml(c.category)} › ${escapeHtml(c.subcategory)}`
          : `${categoryIcon(c.category)} ${escapeHtml(c.category)}`}</option>`).join("");
    }

    function groupHtml(g, n) {
      const sum = g.purchases.reduce((s, p) => s + Number(p.amount), 0);
      const dupCount = g.purchases.length - g.fresh.length;
      return `
        <div class="cat-row" style="flex-direction:column; align-items:stretch; gap:6px;">
          <label style="display:flex; gap:8px; align-items:flex-start;">
            <input type="checkbox" data-n="${n}" ${g.checked ? "checked" : ""} ${g.fresh.length ? "" : "disabled"}>
            <span style="flex:1;"><b>${escapeHtml(g.title)}</b>${g.purchases.length > 1 ? ` ×${g.purchases.length}` : ""} · ${fmtMoney(sum)}
              ${dupCount ? `<br><span class="hint-text">уже внесено: ${dupCount}</span>` : ""}
              ${g.source === "ai" ? `<span class="hint-text"> · ✨ ИИ</span>` : ""}</span>
          </label>
          <select data-cat="${n}">${optionsHtml(g.choice)}</select>
          <details><summary class="hint-text">Операции</summary>
            ${g.purchases.map((p) => `<div class="hint-text">${dateLabel(p.date)} · ${fmtMoney(p.amount)} · ${escapeHtml(p.description)}${p.duplicate_title
              ? ` — <b>уже есть</b> «${escapeHtml(p.duplicate_title)}» ${dateLabel(p.duplicate_date)}` : ""}</div>`).join("")}
          </details>
        </div>`;
    }

    const period = preview.period_from ? `${dateLabel(preview.period_from)} — ${dateLabel(preview.period_to)}` : "";
    content.innerHTML = `
      <div style="margin-bottom:8px;"><b>${period}</b><br>
        ${all.length} ${plural(all.length, "покупка", "покупки", "покупок")} на ${fmtMoney(total)}${dupes.length ? `, из них ${dupes.length} уже внесены — их пропущу` : ""}.</div>
      ${skipped ? `<div class="hint-text" style="margin-bottom:8px;">Не беру: ${escapeHtml(skipped)}.</div>` : ""}
      <div class="hint-text" style="margin-bottom:6px;">Отмечено то, что удалось опознать. Категорию можно поменять — запомню на будущее.</div>
      ${groups.length ? groups.map(groupHtml).join("") : `<div class="hint-text">Покупок в выписке нет.</div>`}
      <button class="btn" id="bank-apply" style="margin-top:12px;"></button>`;

    const applyBtn = content.querySelector("#bank-apply");
    function refresh() {
      const rows = groups.filter((g) => g.checked && g.choice >= 0).reduce((s, g) => s + g.fresh.length, 0);
      applyBtn.disabled = !rows;
      applyBtn.textContent = rows ? `Добавить ${rows} ${plural(rows, "трату", "траты", "трат")}` : "Нечего добавлять";
    }
    content.querySelectorAll("input[data-n]").forEach((el) => el.addEventListener("change", () => {
      const g = groups[Number(el.dataset.n)];
      g.checked = el.checked;
      if (g.checked && g.choice < 0) {
        // Отметили неопознанное — сначала категория
        g.checked = false;
        el.checked = false;
        toast("Выберите категорию");
      }
      refresh();
    }));
    content.querySelectorAll("select[data-cat]").forEach((el) => el.addEventListener("change", () => {
      const n = Number(el.dataset.cat);
      const g = groups[n];
      g.choice = Number(el.value);
      g.checked = g.choice >= 0 && g.fresh.length > 0;
      content.querySelector(`input[data-n="${n}"]`).checked = g.checked;
      refresh();
    }));
    applyBtn.addEventListener("click", () => apply(groups, applyBtn));
    refresh();
  }

  async function apply(groups, btn) {
    const rows = groups.filter((g) => g.checked && g.choice >= 0).flatMap((g) => g.fresh.map((p) => ({
      date: p.date,
      amount: p.amount,
      title: g.title,
      category: choices[g.choice].category,
      subcategory: choices[g.choice].subcategory,
    })));
    if (!rows.length) return;
    btn.disabled = true;
    btn.textContent = "Добавляю…";
    try {
      const r = await api(`/chats/${state.personalChatId}/bank-import`, { method: "POST", body: { rows } });
      closeSheet(sheet);
      haptic("notification", "success");
      toast(`Добавил ${r.created} ${plural(r.created, "трату", "траты", "трат")}`);
      if (onDone) onDone(rows);
    } catch (e) {
      btn.disabled = false;
      toast(e.message);
    }
  }

  pickStep();
}

export { openBankImportSheet };
