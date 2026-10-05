import { apiFetch } from "@/lib/api/client";

/**
 * Delivery settlements API (`/api/v1/settlements/deliveries`, R2). Amounts travel as decimal strings
 * ("1500.00"). Every official total (board, difference, close legs) comes from the backend.
 */

export type FinancialState = "NOT_CLOSED" | "CLOSED" | "BEING_CORRECTED";
export type DifferenceKind = "SHORTAGE" | "SURPLUS" | "BALANCED";
export type TransferStatus = "DECLARED" | "VERIFIED" | "REJECTED";

export interface Person {
  id: number;
  full_name: string;
}

export interface CashByBox {
  account_id: number;
  account_name: string;
  counted: string;
  additions: string;
  total: string;
}

export interface Summary {
  expected: string;
  cash_counted: string;
  cash_additions: string;
  cash_total: string;
  cash_by_box: CashByBox[];
  transfers_verified: string;
  transfers_declared: string;
  transfers_rejected: string;
  declared_transfers_count: number;
  cheques: string;
  pending: string;
  expenses: string;
  justified: string;
  difference: string;
  difference_kind: DifferenceKind;
}

export interface SettlementItem {
  id: number;
  type: string;
  business_date: string;
  employee_id: number;
  employee_name: string;
  sequence_number: number;
  expected_amount: string;
  external_reference: string | null;
  status: string;
  version: number;
  financial_state: FinancialState;
  created_at: string;
  justified: string;
  difference: string;
}

export interface SettlementPage {
  items: SettlementItem[];
  total: number;
}

export interface CountLine {
  denomination_code: string;
  denomination_value: string;
  quantity: number;
  amount: string;
  destination_account_id: number;
  destination_account_name: string;
}

export interface Addition {
  id: number;
  amount: string;
  destination_account_id: number;
  destination_account_name: string;
  description: string | null;
  created_by: number;
  created_by_name: string;
  created_at: string;
  voided: boolean;
  voided_by: number | null;
  voided_by_name: string | null;
  voided_at: string | null;
  void_reason: string | null;
}

export interface TransferEvent {
  from_status: TransferStatus | null;
  to_status: TransferStatus;
  reason: string | null;
  is_correction: boolean;
  changed_by: number;
  changed_by_name: string;
  changed_at: string;
}

export interface Transfer {
  id: number;
  amount: string;
  bank_account_id: number;
  bank_account_name: string;
  reference: string | null;
  payer_name: string | null;
  status: TransferStatus;
  status_reason: string | null;
  status_changed_by: number;
  status_changed_by_name: string;
  status_changed_at: string;
  created_by: number;
  created_at: string;
  events: TransferEvent[];
}

export interface Expense {
  id: number;
  category_id: number;
  category_name: string;
  amount: string;
  description: string | null;
  created_by: number;
  created_by_name: string;
  created_at: string;
  voided: boolean;
  voided_by: number | null;
  voided_by_name: string | null;
  voided_at: string | null;
  void_reason: string | null;
}

export interface CloseRef {
  operation_id: number;
  settlement_version: number;
  closed_at: string;
}

export interface Allocation {
  target: "EMPLOYEE" | "COMPANY";
  employee_id: number | null;
  employee_name: string | null;
  amount: string;
  reason: string | null;
  decided_by: number;
  decided_by_name: string;
  decided_at: string;
}

export interface Difference {
  id: number;
  settlement_version: number;
  kind: Exclude<DifferenceKind, "BALANCED">;
  amount: string;
  close_operation_id: number;
  created_by: number;
  created_by_name: string;
  created_at: string;
  superseded_at: string | null;
  superseded_by: number | null;
  superseded_by_name: string | null;
  allocations: Allocation[];
}

export interface Settlement {
  id: number;
  type: string;
  business_date: string;
  employee_id: number;
  employee_name: string;
  sequence_number: number;
  expected_amount: string;
  expected_source: string;
  external_reference: string | null;
  notes: string | null;
  status: "OPEN" | "CLOSED";
  version: number;
  created_by: number;
  created_by_name: string;
  created_at: string;
  updated_at: string;
  same_day_settlement_ids: number[];
  count_lines: CountLine[];
  additions: Addition[];
  transfers: Transfer[];
  expenses: Expense[];
  summary: Summary;
  financial_state: FinancialState;
  financial_state_detail: string;
  close_in_effect: CloseRef | null;
  closed_by: number | null;
  closed_by_name: string | null;
  closed_at: string | null;
  reopened_by: number | null;
  reopened_by_name: string | null;
  reopened_at: string | null;
  reopen_reason: string | null;
  current_difference: Difference | null;
}

export interface Leg {
  account_id: number | null;
  account_name: string;
  account_kind: string;
  employee_id: number | null;
  amount: string;
}

export interface Blocker {
  code: string;
  message: string;
}

export interface ClosingPreview {
  settlement_id: number;
  version: number;
  status: string;
  financial_state: FinancialState;
  summary: Summary;
  difference: string;
  difference_kind: DifferenceKind;
  amount_to_allocate: string;
  blockers: Blocker[];
  legs: Leg[];
  preview_token: string;
  close_in_effect: CloseRef | null;
}

export interface Closing {
  settlement_version: number;
  operation_id: number;
  reversal_operation_id: number | null;
  reversed_by_operation_id: number | null;
  in_effect: boolean;
  closed_by: number;
  closed_by_name: string;
  closed_at: string;
  business_date: string;
  legs: Leg[];
}

export interface Reopening {
  from_version: number;
  to_version: number;
  reason: string;
  reopened_by: number;
  reopened_by_name: string;
  reopened_at: string;
  close_operation_id: number;
}

export interface History {
  settlement_id: number;
  version: number;
  status: string;
  financial_state: FinancialState;
  closings: Closing[];
  reopenings: Reopening[];
  differences: Difference[];
}

export interface Denomination {
  code: string;
  value: string;
  active: boolean;
  suggested_destination: string | null;
  suggested_destination_account_id: number | null;
  suggested_destination_name: string | null;
}

export interface ExpenseCategory {
  id: number;
  name: string;
  active: boolean;
}

export interface AllocationInput {
  target: "EMPLOYEE" | "COMPANY";
  employee_id?: number | null;
  amount: string;
  reason?: string | null;
}

export interface CloseInput {
  version: number;
  preview_token: string;
  allocations: AllocationInput[];
  confirm: boolean;
}

export interface SettlementFilters {
  date_from?: string;
  date_to?: string;
  employee_id?: number;
  financial_state?: FinancialState;
  limit: number;
  offset: number;
}

const BASE = "/settlements";

const send = <T>(method: "POST" | "PUT" | "PATCH", path: string, body: unknown) =>
  apiFetch<T>(`${BASE}${path}`, { method, body: JSON.stringify(body) });

function query(filters: SettlementFilters) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  return params.toString();
}

export const deliveryPath = (id: number, rest = "") => `/deliveries/${id}${rest}`;

export const deliveryApi = {
  list: (filters: SettlementFilters) => apiFetch<SettlementPage>(`${BASE}/deliveries?${query(filters)}`),
  get: (id: number) => apiFetch<Settlement>(`${BASE}${deliveryPath(id)}`),
  drivers: (on: string) => apiFetch<Person[]>(`${BASE}/drivers?on=${on}`),
  employees: () => apiFetch<Person[]>(`${BASE}/employees`),
  denominations: () => apiFetch<Denomination[]>(`${BASE}/cash-denominations`),
  categories: () => apiFetch<ExpenseCategory[]>(`${BASE}/expense-categories`),
  /** Every category, active or not (configuration screen). */
  allCategories: () => apiFetch<ExpenseCategory[]>(`${BASE}/expense-categories?include_inactive=true`),
  createCategory: (name: string) => send<ExpenseCategory>("POST", "/expense-categories", { name }),
  updateCategory: (id: number, change: { name?: string; active?: boolean }) =>
    send<ExpenseCategory>("PATCH", `/expense-categories/${id}`, change),
  preview: (id: number) => apiFetch<ClosingPreview>(`${BASE}${deliveryPath(id, "/closing-preview")}`),
  history: (id: number) => apiFetch<History>(`${BASE}${deliveryPath(id, "/history")}`),
  /** Every write answers with the whole settlement, already recomputed by the backend. */
  write: (method: "POST" | "PUT" | "PATCH", path: string, body: unknown) => send<Settlement>(method, path, body),
};

/** Everything the delivery screens read; invalidated after every write. */
export const deliveryQueryKey = ["settlements", "deliveries"] as const;
export const settlementQueryKey = (id: number) => [...deliveryQueryKey, "detail", id] as const;
