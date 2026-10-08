import { apiFetch } from "@/lib/api/client";

/**
 * Cheques API (`/api/v1/settlements`, R4). Amounts travel as decimal strings ("1500.00").
 *
 * A cheque is an instrument: where it is (portfolio, deposited…) is never stored, the backend derives it
 * from the ledger and the documents of its cycle (`state`, `location_label`). Every write is idempotent by
 * `client_request_id`; deposit, crediting, rejection and return answer with their document.
 */

export type ChequeKind = "PHYSICAL" | "ECHEQ";
export type PaymentType = "COMMON" | "DEFERRED";
export type ChequeOrigin = "SETTLEMENT" | "OPENING_BALANCE" | "OWNER_CONTRIBUTION" | "PENDING_RECEIPT";
export type ChequeState =
  | "DECLARED"
  | "VERIFICATION_REJECTED"
  | "RECEIVED"
  | "IN_PORTFOLIO"
  | "DEPOSITED"
  | "CREDITED"
  | "REJECTED"
  | "RETURNED"
  | "VOIDED";
export type VerificationStatus = "DECLARED" | "VERIFIED" | "VERIFICATION_REJECTED";
export type DocumentType = "DEPOSIT" | "CREDIT" | "REJECTION" | "RETURN" | "PENDING_RECEIPT";

export interface Cheque {
  id: number;
  kind: ChequeKind;
  payment_type: PaymentType;
  amount: string;
  issuer_bank_id: number;
  bank_bcra_code: number;
  bank_name: string;
  branch_code: string | null;
  account_ref: string | null;
  cheque_number: string;
  echeq_external_id: string | null;
  identity_complete: boolean;
  issue_date: string;
  payment_date: string | null;
  payable_from: string;
  presentation_deadline: string;
  presentation_expired: boolean;
  expires_in_days: number | null;
  expiring_soon: boolean;
  location_label: string | null;
  deposit_id: number | null;
  deposit_business_date: string | null;
  deposit_bank_account_id: number | null;
  deposit_bank_account_name: string | null;
  credit_id: number | null;
  credit_business_date: string | null;
  drawer_name: string;
  drawer_tax_id: string | null;
  delivered_by_name: string;
  origin_type: ChequeOrigin;
  settlement_id: number | null;
  origin_pending_id: number | null;
  received_business_date: string;
  received_by_employee_id: number | null;
  received_by_name: string | null;
  physical_receipt_confirmed_by_name: string | null;
  physical_receipt_confirmed_at: string | null;
  verification_status: VerificationStatus | null;
  verification_changed_by_name: string | null;
  verification_changed_at: string | null;
  verification_reason: string | null;
  notes: string | null;
  state: ChequeState;
  state_label: string;
  published: boolean;
  in_effect: boolean;
  load_change: "ADDED" | "MARKED_FOR_REMOVAL" | null;
  load_change_detail: string | null;
  created_by_name: string;
  created_at: string;
  voided: boolean;
  voided_by_name: string | null;
  voided_at: string | null;
  void_reason: string | null;
}

export interface ChequeEvent {
  id: number;
  event_type: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  reason: string | null;
  settlement_id: number | null;
  settlement_version: number | null;
  close_operation_id: number | null;
  operation_id: number | null;
  created_by_name: string;
  created_at: string;
}

export interface ChequeDocument {
  type: DocumentType;
  id: number;
  business_date: string;
  operation_id: number;
  reversed: boolean;
  reversal_operation_id: number | null;
  pending_id: number | null;
}

export interface ChequeDetail extends Cheque {
  events: ChequeEvent[];
  closings: { close_operation_id: number; settlement_version: number; amount: string }[];
  entry_operation_id: number | null;
  documents: ChequeDocument[];
}

export interface ChequePage {
  items: Cheque[];
  total: number;
}

export interface PortfolioSummary {
  portfolio_count: number;
  portfolio_amount: string;
  in_transit_count: number;
  in_transit_amount: string;
}

export interface DocumentReversal {
  operation_id: number;
  reversed_by_name: string;
  reversed_at: string;
  reason: string;
}

export interface CycleItem {
  cheque_id: number;
  amount: string;
  kind: ChequeKind;
  cheque_number: string;
  bank_name: string;
  delivered_by_name: string;
  state: ChequeState;
  state_label: string;
}

interface DocumentBase {
  id: number;
  business_date: string;
  operation_id: number;
  created_by_name: string;
  created_at: string;
  reversal: DocumentReversal | null;
}

export interface Deposit extends DocumentBase {
  bank_account_id: number;
  bank_account_name: string;
  slip_number: string | null;
  notes: string | null;
  total: string;
  items: CycleItem[];
}

export interface Credit extends DocumentBase {
  bank_account_id: number;
  bank_account_name: string;
  notes: string | null;
  total: string;
  items: CycleItem[];
}

export interface Rejection extends DocumentBase {
  cheque_id: number;
  stage: "IN_TRANSIT" | "AFTER_CREDIT";
  reason: string;
  bank_account_id: number | null;
  pending_id: number;
}

export interface Return extends DocumentBase {
  cheque_id: number;
  reason: string;
  pending_id: number;
}

export interface Bank {
  id: number;
  bcra_code: number;
  name: string;
  source: string;
  active: boolean;
}

export interface ChequeFilters {
  state?: ChequeState;
  kind?: ChequeKind;
  issuer_bank_id?: number;
  origin_type?: ChequeOrigin;
  payable?: boolean;
  expired?: boolean;
  expiring_soon?: boolean;
  settlement_id?: number;
  limit: number;
  offset: number;
}

/** The documents of the cycle, by the path of their endpoints. */
export const DOCUMENT_PATHS = {
  DEPOSIT: "cheque-deposits",
  CREDIT: "cheque-credits",
  REJECTION: "cheque-rejections",
  RETURN: "cheque-returns",
} as const;
export type CycleDocumentType = keyof typeof DOCUMENT_PATHS;

const BASE = "/settlements";

function query(filters: ChequeFilters) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    // `payable=false` is a filter («todavía no pagaderos»); the other flags only filter when true.
    if (value === undefined || value === "" || (value === false && key !== "payable")) continue;
    params.set(key, String(value));
  }
  return params.toString();
}

const send = <T>(path: string, body: unknown) =>
  apiFetch<T>(`${BASE}${path}`, { method: "POST", body: JSON.stringify(body) });

export const chequeApi = {
  list: (filters: ChequeFilters) => apiFetch<ChequePage>(`${BASE}/cheques?${query(filters)}`),
  get: (id: number) => apiFetch<ChequeDetail>(`${BASE}/cheques/${id}`),
  summary: () => apiFetch<PortfolioSummary>(`${BASE}/cheques/summary`),
  banks: () => apiFetch<Bank[]>("/financial-institutions"),
  deposit: (id: number) => apiFetch<Deposit>(`${BASE}/cheque-deposits/${id}`),
  credit: (id: number) => apiFetch<Credit>(`${BASE}/cheque-credits/${id}`),
  rejection: (id: number) => apiFetch<Rejection>(`${BASE}/cheque-rejections/${id}`),
  return: (id: number) => apiFetch<Return>(`${BASE}/cheque-returns/${id}`),
  /** A write of the cycle: answers with its document (or the cheque, for its own writes). */
  write: <T>(path: string, body: unknown) => send<T>(path, body),
};

/** Everything the cheque screens read; invalidated after every write. */
export const chequeQueryKey = ["settlements", "cheques"] as const;
export const chequeDetailKey = (id: number) => [...chequeQueryKey, "detail", id] as const;
export const chequeSummaryKey = [...chequeQueryKey, "summary"] as const;
export const chequeHref = (id: number) => `/rendiciones/cheques/${id}`;
export const documentHref = (type: CycleDocumentType, id: number) =>
  `/rendiciones/cheques/documentos/${DOCUMENT_PATHS[type]}/${id}`;
