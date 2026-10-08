import { todayMonth } from "./format.js";

/* Общее состояние приложения — один объект на всё mini app, его читают и меняют все модули. */

const state = {
  chatId: null,
  initData: "",
  chat: null,
  member: null,
  members: [],
  categories: [],
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

export { state };
