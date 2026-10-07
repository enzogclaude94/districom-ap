import { apiFetch } from "@/lib/api/client";

/**
 * Pending collections API (`/api/v1/settlements`, R3). Amounts travel as decimal strings ("1500.00").
 *
 * Every pending keeps its load (`amount`, `customer_name`: what the next close publishes) apart from its
 * financial values (`amount_in_effect`, `collected`, `resolved`, `balance`, `state`), which come only from
 * the close in effect: a correction in progress is shown as `load_change`, never as money already moved.
 */

export type PendingState = "DRAFT" | "VOIDED" | "OPEN" | "PARTIAL" | "COLLECTED" | "RESOLVED";
export type LoadChange = "ADDED" | "MODIFIED" | "MARKED_FOR_REMOVAL";
export type ReceiptMethod = "CASH" | "TRANSFER";

export interface Pending {
  id: number;
  settlement_id: number;
  customer_name: string;
  amount: string;
  reason: string;
  responsible_employee_id: number;
  responsible_name: string;
  expected_date: string | null;
  created_by: number;
  created_by_name: string;
  created_at: string;
  updated_by: number | null;
  updated_by_name: string | null;
  updated_at: string;
  voided: boolean;
  voided_by: number | null;
  voided_by_name: string | null;
  voided_at: string | null;
  void_reason: string | null;
  state: PendingState;
  amount_in_effect: string | null;
  customer_in_effect: string | null;
  collected: string;
  resolved: string;
  balance: string | null;
  load_change: LoadChange | null;
  load_change_detail: string | null;
  age_hours: number;
  alert: boolean;
  expected_date_overdue: boolean;
}

export interface PendingItem extends Pending {
  settlement_business_date: string;
  settlement_sequence_number: number;
  driver_id: number;
  driver_name: string;
}

export interface Reversal {
  operation_id: number;
  reversed_by: number;
  reversed_by_name: string;
  reversed_at: string;
  reason: string;
}

export interface Receipt {
  id: number;
  amount: string;
  business_date: string;
  method: ReceiptMethod;
  destination_account_id: number;
  destination_name: string;
  operation_id: number;
  created_by: number;
  created_by_name: string;
  created_at: string;
  reversal: Reversal | null;
}

export interface ResolutionPart {
  target: "COMPANY" | "EMPLOYEE";
  employee_id: number | null;
  employee_name: string | null;
  amount: string;
}

export interface Resolution {
  id: number;
  amount: string;
  business_date: string;
  reason: string;
  allocations: ResolutionPart[];
  operation_id: number;
  created_by: number;
  created_by_name: string;
  created_at: string;
  reversal: Reversal | null;
}

export interface Published {
  settlement_version: number;
  close_operation_id: number;
  amount: string;
  customer_name: string;
  in_effect: boolean;
  published_at: string;
}

export interface PendingEvent {
  id: number;
  event_type: string;
  settlement_version: number;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  reason: string | null;
  close_operation_id: number | null;
  receipt_id: number | null;
  resolution_id: number | null;
  created_by: number;
  created_by_name: string;
  created_at: string;
}

export interface PendingDetail extends PendingItem {
  settlement_financial_state: string;
  published: Published[];
  receipts: Receipt[];
  resolutions: Resolution[];
  events: PendingEvent[];
}

export interface PendingPage {
  items: PendingItem[];
  total: number;
}

export interface AlertSummary {
  alert_hours: number;
  count: number;
  balance: string;
}

export interface AlertSettings {
  alert_hours: number;
  is_default: boolean;
  min_hours: number;
  max_hours: number;
}

export interface PendingFilters {
  state?: PendingState[];
  customer?: string;
  driver_id?: number;
  responsible_employee_id?: number;
  min_age_hours?: number;
  alert?: boolean;
  limit: number;
  offset: number;
}

const BASE = "/settlements";

function query(filters: PendingFilters) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === "" || value === false) continue;
    if (Array.isArray(value)) for (const item of value) params.append(key, String(item));
    else params.set(key, String(value));
  }
  return params.toString();
}

export const pendingPath = (id: number, rest = "") => `/pending-collections/${id}${rest}`;

export const pendingApi = {
  list: (filters: PendingFilters) => apiFetch<PendingPage>(`${BASE}/pending-collections?${query(filters)}`),
  summary: () => apiFetch<AlertSummary>(`${BASE}/pending-collections/summary`),
  get: (id: number) => apiFetch<PendingDetail>(`${BASE}${pendingPath(id)}`),
  alertSettings: () => apiFetch<AlertSettings>(`${BASE}/pending-alert-settings`),
  updateAlertSettings: (alertHours: number) =>
    apiFetch<AlertSettings>(`${BASE}/pending-alert-settings`, {
      method: "PUT",
      body: JSON.stringify({ alert_hours: alertHours }),
    }),
  /** Every write on a pending answers with its whole detail, already recomputed by the backend. */
  write: (method: "POST" | "PUT" | "PATCH", path: string, body: unknown) =>
    apiFetch<PendingDetail>(`${BASE}${path}`, { method, body: JSON.stringify(body) }),
};

/** Everything the pending screens read (list, summary, detail); invalidated after every write. */
export const pendingQueryKey = ["settlements", "pending"] as const;
export const pendingDetailKey = (id: number) => [...pendingQueryKey, "detail", id] as const;
export const pendingSummaryKey = [...pendingQueryKey, "summary"] as const;
