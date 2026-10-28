import { apiFetch } from "@/lib/api/client";
import type { ExpectedSource, FinancialState } from "@/modules/settlements/delivery/api";

/**
 * Rendiciones › Cierre del día (R9-2): a calculated view of one business date over the ledger. Read-only for any role
 * in Rendiciones; never signed, frozen nor stored, so every visit recalculates it. Amounts are decimal strings.
 */

export type DayAccountKind = "CASH_BOX" | "BANK";

export interface DayCategory {
  key: string;
  label: string;
  /** For a reversal: the category it reverses, shown right after it and never netted against it. */
  reverses: string | null;
  operations: number;
  amount: string;
}

export interface DayAccount {
  account_id: number;
  name: string;
  kind: DayAccountKind;
  /** Today's state; the day is shown whatever it is now. */
  active: boolean;
  opening_balance: string;
  inflows: string;
  outflows: string;
  net: string;
  closing_balance: string;
  /** opening + net = closing; false is an invariant error of the ledger. */
  reconciled: boolean;
  inflow_categories: DayCategory[];
  outflow_categories: DayCategory[];
}

export interface DayKindTotals {
  kind: DayAccountKind;
  opening_balance: string;
  inflows: string;
  outflows: string;
  net: string;
  closing_balance: string;
}

export interface DayExpectedOrigin {
  source: ExpectedSource;
  legacy: boolean;
  external_reference: string | null;
  revision: number | null;
}

export interface DaySettlement {
  id: number;
  type: "DELIVERY" | "OUTLET";
  name: string;
  sequence_number: number;
  financial_state: FinancialState;
  being_corrected: boolean;
  expected_missing: boolean;
  expected_update_pending: boolean;
  close_kind: "FINANCIAL" | "ADMINISTRATIVE_ZERO" | null;
  /** Of the close in effect; for one not closed, the amount loaded. */
  expected_amount: string | null;
  expected_origin: DayExpectedOrigin | null;
  /** Only with a close in effect: never a draft. */
  justified: string | null;
  difference: string | null;
}

export interface DaySettlements {
  total: number;
  open: number;
  closed: number;
  being_corrected: number;
  without_expected: number;
  store_sys_pending: number;
  items: DaySettlement[];
}

export interface DayCheques {
  portfolio_count: number;
  portfolio_amount: string;
  in_transit_count: number;
  in_transit_amount: string;
  deposited_count: number;
  deposited_amount: string;
  credited_count: number;
  credited_amount: string;
  rejected_count: number;
  rejected_amount: string;
  returned_count: number;
  returned_amount: string;
}

export interface DailyClose {
  business_date: string;
  calculated_at: string;
  accounts: DayAccount[];
  totals: DayKindTotals[];
  reconciled: boolean;
  employee_movements_reserved: boolean;
  settlements: DaySettlements;
  cheques: DayCheques;
}

const BASE = "/settlements/daily-close";

export const dailyCloseApi = {
  get: (on: string) => apiFetch<DailyClose>(`${BASE}?on=${encodeURIComponent(on)}`),
};

/** The CSV of the same view, downloaded by the browser with the session cookie. */
export const dailyCloseCsvHref = (on: string) => `/api/v1${BASE}.csv?on=${encodeURIComponent(on)}`;

export const dailyCloseQueryKey = ["settlements", "daily-close"] as const;
