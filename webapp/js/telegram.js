/* Обёртка над Telegram WebApp API: сам объект tg, вибрация, алерты, подтверждения. */

const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
if (tg) {
  tg.ready();
  tg.expand();
}

function toast(message) {
  haptic("notification", "error"); // toast() в этом коде зовётся только из catch-веток
  if (tg && tg.showAlert) {
    tg.showAlert(message);
  } else {
    alert(message);
  }
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

export { tg, toast, confirmAction, haptic };
