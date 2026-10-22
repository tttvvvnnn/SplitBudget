import { state } from "./state.js";

const API_BASE = "/api";

/* Внутри Telegram запрос подписан initData, в веб-версии initData нет — браузер сам
   отправляет cookie сессии (тот же домен). */
function authHeaders() {
  return state.initData ? { "X-Telegram-Init-Data": state.initData } : {};
}

async function api(path, { method = "GET", body, isForm = false } = {}) {
  const headers = authHeaders();
  let fetchBody = body;
  if (body && !isForm) {
    headers["Content-Type"] = "application/json";
    fetchBody = JSON.stringify(body);
  }
  const res = await fetch(`${API_BASE}${path}`, { method, headers, body: fetchBody });
  if (!res.ok) {
    let detail = "Ошибка запроса";
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch (e) { /* ignore */ }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res;
}

async function loadAuthedImage(imgEl, path) {
  try {
    const res = await fetch(`${API_BASE}${path}`, { headers: authHeaders() });
    if (!res.ok) throw new Error("no photo");
    const blob = await res.blob();
    imgEl.src = URL.createObjectURL(blob);
  } catch (e) {
    imgEl.style.display = "none";
  }
}

export { api, loadAuthedImage };
