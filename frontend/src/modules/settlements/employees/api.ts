import { apiFetch } from "@/lib/api/client";

/**
 * Rendiciones > Empleados (`/api/v1/settlements/employee-finance`, R8-1, ADR 010): a protected area. Without
 * an explicit grant every endpoint answers 404, the platform admin included. Amounts are decimal strings.
 *
 * The internal account is net: positive means the employee owes the company, negative that the company owes
 * the employee. The salary term is the internal monthly base salary, not the legal payslip.
 */

export const EMPLOYEE_FINANCE_AREA = "settlements.employee_finance";

export type BalanceReading = "OWES_COMPANY" | "COMPANY_OWES" | "NONE";
export type TermStatus = "CURRENT" | "FUTURE" | "PAST";
export type MovementOrigin =
  | "SETTLEMENT_SHORTAGE"
  | "SETTLEMENT_SURPLUS"
  | "SETTLEMENT_CORRECTION"
  | "PENDING_WRITE_OFF"
  | "PENDING_WRITE_OFF_REVERSAL"
  | "CONSUMPTION"
  | "ADVANCE"
  | "ADJUSTMENT_CHARGE"
  | "ADJUSTMENT_CREDIT"
  | "CONSUMPTION_REVERSAL"
  | "ADVANCE_REVERSAL"
  | "ADJUSTMENT_REVERSAL"
  | "OTHER";
export type DocumentKind = "DELIVERY_SETTLEMENT" | "OUTLET_SETTLEMENT" | "PENDING_COLLECTION" | "EMPLOYEE_MOVEMENT";

export interface CompensationTerm {
  id: number;
  employee_id: number;
  valid_from: string;
  valid_to: string | null;
  base_salary: string;
  notes: string | null;
  status: TermStatus;
  created_at: string;
  created_by_name: string;
  ended_at: string | null;
  ended_by_name: string | null;
  end_reason: string | null;
}

export interface EmployeeFinanceRow {
  employee_id: number;
  full_name: string;
  active: boolean;
  balance: string;
  balance_reading: BalanceReading;
  current_term: CompensationTerm | null;
  next_term: CompensationTerm | null;
}

export interface EmployeeFinanceDetail extends EmployeeFinanceRow {
  hire_date: string | null;
  has_account: boolean;
}

export interface AccountMovement {
  entry_id: number;
  operation_id: number;
  business_date: string;
  amount: string;
  reading: "CHARGE" | "CREDIT";
  origin: MovementOrigin;
  document_kind: DocumentKind | null;
  document_id: number | null;
  document_date: string | null;
  document_number: number | null;
  /** Employee movements (R8-2): what it was, and whether the whole document was reversed since. */
  description?: string | null;
  document_reversed?: boolean | null;
  created_at: string;
}

/** R8-2: a consumption (operator), an advance from a cash box or bank (admin), an adjustment (admin). */
export type MovementKind = "CONSUMPTION" | "ADVANCE" | "ADJUSTMENT";
/** Of an adjustment: CHARGE the employee will owe more, CREDIT recognised in the employee's favour. */
export type Direction = "CHARGE" | "CREDIT";

export interface MovementLeg {
  /** null: the internal account, created with its first movement. */
  account_id: number | null;
  account_name: string;
  account_kind: string;
  amount: string;
}

export interface MovementPreview {
  kind: MovementKind;
  direction: Direction | null;
  amount: string;
  employee_amount: string;
  balance_before: string;
  balance_after: string;
  legs: MovementLeg[];
}

export interface EmployeeMovement {
  id: number;
  kind: MovementKind;
  direction: Direction | null;
  employee_id: number;
  employee_name: string;
  business_date: string;
  amount: string;
  employee_amount: string;
  funding_account_id: number | null;
  funding_account_name: string | null;
  description: string;
  reference: string | null;
  status: "IN_EFFECT" | "REVERSED";
  operation_id: number;
  reversal_operation_id: number | null;
  reversed_at: string | null;
  reversed_by_name: string | null;
  reversal_reason: string | null;
  created_at: string;
  created_by_name: string;
  legs: MovementLeg[];
}

export interface Page<T> {
  items: T[];
  total: number;
}

const BASE = "/settlements/employee-finance";

function query(params: Record<string, string | number | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined) search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : "";
}

const post = <T>(path: string, body: unknown) =>
  apiFetch<T>(`${BASE}${path}`, { method: "POST", body: JSON.stringify(body) });

export const MOVEMENT_PATHS: Record<MovementKind, string> = {
  CONSUMPTION: "consumptions",
  ADVANCE: "advances",
  ADJUSTMENT: "adjustments",
};

export const employeeFinanceApi = {
  list: (params: { q?: string; limit: number; offset: number }) =>
    apiFetch<Page<EmployeeFinanceRow>>(`${BASE}/employees${query(params)}`),
  detail: (employeeId: number) => apiFetch<EmployeeFinanceDetail>(`${BASE}/employees/${employeeId}`),
  movements: (employeeId: number, params: { limit: number; offset: number }) =>
    apiFetch<Page<AccountMovement>>(`${BASE}/employees/${employeeId}/movements${query(params)}`),
  terms: (employeeId: number) => apiFetch<CompensationTerm[]>(`${BASE}/employees/${employeeId}/compensation-terms`),
  createTerm: (employeeId: number, body: { valid_from: string; base_salary: string; notes: string | null }) =>
    apiFetch<CompensationTerm>(`${BASE}/employees/${employeeId}/compensation-terms`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  endTerm: (termId: number, body: { valid_to: string; reason: string }) =>
    apiFetch<CompensationTerm>(`${BASE}/compensation-terms/${termId}/end`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  previewMovement: (
    employeeId: number,
    body: {
      kind: MovementKind;
      business_date: string;
      amount: string;
      direction?: Direction;
      funding_account_id?: number;
    },
  ) => post<MovementPreview>(`/employees/${employeeId}/movements/preview`, body),
  /** A write of R8-2 (register, reverse) as `useWriteAction` sends it. */
  write: (path: string, body: Record<string, unknown>) => post<EmployeeMovement>(path, body),
  movement: (movementId: number) => apiFetch<EmployeeMovement>(`${BASE}/employee-movements/${movementId}`),
};

export const employeeFinanceQueryKey = ["settlements", "employee-finance"] as const;
