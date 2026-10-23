import { state } from "./state.js";
import { categoryIcon, escapeHtml } from "./format.js";
import { promptNewCategory } from "./categories.js";

const NEW = "__new__"; // пункт «➕ Своя…» в выпадающих списках

/* Поля «Категория» + «Подкатегория» в формах траты и повтора. Дерево категорий —
   state.categoryTree (GET /categories). Подкатегория необязательна; список подкатегорий
   перестраивается при смене категории. prefix — "f" (трата) или "r" (повтор). */

function subcategoriesOf(category) {
  const node = state.categoryTree.find((c) => c.name === category);
  return node ? node.subcategories : [];
}

function subcategoryOptionsHtml(category, selected) {
  return `<option value="">— без подкатегории —</option>` + subcategoriesOf(category)
    .map((s) => `<option value="${escapeHtml(s)}" ${s === selected ? "selected" : ""}>${escapeHtml(s)}</option>`)
    .join("") + `<option value="${NEW}">➕ Своя подкатегория…</option>`;
}

function categoryOptionsHtml(current) {
  const names = state.categoryTree.map((c) => c.name);
  // Трата со старой/нестандартной категорией: оставляем её в списке, чтобы не потерять при сохранении
  if (current && !names.includes(current)) names.push(current);
  return names.map((c) => `<option value="${escapeHtml(c)}" ${c === current ? "selected" : ""}>${categoryIcon(c)} ${escapeHtml(c)}</option>`).join("")
    + `<option value="${NEW}">➕ Своя категория…</option>`;
}

function categoryFieldsHtml(prefix, category, subcategory) {
  const current = category || state.categoryTree[0].name;
  return `
    <div class="field-row">
      <div class="field">
        <label>Категория</label>
        <select id="${prefix}-category">${categoryOptionsHtml(current)}</select>
      </div>
      <div class="field" id="${prefix}-subcategory-field">
        <label>Подкатегория</label>
        <select id="${prefix}-subcategory">${subcategoryOptionsHtml(current, subcategory)}</select>
      </div>
    </div>
    <div class="hint-text" id="${prefix}-category-hint" style="display:none; margin: -6px 0 10px;"></div>`;
}

/* onUserChange — вызывается, когда пользователь сам меняет категорию или подкатегорию
   (после этого автоподбор по названию её больше не трогает). */
function bindCategoryFields(root, prefix, onUserChange) {
  const catSelect = root.querySelector(`#${prefix}-category`);
  const subSelect = root.querySelector(`#${prefix}-subcategory`);
  // Прежний выбор — чтобы вернуть его, если «➕ Своя…» передумали создавать
  catSelect.dataset.last = catSelect.value;
  subSelect.dataset.last = subSelect.value;
  catSelect.addEventListener("change", () => {
    if (catSelect.value === NEW) {
      catSelect.value = catSelect.dataset.last;
      promptNewCategory(null, (name) => {
        catSelect.innerHTML = categoryOptionsHtml(name);
        setCategory(root, prefix, name, "");
        if (onUserChange) onUserChange();
      });
      return;
    }
    setCategory(root, prefix, catSelect.value, "");
    showCategoryHint(root, prefix, "");
    if (onUserChange) onUserChange();
  });
  subSelect.addEventListener("change", () => {
    if (subSelect.value === NEW) {
      subSelect.value = subSelect.dataset.last;
      promptNewCategory(catSelect.value, (name) => {
        setCategory(root, prefix, catSelect.value, name);
        if (onUserChange) onUserChange();
      });
      return;
    }
    subSelect.dataset.last = subSelect.value;
    showCategoryHint(root, prefix, "");
    if (onUserChange) onUserChange();
  });
}

function showCategoryHint(root, prefix, text) {
  const hint = root.querySelector(`#${prefix}-category-hint`);
  hint.textContent = text;
  hint.style.display = text ? "block" : "none";
}

/* Программно выставить категорию/подкатегорию (например, когда её угадали по названию). */
function setCategory(root, prefix, category, subcategory) {
  const catSelect = root.querySelector(`#${prefix}-category`);
  if (![...catSelect.options].some((o) => o.value === category)) catSelect.innerHTML = categoryOptionsHtml(category);
  catSelect.value = category;
  catSelect.dataset.last = category;
  const subSelect = root.querySelector(`#${prefix}-subcategory`);
  subSelect.innerHTML = subcategoryOptionsHtml(category, subcategory);
  subSelect.dataset.last = subSelect.value;
  catSelect.dispatchEvent(new Event("categoryset")); // не "change": это не выбор пользователя
}

function readCategoryFields(root, prefix) {
  return {
    category: root.querySelector(`#${prefix}-category`).value,
    subcategory: root.querySelector(`#${prefix}-subcategory`).value,
  };
}

/* Бейдж категории в карточке: «🛒 Алкоголь» (подкатегория, если есть), полное имя — в title. */
function categoryBadgeHtml(category, subcategory) {
  const full = subcategory ? `${category} › ${subcategory}` : category;
  return `<span class="badge" title="${escapeHtml(full)}">${categoryIcon(category)} ${escapeHtml(subcategory || category)}</span>`;
}

export { categoryFieldsHtml, bindCategoryFields, setCategory, showCategoryHint, readCategoryFields, categoryBadgeHtml };
