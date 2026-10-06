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
  | "UNCOLLECTIBLE";

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
  | "REVERSAL";

export interface Account {
  id: number;
  code: string | null;
  name: string;
  kind: AccountKind;
  allow_negative: boolean;
  active: boolean;
  balance: string;
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
