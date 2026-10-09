import { todayMonth } from "./format.js";

/* Общее состояние приложения — один объект на всё mini app, его читают и меняют все модули. */

const state = {
  chatId: null,
  personalChatId: null, // id личного пространства «Мои финансы» (GET /personal)
  personalCurrency: "",
  switchSpace: null, // переключение пространства (задаёт app.js) — для перехода из «Все траты» к трате
  familyChats: [], // семейные чаты пользователя — для переключателя в шапке
  hiddenChats: [], // чаты, которые пользователь убрал из шапки (GET /my-chats?hidden=true)
  initData: "",
  chat: null,
  member: null,
  members: [],
  categories: [],
  categoryTree: [], // [{name, icon, subcategories}] — GET /categories
  tab: "expenses",
  month: todayMonth(),
  expenses: [],
  expenseCategory: "",
  filters: { search: "", category: "", payer: "" },
  filtersOpen: false,
  balances: null,
  settlements: [],
  recurring: [],
  editingExpense: null,
  editingRecurring: null,
  version: "",
  buildInfo: "",
};

/* Особое значение chatId: сводка «Все траты» по всем пространствам пользователя сразу. */
const ALL_SPACE = "all";

function isAll() {
  return state.chatId === ALL_SPACE;
}

/* Открыто личное пространство «Мои финансы» (а не семейный чат): участник один — сам
   пользователь, поэтому всё про «кто платил / как делить / кто кому должен» скрыто. */
function isPersonal() {
  return !!(state.chat && state.chat.is_personal);
}

/* Прячет внутри root элементы с атрибутом data-family-only, если открыто личное пространство. */
function hideFamilyOnly(root) {
  if (!isPersonal()) return;
  root.querySelectorAll("[data-family-only]").forEach((el) => { el.style.display = "none"; });
}

export { state, ALL_SPACE, isAll, isPersonal, hideFamilyOnly };
