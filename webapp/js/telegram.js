/* Обёртка над Telegram WebApp API: сам объект tg, вибрация, алерты, подтверждения.

   В обычном браузере (веб-версия) telegram-web-app.js тоже создаёт WebApp, но пустой: без
   initData, а MainButton/BackButton там никуда не рисуются. Поэтому вне Telegram tg = null,
   и весь код идёт по запасным веткам — свои кнопки в шторках, alert/confirm браузера. */

const webApp = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
const tg = webApp && webApp.initData ? webApp : null;
// Открыто в браузере, а не внутри Telegram: вход по cookie (js/login.js)
const isWebVersion = !tg;
if (tg) {
  tg.ready();
  tg.expand();
}

function toast(message) {
  haptic("notification", "error"); // toast() в этом коде зовётся только из catch-веток
  if (tg && tg.showAlert) {
    tg.showAlert(message);
    return;
  }
  // В браузере — своё всплывающее сообщение внизу вместо модального alert()
  document.querySelectorAll(".toast").forEach((el) => el.remove());
  const el = document.createElement("div");
  el.className = "toast";
  el.setAttribute("role", "alert");
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

function confirmAction(message) {
  return new Promise((resolve) => {
    if (tg && tg.showConfirm) {
      tg.showConfirm(message, (ok) => resolve(ok));
    } else {
      resolve(confirm(message));
    }
  });
}

/* Тактильный отклик через Telegram WebApp API — короткая вибрация на действие, без неё
   интерфейс ощущается «веб-страницей», а не частью Telegram. На старых клиентах, где
   HapticFeedback не поддерживается, тихо ничего не делает. */
function haptic(kind, style) {
  if (!tg || !tg.HapticFeedback) return;
  try {
    if (kind === "impact") tg.HapticFeedback.impactOccurred(style || "light");
    else if (kind === "notification") tg.HapticFeedback.notificationOccurred(style || "success");
    else if (kind === "selection") tg.HapticFeedback.selectionChanged();
  } catch (e) { /* не критично */ }
}

export { tg, isWebVersion, toast, confirmAction, haptic };
