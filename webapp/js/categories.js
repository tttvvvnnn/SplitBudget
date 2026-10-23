import { state } from "./state.js";
import { api } from "./api.js";
import { haptic, toast, confirmAction } from "./telegram.js";
import { escapeHtml, fmtMoney, categoryIcon } from "./format.js";
import { openSheet, closeSheet, openTextPromptModal } from "./sheet.js";

/* Свои категории и подкатегории пространства (app/shared/custom_categories.py).
   state.categoryNodes — всё дерево с сервера (со скрытыми и, в «Моих финансах», с
   категориями семейных чатов), state.categoryTree — то, что предлагаем при выборе
   категории траты: видимые категории этого пространства. */

function setCategoryNodes(nodes) {
  state.categoryNodes = nodes;
  state.categoryTree = nodes
    .filter((n) => !n.hidden && !n.family)
    .map((n) => ({ name: n.name, icon: n.icon, subcategories: n.subcategories }));
  state.categories = nodes.filter((n) => !n.hidden).map((n) => n.name);
}

async function loadCategories() {
  setCategoryNodes(await api(`/chats/${state.chatId}/categories`));
}

/* «🏍 Мото» — эмодзи в начале станет иконкой категории. */
function parseIconName(text) {
  const m = text.match(/^(\p{Extended_Pictographic}[️‍\p{Extended_Pictographic}\p{Emoji_Modifier}]*)\s*(.+)$/u);
  return m ? { icon: m[1], name: m[2].trim() } : { icon: null, name: text.trim() };
}

/* Спросить название и создать категорию (parent = null) или подкатегорию. onCreated(name)
   вызывается после того, как дерево уже обновлено. */
function promptNewCategory(parent, onCreated) {
  openTextPromptModal({
    title: parent ? `Подкатегория в «${parent}»` : "Своя категория",
    hint: parent ? "Будет только в этом пространстве." : "Будет только в этом пространстве. Можно начать с эмодзи — он станет иконкой: «🏍 Мото».",
    label: "Название",
    placeholder: parent ? "Например, Мойка" : "🏍 Мото",
    submitLabel: "Добавить",
    onSubmit: async (value) => {
      const { icon, name } = parent ? { icon: null, name: value } : parseIconName(value);
      setCategoryNodes(await api(`/chats/${state.chatId}/categories`, { method: "POST", body: { name, icon, parent } }));
      if (onCreated) onCreated(name);
    },
  });
}

/* Экран «Категории»: свои добавить/переименовать/удалить, любые скрыть/вернуть. */
function openCategoriesSheet(onChange) {
  let overlay = null;
  const expanded = new Set();

  // name/parent — для «Скрыть»: parent пустой у категории
  function rowHtml(label, attrs, { custom, hidden, id, name, parent }) {
    return `
      <div class="cat-row ${hidden ? "hidden-cat" : ""} ${parent ? "cat-sub" : ""}">
        <span class="cat-name" ${attrs}>${label}${custom ? "" : ' <span class="hint-text">· базовая</span>'}</span>
        <span class="cat-actions">
          ${custom ? `<button class="link-btn" data-edit="${id}">✏️</button>` : ""}
          ${name === "Другое" && !parent ? "" : `<button class="link-btn" data-hide-name="${escapeHtml(name)}" data-hide-parent="${escapeHtml(parent)}" data-hidden="${hidden ? 1 : 0}">${hidden ? "Вернуть" : "Скрыть"}</button>`}
        </span>
      </div>`;
  }

  function html() {
    const nodes = state.categoryNodes.filter((n) => !n.family);
    return `
      <div class="sheet-title">Категории</div>
      <div class="hint-text" style="margin-bottom:12px;">Свои категории есть только в этом пространстве. Базовые можно скрыть: старые траты в них останутся. Свою категорию можно удалить, если в ней нет трат.</div>
      ${state.aiAvailable ? `
        <label class="field" style="display:flex; gap:8px; align-items:flex-start; margin-bottom:12px;">
          <input type="checkbox" id="cat-ai" ${state.chat.ai_enabled ? "checked" : ""}>
          <span>✨ Подсказки ИИ<br><span class="hint-text">Если категорию не угадать по прошлым тратам и словарю, название траты (без суммы) отправляется ИИ. Он подберёт категорию или предложит создать новую.</span></span>
        </label>` : ""}
      <button class="btn secondary" id="cat-add">➕ Своя категория</button>
      ${state.aiAvailable && state.chat.ai_enabled ? `<button class="btn secondary" id="cat-tidy" style="margin-top:8px;">✨ Разобрать «Другое»</button>` : ""}
      <div class="cat-list">
        ${nodes.map((n) => {
          const open = expanded.has(n.name);
          return `
            <div class="cat-block">
              ${rowHtml(`<span class="cat-toggle">${open ? "▾" : "▸"}</span> ${n.icon} ${escapeHtml(n.name)}`,
                `data-open="${escapeHtml(n.name)}"`, { custom: n.custom, hidden: n.hidden, id: n.id, name: n.name, parent: "" })}
              ${open ? `
                <div class="cat-subs">
                  ${n.all_subcategories.map((s) => rowHtml(escapeHtml(s.name), "", { custom: s.custom, hidden: s.hidden, id: s.id, name: s.name, parent: n.name })).join("")}
                  <button class="link-btn" data-add-sub="${escapeHtml(n.name)}" style="margin:4px 0 8px 18px;">➕ Подкатегория</button>
                </div>` : ""}
            </div>`;
        }).join("")}
      </div>`;
  }

  function changed(nodes) {
    if (nodes) setCategoryNodes(nodes);
    render();
    if (onChange) onChange();
  }

  function render() {
    const sheet = overlay.querySelector(".sheet");
    const scroll = sheet.scrollTop;
    overlay.querySelector("#cat-content").innerHTML = html();
    sheet.scrollTop = scroll;
    bind();
  }

  function bind() {
    overlay.querySelector("#cat-add").addEventListener("click", () => promptNewCategory(null, () => changed()));
    const tidyBtn = overlay.querySelector("#cat-tidy");
    if (tidyBtn) tidyBtn.addEventListener("click", () => openTidySheet((nodes) => changed(nodes)));
    const aiBox = overlay.querySelector("#cat-ai");
    if (aiBox) {
      aiBox.addEventListener("change", async () => {
        try {
          state.chat = await api(`/chats/${state.chatId}/ai`, { method: "PUT", body: { ai_enabled: aiBox.checked } });
          haptic("selection");
          render(); // показать/спрятать «Разобрать «Другое»»
        } catch (e) {
          aiBox.checked = !aiBox.checked;
          toast(e.message);
        }
      });
    }
    overlay.querySelectorAll("[data-open]").forEach((el) => {
      el.addEventListener("click", () => {
        haptic("selection");
        const name = el.dataset.open;
        if (expanded.has(name)) expanded.delete(name); else expanded.add(name);
        render();
      });
    });
    overlay.querySelectorAll("[data-add-sub]").forEach((btn) => {
      btn.addEventListener("click", () => promptNewCategory(btn.dataset.addSub, () => changed()));
    });
    overlay.querySelectorAll("[data-hide-name]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const body = { name: btn.dataset.hideName, parent: btn.dataset.hideParent || null, hidden: btn.dataset.hidden !== "1" };
        try {
          changed(await api(`/chats/${state.chatId}/categories/hidden`, { method: "PUT", body }));
          haptic("notification", "success");
        } catch (e) {
          toast(e.message);
        }
      });
    });
    overlay.querySelectorAll("[data-edit]").forEach((btn) => {
      btn.addEventListener("click", () => openEdit(Number(btn.dataset.edit)));
    });
  }

  function findCustom(id) {
    for (const n of state.categoryNodes) {
      if (n.custom && n.id === id && !n.family) return { name: n.name, icon: n.icon, parent: null };
      const s = n.all_subcategories.find((x) => x.custom && x.id === id);
      if (s) return { name: s.name, icon: null, parent: n.name };
    }
    return null;
  }

  function openEdit(id) {
    const item = findCustom(id);
    if (!item) return;
    const edit = openSheet(`
      <div class="sheet-title">${item.parent ? "Подкатегория" : "Категория"}</div>
      ${item.parent ? "" : `
        <div class="field" style="max-width:110px;">
          <label>Иконка</label>
          <input type="text" id="ce-icon" maxlength="8" value="${escapeHtml(item.icon || "")}">
        </div>`}
      <div class="field">
        <label>Название</label>
        <input type="text" id="ce-name" maxlength="64" value="${escapeHtml(item.name)}">
      </div>
      <div class="hint-text" style="margin-bottom:12px;">Траты и статьи плана с этой категорией переименуются вместе с ней.</div>
      <div class="error-text" id="ce-error" style="display:none;"></div>
      <button class="btn" id="ce-save">Сохранить</button>
      <button class="btn danger" id="ce-delete" style="margin-top:10px;">Удалить</button>`);
    edit.querySelector("#ce-save").addEventListener("click", async () => {
      const iconEl = edit.querySelector("#ce-icon");
      const body = { name: edit.querySelector("#ce-name").value.trim(), icon: iconEl ? iconEl.value.trim() || null : null };
      try {
        const nodes = await api(`/chats/${state.chatId}/categories/${id}`, { method: "PATCH", body });
        closeSheet(edit);
        changed(nodes);
      } catch (e) {
        const err = edit.querySelector("#ce-error");
        err.textContent = e.message;
        err.style.display = "block";
      }
    });
    edit.querySelector("#ce-delete").addEventListener("click", async () => {
      if (!(await confirmAction(`Удалить «${item.name}»?`))) return;
      try {
        const nodes = await api(`/chats/${state.chatId}/categories/${id}`, { method: "DELETE" });
        closeSheet(edit);
        changed(nodes);
      } catch (e) {
        toast(e.message);
      }
    });
  }

  overlay = openSheet(`<div id="cat-content"></div>`);
  render();
}

/* «Разобрать «Другое»»: траты из «Другого», сгруппированные по названию, и куда их, похоже,
   переложить (выученное, словарь, ИИ). Отмеченные перекладываются и запоминаются; новые
   категории, предложенные ИИ, создаются только для отмеченных. ИИ отвечает не мгновенно —
   за раз разбираем сколько успеем, остальное по «Найти ещё». */
function openTidySheet(onChange) {
  const sheet = openSheet(`<div class="sheet-title">Разобрать «Другое»</div><div id="tidy-content"><div class="hint-text">✨ Смотрю траты в «Другом»…</div></div>`);
  const content = sheet.querySelector("#tidy-content");
  let items = [];
  const seen = new Set();
  const unknown = new Set();

  function label(i) {
    if (i.proposal) {
      const p = i.proposal;
      const where = [p.category, p.subcategory].filter(Boolean).join(" › ");
      return `${p.action === "new_category" ? `${escapeHtml(p.icon || "🏷️")} ` : `${categoryIcon(p.category)} `}${escapeHtml(where)} <span class="hint-text">· новая</span>`;
    }
    return `${categoryIcon(i.category)} ${escapeHtml([i.category, i.subcategory].filter(Boolean).join(" › "))}`;
  }

  function unknownHtml() {
    if (!unknown.size) return "";
    return `<div class="hint-text" style="margin-top:10px;">Не понял, куда отнести: ${[...unknown].map(escapeHtml).join(", ")}. Их можно поменять вручную в самой трате.</div>`;
  }

  function render(more) {
    if (!items.length) {
      content.innerHTML = `<div class="hint-text">${unknown.size ? "Подходящей категории не нашлось." : "В «Другом» нет трат без подкатегории."}</div>${unknownHtml()}
        ${more ? `<button class="btn secondary" id="tidy-more" style="margin-top:10px;">Найти ещё</button>` : ""}`;
      const moreBtn = content.querySelector("#tidy-more");
      if (moreBtn) moreBtn.addEventListener("click", () => { moreBtn.disabled = true; moreBtn.textContent = "✨ Ищу…"; load(); });
      return;
    }
    content.innerHTML = `
      <div class="hint-text" style="margin-bottom:10px;">Отметьте, что переложить. Бот запомнит и в следующий раз подставит сам.</div>
      ${items.map((i, n) => `
        <label class="cat-row" style="gap:8px; justify-content:flex-start;">
          <input type="checkbox" data-n="${n}" checked>
          <span><b>${escapeHtml(i.title)}</b>${i.count > 1 ? ` ×${i.count}` : ""} · ${fmtMoney(i.total)}<br>→ ${label(i)}</span>
        </label>`).join("")}
      ${unknownHtml()}
      ${more ? `<button class="btn secondary" id="tidy-more" style="margin-top:10px;">Найти ещё</button>` : ""}
      <button class="btn" id="tidy-apply" style="margin-top:10px;">Переложить</button>`;
    const moreBtn = content.querySelector("#tidy-more");
    if (moreBtn) moreBtn.addEventListener("click", () => { moreBtn.disabled = true; moreBtn.textContent = "✨ Ищу…"; load(); });
    content.querySelector("#tidy-apply").addEventListener("click", apply);
  }

  async function load() {
    try {
      const r = await api(`/chats/${state.chatId}/tidy-other`);
      for (const t of r.unknown || []) unknown.add(t);
      for (const i of r.items) {
        if (!seen.has(i.title.toLowerCase())) { seen.add(i.title.toLowerCase()); items.push(i); }
      }
      render(r.more);
    } catch (e) {
      content.innerHTML = `<div class="error-text">${escapeHtml(e.message)}</div>`;
    }
  }

  async function apply() {
    const picked = [...content.querySelectorAll("input[data-n]:checked")].map((el) => items[Number(el.dataset.n)]);
    if (!picked.length) { closeSheet(sheet); return; }
    const body = {
      items: picked.map((i) => (i.proposal
        ? { title: i.title, create: true }
        : { title: i.title, category: i.category, subcategory: i.subcategory })),
    };
    try {
      const r = await api(`/chats/${state.chatId}/tidy-other`, { method: "POST", body });
      closeSheet(sheet);
      haptic("notification", "success");
      toast(`Переложил трат: ${r.moved}`);
      if (onChange) onChange(r.categories);
    } catch (e) {
      toast(e.message);
    }
  }

  load();
}

export { loadCategories, setCategoryNodes, promptNewCategory, openCategoriesSheet };
