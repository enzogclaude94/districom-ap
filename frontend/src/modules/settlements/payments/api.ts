import { apiFetch } from "@/lib/api/client";
import type { ThirdParty, ThirdPartyInput } from "@/lib/api/third-parties";
import type { AccountKind, Operation } from "@/modules/settlements/cash/api";
import type { ChequeKind, ChequeState, PaymentType } from "@/modules/settlements/cheques/api";

/**
 * Payments API (`/api/v1/settlements/payments`, R6-1/R6-2): payments made to third parties, never debts. A
 * payment combines cash, transfers and cheques of the portfolio, each cheque for its whole amount.
 */

export type PaymentConcept = "MERCHANDISE" | "EXPENSE" | "REFUND" | "OTHER";
export type PaymentMethod = "CASH" | "TRANSFER" | "CHEQUE";
export type PaymentState = "IN_EFFECT" | "REVERSED";

/** The cheque of a CHEQUE item as it is now: «Entregado» while the payment is in effect. */
export interface PaymentCheque {
  cheque_id: number;
  kind: ChequeKind;
  payment_type: PaymentType;
  cheque_number: string;
  bank_name: string;
  drawer_name: string;
  delivered_by_name: string;
  payable_from: string;
  state: ChequeState;
  state_label: string;
  /** When the payment was reversed: who confirmed the cheque was recovered from the third party, and when. */
  recovered_at: string | null;
  recovered_by_name: string | null;
}

export interface PaymentItem {
  id: number;
  method: PaymentMethod;
  account_id: number | null;
  account_name: string | null;
  account_kind: AccountKind | null;
  cheque_id: number | null;
  cheque: PaymentCheque | null;
  amount: string;
  reference: string | null;
}

export interface Payment {
  id: number;
  state: PaymentState;
  business_date: string;
  third_party_id: number;
  third_party_name: string;
  third_party_tax_id_display: string | null;
  third_party_active: boolean;
  concept: PaymentConcept;
  expense_category_id: number | null;
  expense_category_name: string | null;
  /** The technical account of the counter-leg («Pagos por mercadería», «Gastos operativos»…). */
  counter_account_name: string;
  amount: string;
  reference: string | null;
  description: string | null;
  items: PaymentItem[];
  operation_id: number | null;
  reversal_operation_id: number | null;
  created_by: number;
  created_by_name: string;
  created_at: string;
  reversed_by_name: string | null;
  reversed_at: string | null;
  reversal_reason: string | null;
}

export interface PaymentPage {
  items: Payment[];
  total: number;
  /** Sum of the payments in effect among the filtered ones; reversed ones never count. */
  amount_in_effect: string;
}

export interface PaymentEvent {
  id: number;
  event_type: string;
  occurred_at: string;
  user_name: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface PaymentDetail extends Payment {
  operation: Operation | null;
  reversal: Operation | null;
  history: PaymentEvent[];
}

/**
 * Filters of `/rendiciones/pagos`, all applied by the backend. Those on items (account, method, cheque) keep a
 * mixed payment whole when any of its items matches.
 */
export interface PaymentFilters {
  third_party_id?: number;
  concept?: PaymentConcept;
  expense_category_id?: number;
  date_from?: string;
  date_to?: string;
  account_id?: number;
  method?: PaymentMethod;
  cheque_id?: number;
  cheque_number?: string;
  reference?: string;
  state?: PaymentState;
  limit: number;
  offset: number;
}

/** What was paid to a third party (R6-3): payments in effect only. Never a balance, a debt or an account. */
export interface ThirdPartyPaymentSummary {
  third_party_id: number;
  payments_in_effect: number;
  amount_in_effect: string;
  payments_reversed: number;
  by_concept: { concept: PaymentConcept; count: number; amount: string }[];
  /** Each item adds to its own means: a mixed payment is split, and the methods sum the total in effect. */
  by_method: { method: PaymentMethod; count: number; amount: string }[];
  cheques_delivered: number;
  cheques_delivered_amount: string;
}

/** A cheque given to the third party in a payment; `recovered` after that payment was reversed. */
export interface DeliveredCheque {
  cheque_id: number;
  cheque_number: string;
  bank_name: string;
  amount: string;
  delivered_on: string;
  payment_id: number;
  payment_state: PaymentState;
  recovered: boolean;
  recovered_at: string | null;
  state: ChequeState;
  state_label: string;
}

export interface DeliveredChequePage {
  items: DeliveredCheque[];
  total: number;
}

const BASE = "/settlements/payments";

function query(filters: PaymentFilters) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && String(value).trim() !== "") params.set(key, String(value).trim());
  }
  return params.toString();
}

export const paymentPath = (id: number, rest = "") => `/payments/${id}${rest}`;

export const paymentApi = {
  list: (filters: PaymentFilters) => apiFetch<PaymentPage>(`${BASE}?${query(filters)}`),
  get: (id: number) => apiFetch<PaymentDetail>(`${BASE}/${id}`),
  thirdPartySummary: (thirdPartyId: number) =>
    apiFetch<ThirdPartyPaymentSummary>(`${BASE}/third-parties/${thirdPartyId}/summary`),
  thirdPartyCheques: (thirdPartyId: number, limit: number, offset: number) =>
    apiFetch<DeliveredChequePage>(`${BASE}/third-parties/${thirdPartyId}/cheques?limit=${limit}&offset=${offset}`),
  /** Create, reverse and correct answer with the whole payment, as the backend recorded it. */
  write: (path: string, body: unknown) =>
    apiFetch<PaymentDetail>(`/settlements${path}`, { method: "POST", body: JSON.stringify(body) }),
  /** Quick create of a third party while recording a payment (operator); it lands in the platform master. */
  createThirdParty: (input: ThirdPartyInput) =>
    apiFetch<ThirdParty>("/settlements/third-parties", { method: "POST", body: JSON.stringify(input) }),
};

export const paymentQueryKey = ["settlements", "payments"] as const;
export const paymentDetailKey = (id: number) => [...paymentQueryKey, "detail", id] as const;
export const paymentHref = (id: number) => `/rendiciones/pagos/${id}`;
