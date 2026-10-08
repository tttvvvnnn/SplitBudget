import { state } from "./state.js";

const API_BASE = "/api";

async function api(path, { method = "GET", body, isForm = false } = {}) {
  const headers = { "X-Telegram-Init-Data": state.initData };
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
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res;
}

async function loadAuthedImage(imgEl, path) {
  try {
    const res = await fetch(`${API_BASE}${path}`, { headers: { "X-Telegram-Init-Data": state.initData } });
    if (!res.ok) throw new Error("no photo");
    const blob = await res.blob();
    imgEl.src = URL.createObjectURL(blob);
  } catch (e) {
    imgEl.style.display = "none";
  }
}

export { api, loadAuthedImage };
