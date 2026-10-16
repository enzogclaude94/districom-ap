import { apiFetch } from "@/lib/api/client";

/** Cajas API (`/api/v1/settlements`, R1). Amounts travel as decimal strings ("1500.00"). */

export type AccountKind =
  | "CASH_BOX"
  | "BANK"
  | "OWNER_EQUITY"
  | "OPENING_BALANCE"
  | "ADJUSTMENT"
  | "DELIVERY_COLLECTIONS"
  | "EXPENSE"
  | "SETTLEMENT_DIFFERENCE"
  | "EMPLOYEE"
  | "PENDING_COLLECTIONS"
  | "UNCOLLECTIBLE"
  | "CHEQUE_PORTFOLIO"
  | "CHEQUES_IN_TRANSIT"
  | "MERCHANDISE_PAYMENTS"
  | "THIRD_PARTY_REFUNDS"
  | "OTHER_PAYMENTS"
  | "OUTLET_COLLECTIONS"
  | "OUTLET_ADVANCES"
  | "EMPLOYEE_CONSUMPTIONS"
  | "EMPLOYEE_ADJUSTMENTS";

export type OperationType =
  | "OPENING_BALANCE"
  | "CASH_TRANSFER"
  | "BANK_DEPOSIT"
  | "BANK_WITHDRAWAL"
  | "OWNER_CONTRIBUTION"
  | "OWNER_WITHDRAWAL"
  | "ADJUSTMENT"
  | "SETTLEMENT_CLOSE"
  | "PENDING_COLLECTION"
  | "PENDING_WRITE_OFF"
  | "CHEQUE_OPENING_BALANCE"
  | "CHEQUE_OWNER_CONTRIBUTION"
  | "CHEQUE_DEPOSIT"
  | "CHEQUE_CREDIT"
  | "CHEQUE_REJECTION"
  | "CHEQUE_RETURN"
  | "DIRECT_EXPENSE"
  | "PAYMENT"
  | "OUTLET_REMITTANCE"
  | "REVERSAL"
  | "EMPLOYEE_CONSUMPTION"
  | "EMPLOYEE_ADVANCE"
  | "EMPLOYEE_ADJUSTMENT"
  /** An employee finance operation (R8) shown to someone without the area: only date, amount and box (ADR 010). */
  | "EMPLOYEE_RESERVED";

export interface Account {
  id: number;
  code: string | null;
  name: string;
  kind: AccountKind;
  allow_negative: boolean;
  active: boolean;
  balance: string;
  /** «Banco en descubierto» (R4-2): a bank below zero, only ever by a cheque rejected after its crediting. */
  overdrawn: boolean;
  overdrawn_label: string | null;
  /** What overdrew it (R4-3): the rejections after crediting in effect on this bank, newest first. */
  overdraft_causes?: OverdraftCause[];
}

export interface OverdraftCause {
  rejection_id: number;
  cheque_id: number;
  operation_id: number;
  business_date: string;
  amount: string;
}

export interface Movement {
  entry_id: number;
  operation_id: number;
  operation_type: OperationType;
  business_date: string;
  description: string | null;
  amount: string;
  created_by: number;
  created_by_name: string;
  created_at: string;
  source_type: string | null;
  source_id: number | null;
  reverses_operation_id: number | null;
  reversed_by_operation_id: number | null;
}

export interface MovementPage {
  items: Movement[];
  total: number;
}

export interface Entry {
  account_id: number;
  account_name: string;
  account_kind: AccountKind;
  amount: string;
  /** The cheque a leg on a cheque account moves; absent or null on every other account. */
  cheque_id?: number | null;
}

export interface Operation {
  id: number;
  type: OperationType;
  business_date: string;
  description: string | null;
  created_by: number;
  created_by_name: string;
  created_at: string;
  source_type: string | null;
  source_id: number | null;
  reverses_operation_id: number | null;
  reversed_by_operation_id: number | null;
  entries: Entry[];
}

/** Fields every operation carries (ADR 008): one request id per user action, resent on retries. */
export interface OperationBase {
  client_request_id: string;
  business_date: string;
  description?: string | null;
}

export interface OperationBodies {
  "cash-transfer": OperationBase & { from_account_id: number; to_account_id: number; amount: string };
  "bank-deposit": OperationBase & { cash_account_id: number; bank_account_id: number; amount: string };
  "bank-withdrawal": OperationBase & { bank_account_id: number; cash_account_id: number; amount: string };
  "opening-balance": OperationBase & { account_id: number; amount: string };
  "owner-contribution": OperationBase & { owner_account_id: number; account_id: number; amount: string };
  "owner-withdrawal": OperationBase & { owner_account_id: number; account_id: number; amount: string };
  adjustment: OperationBase & { account_id: number; amount: string; description: string };
}

export type OperationKind = keyof OperationBodies;

export interface ReversalBody {
  client_request_id: string;
  reason: string;
  business_date: string;
}

export interface MovementFilters {
  limit: number;
  offset: number;
}

const BASE = "/settlements";

export const operationPath = (kind: OperationKind) => `/operations/${kind}`;
export const reversePath = (operationId: number) => `/operations/${operationId}/reverse`;

const post = <T>(path: string, body: unknown) =>
  apiFetch<T>(`${BASE}${path}`, { method: "POST", body: JSON.stringify(body) });

export const cashApi = {
  accounts: () => apiFetch<Account[]>(`${BASE}/accounts?include_inactive=true`),
  movements: (accountId: number, { limit, offset }: MovementFilters) =>
    apiFetch<MovementPage>(`${BASE}/accounts/${accountId}/movements?limit=${limit}&offset=${offset}`),
  operation: (id: number) => apiFetch<Operation>(`${BASE}/operations/${id}`),
  post: <K extends OperationKind>(kind: K, body: OperationBodies[K]) => post<Operation>(operationPath(kind), body),
  /** Resends a stored request as is (retry of an operation whose outcome was unknown). */
  send: (path: string, body: unknown) => post<Operation>(path, body),
  reverse: (id: number, body: ReversalBody) => post<Operation>(reversePath(id), body),
  createOwnerAccount: (name: string) => post<Account>("/accounts", { name }),
};

/** Everything the Cajas screen reads; invalidated after every write so balances come back from the API. */
export const cashQueryKey = ["settlements", "cash"] as const;
