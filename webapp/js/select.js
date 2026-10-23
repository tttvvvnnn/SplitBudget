/* Выпадающие списки в общем стеклянном стиле вместо нативных.

   Нативный <select> остаётся в разметке (скрытым): вкладки и формы по-прежнему читают и
   ставят .value, слушают "change" и перерисовывают <option> через innerHTML. Рядом с ним
   ставится кнопка .dd-btn, а по нажатию открывается стеклянный список .dd-menu. Новые
   <select> подхватываются сами (MutationObserver), подключать ничего не нужно. */

import { haptic } from "./telegram.js";
import { escapeHtml } from "./format.js";

const MENU_GAP = 6;
const MENU_MAX_H = 340;
const MENU_MAX_W = 360; // длиннее — пункт переносится на вторую строку
const EDGE = 12; // отступ меню от краёв окна

let openMenu = null; // {select, btn, menu, items, active}

/* .value и .selectedIndex, поставленные из кода, не дают ни события, ни мутации — подменяем
   свойства у конкретного <select>, чтобы подпись кнопки не отставала. */
function watchProps(select, sync) {
  for (const prop of ["value", "selectedIndex"]) {
    const desc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, prop);
    Object.defineProperty(select, prop, {
      configurable: true,
      get() { return desc.get.call(this); },
      set(v) { desc.set.call(this, v); sync(); },
    });
  }
}

function enhance(select) {
  if (select._dd || select.multiple || select.size > 1) return;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "dd-btn";
  btn.setAttribute("aria-haspopup", "listbox");
  btn.setAttribute("aria-expanded", "false");
  btn.innerHTML = '<span class="dd-label"></span><span class="dd-chevron" aria-hidden="true"></span>';
  select.classList.add("dd-native");
  select.tabIndex = -1;
  select.setAttribute("aria-hidden", "true");
  select.after(btn);

  const sync = () => {
    const opt = select.options[select.selectedIndex];
    btn.querySelector(".dd-label").textContent = opt ? opt.textContent.trim() : "";
    btn.disabled = select.disabled;
    if (openMenu && openMenu.select === select) closeMenu();
  };
  select._dd = { btn, sync };
  watchProps(select, sync);
  select.addEventListener("change", sync);
  new MutationObserver(sync).observe(select, {
    childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "selected", "label"],
  });
  btn.addEventListener("click", () => {
    if (openMenu && openMenu.select === select) closeMenu();
    else showMenu(select);
  });
  btn.addEventListener("keydown", (ev) => {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(ev.key) && !openMenu) {
      ev.preventDefault();
      showMenu(select);
    }
  });
  sync();
}

function optionHtml(opt, idx, selectedIdx) {
  const cls = ["dd-item"];
  if (idx === selectedIdx) cls.push("selected");
  if (opt.disabled) cls.push("disabled");
  return `<div class="${cls.join(" ")}" role="option" data-idx="${idx}" aria-selected="${idx === selectedIdx}">
    <span class="dd-item-text">${escapeHtml(opt.textContent.trim())}</span>
    <span class="dd-check" aria-hidden="true"></span>
  </div>`;
}

function showMenu(select) {
  closeMenu();
  const { btn } = select._dd;
  if (btn.disabled) return;
  const selectedIdx = select.selectedIndex;
  const opts = Array.from(select.options);
  let html = "";
  for (const child of select.children) {
    if (child.tagName === "OPTGROUP") {
      html += `<div class="dd-group">${escapeHtml(child.label)}</div>`;
      for (const o of child.children) html += optionHtml(o, opts.indexOf(o), selectedIdx);
    } else if (child.tagName === "OPTION") {
      html += optionHtml(child, opts.indexOf(child), selectedIdx);
    }
  }
  const menu = document.createElement("div");
  menu.className = "dd-menu";
  menu.setAttribute("role", "listbox");
  menu.innerHTML = html || '<div class="dd-empty">Пусто</div>';
  document.body.appendChild(menu);
  place(menu, btn);

  const items = Array.from(menu.querySelectorAll(".dd-item"));
  openMenu = { select, btn, menu, items, active: items.findIndex((i) => i.classList.contains("selected")) };
  btn.classList.add("open");
  btn.setAttribute("aria-expanded", "true");
  highlight(openMenu.active, true);

  menu.addEventListener("click", (ev) => {
    const item = ev.target.closest(".dd-item");
    if (item && !item.classList.contains("disabled")) choose(Number(item.dataset.idx));
  });
  menu.addEventListener("pointermove", (ev) => {
    const item = ev.target.closest(".dd-item");
    if (item) highlight(items.indexOf(item), false);
  });
  haptic("selection");
}

/* Под кнопкой, а если снизу не помещается — над ней; ширина по самому длинному пункту,
   но не уже кнопки. */
function place(menu, btn) {
  const r = btn.getBoundingClientRect();
  const vw = window.innerWidth, vh = window.innerHeight;
  const below = vh - r.bottom - MENU_GAP - EDGE;
  const above = r.top - MENU_GAP - EDGE;
  const up = below < Math.min(MENU_MAX_H, menu.scrollHeight) && above > below;
  const maxH = Math.max(120, Math.min(MENU_MAX_H, up ? above : below));
  menu.style.maxHeight = `${maxH}px`;
  menu.style.minWidth = `${Math.min(r.width, vw - EDGE * 2)}px`;
  menu.style.maxWidth = `${Math.min(MENU_MAX_W, vw - EDGE * 2)}px`;
  const width = menu.offsetWidth;
  menu.style.left = `${Math.min(Math.max(r.left, EDGE), vw - width - EDGE)}px`;
  if (up) {
    menu.style.bottom = `${vh - r.top + MENU_GAP}px`;
    menu.classList.add("up");
  } else {
    menu.style.top = `${r.bottom + MENU_GAP}px`;
  }
}

function highlight(i, scroll) {
  if (!openMenu) return;
  openMenu.items.forEach((el, j) => el.classList.toggle("active", j === i));
  openMenu.active = i;
  if (scroll && openMenu.items[i]) openMenu.items[i].scrollIntoView({ block: "nearest" });
}

function choose(idx) {
  const { select, btn } = openMenu;
  closeMenu();
  btn.focus({ preventScroll: true });
  if (idx === select.selectedIndex) return;
  select.selectedIndex = idx;
  haptic("selection");
  select.dispatchEvent(new Event("input", { bubbles: true }));
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

function closeMenu() {
  if (!openMenu) return;
  const { menu, btn } = openMenu;
  openMenu = null;
  menu.remove();
  btn.classList.remove("open");
  btn.setAttribute("aria-expanded", "false");
}

function move(step) {
  const { items } = openMenu;
  let i = openMenu.active;
  for (let n = 0; n < items.length; n++) {
    i = i < 0 ? (step > 0 ? 0 : items.length - 1) : (i + step + items.length) % items.length;
    if (!items[i].classList.contains("disabled")) break;
  }
  highlight(i, true);
}

// В фазе захвата — раньше, чем Esc закроет шторку (sheet.js): закрываем только список.
document.addEventListener("keydown", (ev) => {
  if (!openMenu) return;
  if (ev.key === "Escape") { ev.preventDefault(); ev.stopImmediatePropagation(); closeMenu(); }
  else if (ev.key === "ArrowDown") { ev.preventDefault(); move(1); }
  else if (ev.key === "ArrowUp") { ev.preventDefault(); move(-1); }
  else if (ev.key === "Enter" || ev.key === " ") {
    ev.preventDefault();
    const item = openMenu.items[openMenu.active];
    if (item && !item.classList.contains("disabled")) choose(Number(item.dataset.idx));
  } else if (ev.key === "Tab") closeMenu();
}, true);

document.addEventListener("pointerdown", (ev) => {
  if (openMenu && !openMenu.menu.contains(ev.target) && !openMenu.btn.contains(ev.target)) closeMenu();
}, true);

// Прокрутка страницы или шторки уводит кнопку из-под меню — проще закрыть.
document.addEventListener("scroll", (ev) => {
  if (openMenu && !openMenu.menu.contains(ev.target)) closeMenu();
}, true);
window.addEventListener("resize", closeMenu);

function enhanceAll(root) {
  if (root.tagName === "SELECT") enhance(root);
  else if (root.querySelectorAll) root.querySelectorAll("select").forEach(enhance);
}

new MutationObserver((records) => {
  for (const r of records) {
    for (const node of r.addedNodes) if (node.nodeType === 1) enhanceAll(node);
    // Кнопка без своего <select> (его убрали из разметки) — убираем и её, и меню.
    for (const node of r.removedNodes) {
      if (node.nodeType !== 1) continue;
      const sels = node.tagName === "SELECT" ? [node] : node.querySelectorAll ? node.querySelectorAll("select") : [];
      for (const s of sels) {
        if (s._dd && openMenu && openMenu.select === s) closeMenu();
        if (s._dd && s._dd.btn.isConnected && !s.isConnected) s._dd.btn.remove();
      }
    }
  }
}).observe(document.documentElement, { childList: true, subtree: true });
enhanceAll(document.body);
