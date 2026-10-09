import { apiFetch } from "@/lib/api/client";
import type { AccountKind, Operation } from "@/modules/settlements/cash/api";
import type { FuelLoad } from "@/modules/settlements/delivery/api";

/** Expenses API (`/api/v1/settlements/expenses`, R5-3): direct expenses from a cash box or the bank. */

export type ExpenseOrigin = "SETTLEMENT" | "DIRECT";
/** VOIDED: a settlement expense voided while its settlement was open. REVERSED: a direct expense undone. */
export type ExpenseState = "IN_EFFECT" | "VOIDED" | "REVERSED";

export interface ExpenseItem {
  id: number;
  origin: ExpenseOrigin;
  state: ExpenseState;
  business_date: string;
  category_id: number;
  category_name: string;
  category_code: string | null;
  amount: string;
  description: string | null;
  settlement_id: number | null;
  paid_from_account_id: number | null;
  paid_from_account_name: string | null;
  paid_from_account_kind: AccountKind | null;
  operation_id: number | null;
  reversal_operation_id: number | null;
  fuel: FuelLoad | null;
  legacy_fuel_without_detail: boolean;
  created_by: number;
  created_by_name: string;
  created_at: string;
  ended_by_name: string | null;
  ended_at: string | null;
  end_reason: string | null;
}

export interface ExpensePage {
  items: ExpenseItem[];
  total: number;
}

export interface ExpenseEvent {
  id: number;
  event_type: string;
  occurred_at: string;
  user_name: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface ExpenseDetail extends ExpenseItem {
  operation: Operation | null;
  reversal: Operation | null;
  history: ExpenseEvent[];
}

export interface ExpenseFilters {
  origin?: ExpenseOrigin;
  category_id?: number;
  date_from?: string;
  date_to?: string;
  account_id?: number;
  state?: ExpenseState;
  limit: number;
  offset: number;
}

const BASE = "/settlements/expenses";

function query(filters: ExpenseFilters) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  return params.toString();
}

export const expensePath = (id: number, rest = "") => `/expenses/${id}${rest}`;

export const expenseApi = {
  list: (filters: ExpenseFilters) => apiFetch<ExpensePage>(`${BASE}?${query(filters)}`),
  get: (id: number) => apiFetch<ExpenseDetail>(`${BASE}/${id}`),
  /** Create, reverse and correct answer with the whole expense, as the backend recorded it. */
  write: (path: string, body: unknown) =>
    apiFetch<ExpenseDetail>(`/settlements${path}`, { method: "POST", body: JSON.stringify(body) }),
};

export const expenseQueryKey = ["settlements", "expenses"] as const;
export const expenseDetailKey = (id: number) => [...expenseQueryKey, "detail", id] as const;
export const expenseHref = (id: number) => `/rendiciones/cajas/gastos/${id}`;
