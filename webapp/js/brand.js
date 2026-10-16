/* Брендинг и тема: знак «монета пополам» (brand/logo-mark.svg) и светлая/тёмная тема. */

import { tg } from "./telegram.js";

/* Знак inline, чтобы половинки красились из CSS (--mark-a/--mark-b) под текущую тему. */
function brandMarkSvg() {
  return `<svg class="brand-mark" viewBox="0 0 100 100" aria-hidden="true">
    <g transform="rotate(-24 50 50) translate(0 -2)">
      <path class="half-a" d="M47 10A40 40 0 0 0 47 90Z"/>
      <path class="half-b" d="M53 14A40 40 0 0 1 53 94Z"/>
    </g></svg>`;
}

function wordmarkHtml() {
  return `<span class="wordmark"><span class="split">Split</span>Budget</span>`;
}

/* Цвет верхнего края фона (--page-bg) — им красим шапку и подложку самого Telegram,
   чтобы приложение не выглядело вставкой. Держать в синхроне со style.css. */
const PAGE_BG = { light: "#edf3fa", dark: "#08111e" };

/* Светлая или тёмная — как в Telegram (tg.colorScheme), без Telegram — как в системе.
   Цвета темы Telegram больше не используем: у приложения своя палитра бренда. */
function applyTheme() {
  const scheme = tg && tg.colorScheme
    ? tg.colorScheme
    : (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = scheme;
  if (tg) {
    try {
      if (tg.setBackgroundColor) tg.setBackgroundColor(PAGE_BG[scheme]);
      if (tg.setHeaderColor) tg.setHeaderColor(PAGE_BG[scheme]);
      if (tg.setBottomBarColor) tg.setBottomBarColor(PAGE_BG[scheme]);
    } catch (e) { /* старый клиент — не критично */ }
  }
}

/* Telegram на Android сообщает класс производительности устройства в User-Agent
   («...; Android 14; SDK 34; LOW)»). На слабых устройствах размытие под каждой карточкой
   тормозит при прокрутке — там карточки становятся плотными, размытие остаётся только
   у шапки, таббара и шторок. */
function applyGlassLevel() {
  if (/Telegram-Android\/.*;\s*LOW\)/i.test(navigator.userAgent)) {
    document.documentElement.classList.add("glass-lite");
  }
}

function initTheme() {
  applyTheme();
  applyGlassLevel();
  if (tg && tg.onEvent) tg.onEvent("themeChanged", applyTheme);
}

export { brandMarkSvg, wordmarkHtml, initTheme };
