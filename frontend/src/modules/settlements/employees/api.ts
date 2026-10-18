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
  | "LOAN"
  | "LOAN_REVERSAL"
  | "PAYROLL"
  | "PAYROLL_CORRECTION"
  | "OTHER";
export type DocumentKind =
  | "DELIVERY_SETTLEMENT"
  | "OUTLET_SETTLEMENT"
  | "PENDING_COLLECTION"
  | "EMPLOYEE_MOVEMENT"
  | "EMPLOYEE_LOAN"
  | "EMPLOYEE_PAYROLL";

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
  /** Employee movements (R8-2) and loans (R8-3): what it was, and whether the whole document was reversed since. */
  description?: string | null;
  document_reversed?: boolean | null;
  /** R8-4: what the closes in effect of internal payrolls applied of it (absolute). */
  applied?: string;
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

/** R8-3: an interest-free loan, its schedule and the deferrals of its installments. */
export type LoanStatus = "ACTIVE" | "REVERSED";
/** SCHEDULED in effect; DEFERRED history (replaced by new installments); CANCELLED by the loan's reversal. */
export type InstallmentStatus = "SCHEDULED" | "DEFERRED" | "CANCELLED";

export interface ScheduledInstallment {
  number: number;
  due_on: string;
  amount: string;
}

export interface LoanPreview {
  principal: string;
  business_date: string;
  funding_account_id: number;
  funding_account_name: string;
  balance_before: string;
  balance_after: string;
  legs: MovementLeg[];
  installments: ScheduledInstallment[];
  total: string;
}

export interface LoanInstallment extends ScheduledInstallment {
  id: number;
  status: InstallmentStatus;
  /** Derived (R8-4): a close in effect applied it («Aplicada») or not yet («Pendiente de aplicar»). */
  applied: boolean;
  applied_payroll_id: number | null;
  deferred_from_id: number | null;
  deferred_from_number: number | null;
  deferred_reason: string | null;
  deferred_at: string | null;
  deferred_by_name: string | null;
  replaced_by: ScheduledInstallment[];
  created_at: string;
}

export interface EmployeeLoanRow {
  id: number;
  business_date: string;
  principal: string;
  installment_count: number;
  status: LoanStatus;
  /** From the loan, never from the internal account: principal minus the installments applied; 0 reversed. */
  balance: string;
  applied_total: string;
  /** Installments still to apply. */
  scheduled_count: number;
  next_due_on: string | null;
  description: string | null;
}

export interface EmployeeLoan extends EmployeeLoanRow {
  employee_id: number;
  employee_name: string;
  funding_account_id: number;
  funding_account_name: string;
  first_due_on: string;
  reference: string | null;
  operation_id: number;
  reversal_operation_id: number | null;
  reversed_at: string | null;
  reversed_by_name: string | null;
  reversal_reason: string | null;
  created_at: string;
  created_by_name: string;
  legs: MovementLeg[];
  installments: LoanInstallment[];
}

export interface LoanTerms {
  business_date: string;
  principal: string;
  funding_account_id: number;
  installment_count: number;
  first_due_on: string;
  /** The schedule as confirmed; omitted: the default one (equal monthly installments). */
  installments?: { due_on: string; amount: string }[];
}

/** R8-4: internal payrolls («liquidaciones internas»): not a legal payslip. */
export type PayrollKind = "REGULAR" | "EXTRAORDINARY";
export type PayrollStatus = "OPEN" | "CLOSED" | "CANCELLED";
export type PayrollLineKind = "SALARY" | "BONUS" | "INCENTIVE" | "OTHER_CREDIT";

export interface PayrollTotals {
  credits: string;
  deductions: string;
  credits_applied: string;
  net: string;
  deduction_pct: string | null;
}

export interface PayrollRow {
  id: number;
  employee_id: number;
  employee_name: string;
  kind: PayrollKind;
  period_start: string;
  period_end: string;
  payment_date: string | null;
  status: PayrollStatus;
  version: number;
  totals: PayrollTotals;
  totals_from_draft: boolean;
  in_correction: boolean;
}

export interface PayrollLine {
  id: number;
  kind: PayrollLineKind;
  amount: string;
  description: string | null;
  source: "TERM" | "MANUAL";
  compensation_term_id: number | null;
}

export interface PayrollLeg {
  entry_id: number;
  operation_id: number;
  origin: MovementOrigin;
  business_date: string;
  document_kind: DocumentKind | null;
  document_id: number | null;
  description: string | null;
  group_operation_id: number;
  reversed: boolean;
  original_amount: string;
  applied_elsewhere: string;
  available: string;
  proposed: boolean;
  included: boolean;
  amount: string;
  partial: boolean;
}

export interface PayrollInstallment {
  installment_id: number;
  loan_id: number;
  loan_description: string | null;
  number: number;
  due_on: string;
  amount: string;
  overdue: boolean;
  included: boolean;
}

export interface PayrollPreview extends PayrollTotals {
  preview_token: string;
  blockers: { code: string; message: string }[];
  accrual: "FINANCIAL" | "NO_ACCRUAL";
}

export interface PayrollApplication {
  entry_id: number;
  origin: MovementOrigin;
  business_date: string;
  amount: string;
  installment_id: number | null;
  installment_number: number | null;
  loan_id: number | null;
}

export interface PayrollClosing extends PayrollTotals {
  id: number;
  version: number;
  kind: "FINANCIAL" | "NO_ACCRUAL";
  operation_id: number | null;
  reversal_operation_id: number | null;
  closed_at: string;
  closed_by_name: string;
  superseded_at: string | null;
  lines: PayrollLine[];
  applications: PayrollApplication[];
}

export interface Payroll extends PayrollRow {
  notes: string | null;
  created_at: string;
  created_by_name: string;
  cancelled_at: string | null;
  cancelled_by_name: string | null;
  cancel_reason: string | null;
  salary_hint: { status: "PROPOSED" | "MANUAL" | "NONE"; message: string; amount: string | null };
  lines: PayrollLine[];
  legs: PayrollLeg[];
  installments: PayrollInstallment[];
  preview: PayrollPreview | null;
  closing: PayrollClosing | null;
  closings: PayrollClosing[];
  reopenings: {
    from_version: number;
    to_version: number;
    reason: string;
    reopened_at: string;
    reopened_by_name: string;
  }[];
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
  previewLoan: (employeeId: number, body: LoanTerms) =>
    post<LoanPreview>(`/employees/${employeeId}/loans/preview`, body),
  /** A write of R8-3 (give, defer, reverse): each answers the loan as it stands. */
  writeLoan: (path: string, body: Record<string, unknown>) => post<EmployeeLoan>(path, body),
  loans: (employeeId: number) => apiFetch<EmployeeLoanRow[]>(`${BASE}/employees/${employeeId}/loans`),
  loan: (loanId: number) => apiFetch<EmployeeLoan>(`${BASE}/loans/${loanId}`),
  payrolls: (params: { employee_id?: number; status?: PayrollStatus; limit: number; offset: number }) =>
    apiFetch<Page<PayrollRow>>(`${BASE}/payrolls${query(params)}`),
  payroll: (payrollId: number) => apiFetch<Payroll>(`${BASE}/payrolls/${payrollId}`),
  /** A write of R8-4: each answers the payroll as it stands (draft and preview included). */
  writePayroll: (method: "POST" | "PUT" | "PATCH" | "DELETE", path: string, body?: Record<string, unknown>) =>
    apiFetch<Payroll>(`${BASE}${path}`, { method, body: body === undefined ? undefined : JSON.stringify(body) }),
};

export const employeeFinanceQueryKey = ["settlements", "employee-finance"] as const;
