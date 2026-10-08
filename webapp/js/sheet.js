import { tg, haptic } from "./telegram.js";
import { escapeHtml } from "./format.js";

/* ---------------- Шторки (bottom sheet) и общие формы ---------------- */

function openSheet(innerHtml) {
  // на всякий случай сбрасываем нативные кнопки от предыдущей шторки, если та не закрылась
  // штатно (не должно случаться, но так спокойнее)
  if (tg && tg.MainButton) tg.MainButton.hide();
  if (tg && tg.BackButton) tg.BackButton.hide();

  const overlay = document.createElement("div");
  overlay.className = "sheet-overlay";
  overlay.innerHTML = `<div class="sheet"><div class="sheet-handle"></div>${innerHtml}</div>`;
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) closeSheet(overlay);
  });
  document.body.appendChild(overlay);

  // Системная кнопка «назад» Telegram закрывает текущую шторку так же, как тап по фону
  if (tg && tg.BackButton) {
    const backHandler = () => closeSheet(overlay);
    overlay._backHandler = backHandler;
    tg.BackButton.onClick(backHandler);
    tg.BackButton.show();
  }
  return overlay;
}

function closeSheet(overlay) {
  if (tg && tg.MainButton) {
    if (overlay._mainHandler) tg.MainButton.offClick(overlay._mainHandler);
    tg.MainButton.hide();
  }
  if (tg && tg.BackButton) {
    if (overlay._backHandler) tg.BackButton.offClick(overlay._backHandler);
    tg.BackButton.hide();
  }
  haptic("impact", "light");
  overlay.remove();
}

/* Подменяет самодельную кнопку submit/save внутри шторки на нативную нижнюю кнопку
   Telegram (MainButton) — просто прячет исходную кнопку и проксирует тап на неё же, чтобы
   вся логика клика (валидация, запрос к API, обработка ошибок) осталась в одном месте.
   Если MainButton недоступен (старый клиент Telegram или тестирование вне Telegram) —
   исходная кнопка остаётся видимой и рабочей, как раньше. */
function useMainButtonFor(overlay, btn, text) {
  if (!tg || !tg.MainButton) return;
  btn.style.display = "none";
  tg.MainButton.setText(text);
  tg.MainButton.show();
  tg.MainButton.enable();
  const handler = () => btn.click();
  overlay._mainHandler = handler;
  tg.MainButton.onClick(handler);
}

function showFormError(el, message) {
  haptic("notification", "error");
  el.textContent = message;
  el.style.display = "block";
}

/* Общий "спросить одну строку текста" — шторка поверх чего угодно, вплоть до уже открытой
   шторки траты/повтора (используется и оттуда — при добавлении участника прямо из выбора
   участников). Намеренно НЕ использует openSheet()/useMainButtonFor(): те завязаны на
   единственный нативный tg.MainButton/tg.BackButton, а эта форма может открыться ПОВЕРХ уже
   открытой шторки, у которой этот нативный MainButton уже занят своей кнопкой сохранения —
   переключение его туда-обратно было бы хрупким (два набора обработчиков на одну кнопку).
   Поэтому здесь — обычная кнопка внутри собственной шторки, без претензии на нативную
   нижнюю кнопку Telegram. onSubmit(value) должен либо бросить исключение с человекочитаемым
   .message (тогда оно покажется как ошибка формы и шторка останется открытой), либо
   успешно завершиться — тогда шторка закрывается сама. */
function openTextPromptModal({ title, hint, label, initialValue, placeholder, submitLabel, onSubmit }) {
  const overlay = document.createElement("div");
  overlay.className = "sheet-overlay";
  overlay.innerHTML = `
    <div class="sheet">
      <div class="sheet-handle"></div>
      <div class="sheet-title">${escapeHtml(title)}</div>
      ${hint ? `<div class="hint-text" style="margin: 0 0 12px;">${escapeHtml(hint)}</div>` : ""}
      <div class="field">
        <label>${escapeHtml(label)}</label>
        <input type="text" id="tp-value" value="${escapeHtml(initialValue || "")}" placeholder="${escapeHtml(placeholder || "")}" maxlength="255">
      </div>
      <div class="error-text" id="tp-error" style="display:none;"></div>
      <button class="btn" id="tp-submit">${escapeHtml(submitLabel)}</button>
    </div>`;
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) overlay.remove();
  });
  document.body.appendChild(overlay);

  const input = overlay.querySelector("#tp-value");
  input.focus();
  input.select();

  overlay.querySelector("#tp-submit").addEventListener("click", async () => {
    const errorEl = overlay.querySelector("#tp-error");
    const submitBtn = overlay.querySelector("#tp-submit");
    const value = input.value.trim();
    if (!value) { showFormError(errorEl, "Введите имя"); return; }
    submitBtn.disabled = true;
    try {
      await onSubmit(value);
      haptic("notification", "success");
      overlay.remove();
    } catch (e) {
      showFormError(errorEl, e.message);
      submitBtn.disabled = false;
    }
  });
  return overlay;
}

export { openSheet, closeSheet, useMainButtonFor, showFormError, openTextPromptModal };
