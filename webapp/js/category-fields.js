import { state } from "./state.js";
import { categoryIcon, escapeHtml } from "./format.js";

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
    .join("");
}

function categoryFieldsHtml(prefix, category, subcategory) {
  const names = state.categoryTree.map((c) => c.name);
  // Трата со старой/нестандартной категорией: оставляем её в списке, чтобы не потерять при сохранении
  if (category && !names.includes(category)) names.push(category);
  const current = category || names[0];
  const hasSubs = subcategoriesOf(current).length > 0;
  return `
    <div class="field-row">
      <div class="field">
        <label>Категория</label>
        <select id="${prefix}-category">
          ${names.map((c) => `<option value="${escapeHtml(c)}" ${c === current ? "selected" : ""}>${categoryIcon(c)} ${escapeHtml(c)}</option>`).join("")}
        </select>
      </div>
      <div class="field" id="${prefix}-subcategory-field" style="display:${hasSubs ? "block" : "none"};">
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
  catSelect.addEventListener("change", () => {
    setCategory(root, prefix, catSelect.value, "");
    showCategoryHint(root, prefix, "");
    if (onUserChange) onUserChange();
  });
  root.querySelector(`#${prefix}-subcategory`).addEventListener("change", () => {
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
  root.querySelector(`#${prefix}-category`).value = category;
  root.querySelector(`#${prefix}-subcategory`).innerHTML = subcategoryOptionsHtml(category, subcategory);
  root.querySelector(`#${prefix}-subcategory-field`).style.display =
    subcategoriesOf(category).length > 0 ? "block" : "none";
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
