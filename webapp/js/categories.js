import { state } from "./state.js";
import { api } from "./api.js";
import { haptic, toast, confirmAction } from "./telegram.js";
import { escapeHtml } from "./format.js";
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
      <button class="btn secondary" id="cat-add">➕ Своя категория</button>
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

export { loadCategories, setCategoryNodes, promptNewCategory, openCategoriesSheet };
