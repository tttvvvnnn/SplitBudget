/* Экран входа веб-версии (браузер вне Telegram) — вход через бота, см. app/shared/web_auth.py.

   Запрос на вход создаётся сразу при показе экрана, чтобы кнопка была обычной ссылкой
   на t.me: окно, открытое после await, браузер заблокировал бы как всплывающее. После
   нажатия опрашиваем сервер, пока вход не подтвердят в боте; cookie ставит сам ответ. */

import { escapeHtml } from "./format.js";
import { brandMarkSvg } from "./brand.js";

const POLL_MS = 2000;

let login = null; // {token, secret, bot_url, expires_in}
let pollTimer = null;
let polling = false; // ждём подтверждения: не подменяем запрос, пока его не отменят или он не протухнет
let refreshTimer = null;

async function postJson(path, body) {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error("Сервер недоступен, попробуйте ещё раз");
  return res.json();
}

function renderLogin(onLoggedIn) {
  const app = document.getElementById("app");
  app.classList.add("app-login"); // без отступов под таббар и боковую панель
  app.innerHTML = `
    <div class="login-screen">
      <div class="login-card">
        <div class="login-mark">${brandMarkSvg()}</div>
        <div class="wordmark login-wordmark"><span class="split">Split</span>Budget</div>
        <p class="login-lead">Веб-версия: те же траты, «Мои финансы» и семейные чаты, что в Telegram.</p>
        <a class="btn login-btn" id="login-btn" href="#" target="_blank" rel="noopener" aria-disabled="true">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M21.9 4.3 18.7 19.5c-.2 1-.9 1.3-1.8.8l-4.9-3.6-2.4 2.3c-.3.3-.5.5-1 .5l.3-5 9.2-8.3c.4-.4-.1-.6-.6-.2L6.2 13 1.3 11.5c-1-.3-1-1 .2-1.5L20.6 2.6c.9-.3 1.6.2 1.3 1.7Z"/></svg>
          <span>Войти через Telegram</span>
        </a>
        <div class="login-status" id="login-status" aria-live="polite"></div>
        <p class="hint-text login-hint">Откроется чат с ботом — нажмите там «✅ Войти» и вернитесь на эту вкладку.
          Если Telegram открылся на телефоне, подтвердите там: вход выполнится здесь.</p>
      </div>
    </div>`;

  const btn = document.getElementById("login-btn");
  btn.addEventListener("click", (ev) => {
    if (!login) { ev.preventDefault(); return; }
    setStatus("Ждём подтверждения в Telegram…", true);
    startPolling(onLoggedIn);
  });
  newLogin();
}

function setStatus(text, waiting = false) {
  const el = document.getElementById("login-status");
  if (!el) return;
  el.innerHTML = text ? `${waiting ? '<span class="login-spinner"></span>' : ""}${escapeHtml(text)}` : "";
}

/* Новый запрос на вход; живёт 10 минут — обновляем заранее, чтобы ссылка не протухла. */
async function newLogin() {
  stopPolling();
  clearTimeout(refreshTimer);
  const btn = document.getElementById("login-btn");
  try {
    login = await postJson("/web-login");
  } catch (e) {
    login = null;
    setStatus(e.message);
    refreshTimer = setTimeout(newLogin, 5000);
    return;
  }
  if (btn) {
    btn.href = login.bot_url;
    btn.removeAttribute("aria-disabled");
  }
  refreshTimer = setTimeout(() => { if (!polling) newLogin(); }, Math.max(login.expires_in - 60, 30) * 1000);
}

function startPolling(onLoggedIn) {
  stopPolling();
  polling = true;
  const current = login;
  const tick = async () => {
    let res;
    try {
      res = await postJson("/web-login/poll", { token: current.token, secret: current.secret });
    } catch (e) {
      pollTimer = setTimeout(tick, POLL_MS * 2);
      return;
    }
    if (current !== login) return; // запрос уже заменён новым
    if (res.status === "confirmed") {
      clearTimeout(refreshTimer);
      setStatus("Вход выполнен", false);
      document.getElementById("app").classList.remove("app-login");
      onLoggedIn();
    } else if (res.status === "pending") {
      pollTimer = setTimeout(tick, POLL_MS);
    } else {
      setStatus(res.status === "cancelled" ? "Вход отменён в боте. Можно попробовать ещё раз." : "Ссылка устарела, нажмите кнопку ещё раз.");
      newLogin();
    }
  };
  pollTimer = setTimeout(tick, POLL_MS);
}

function stopPolling() {
  clearTimeout(pollTimer);
  pollTimer = null;
  polling = false;
}

async function logout() {
  try {
    await fetch("/api/web-logout", { method: "POST" });
  } finally {
    window.location.replace("/");
  }
}

export { renderLogin, logout };
