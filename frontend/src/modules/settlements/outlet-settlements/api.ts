import { apiFetch } from "@/lib/api/client";
import { outletSettlementQueryKey, type FinancialState, type Settlement } from "@/modules/settlements/delivery/api";

/**
 * Boca de expendio settlements API (`/api/v1/settlements/outlet-settlements`, R7-2). The board, the loads,
 * the close and the history are the same as a delivery's (`delivery/api.ts`, through `settlementPath`); what
 * changes is who renders (a boca) and that the Store Sys amount may still be unknown (null) or $0.
 */

export interface OutletSettlementItem {
  id: number;
  business_date: string;
  outlet_id: number;
  outlet_code: string;
  outlet_name: string;
  sequence_number: number;
  expected_amount: string | null;
  external_reference: string | null;
  status: string;
  version: number;
  financial_state: FinancialState;
  /** Its close in effect is an administrative zero close (no operation in Cajas). */
  administrative_zero: boolean;
  created_at: string;
  justified: string;
  /** Null while the settlement is open (R7-3: no difference until the close) or the expected amount is not loaded. */
  difference: string | null;
  /** A Store Sys update waits for a decision (R9-1). */
  expected_update_pending?: boolean;
}

export interface OutletSettlementPage {
  items: OutletSettlementItem[];
  total: number;
}

export interface OutletSettlementFilters {
  date_from?: string;
  date_to?: string;
  outlet_id?: number;
  financial_state?: FinancialState;
  limit: number;
  offset: number;
}

/** A settlement as the board and the ficha of a boca name it. */
export interface OutletSettlementRef {
  id: number;
  business_date: string;
  sequence_number: number;
  status: string;
  financial_state: FinancialState;
  administrative_zero: boolean;
}

/** One settlement of the daily board, with its own figures (never added to another one's). */
export interface OutletBoardSettlement extends OutletSettlementRef {
  expected_amount: string | null;
  remitted: string;
  cash_total: string;
  transfers_verified: string;
  cheques: string;
  expenses: string;
  justified: string;
  /** While open: what is left to hand over or justify; null without an expected amount. */
  remaining: string | null;
  /** While open: how much the justified total already goes above the expected amount. */
  justified_above_expected: string | null;
  /** Only once closed. */
  difference: string | null;
  difference_kind: "SHORTAGE" | "SURPLUS" | "BALANCED" | null;
  /** A Store Sys update waits for a decision (R9-1). */
  expected_update_pending?: boolean;
}

export interface OutletBoardRow {
  outlet_id: number;
  outlet_code: string;
  outlet_name: string;
  outlet_active: boolean;
  settlements: OutletBoardSettlement[];
  /** The settlement the board shows: the first open one, else the last one; null without settlements. */
  current_id: number | null;
}

export interface OutletBoard {
  business_date: string;
  rows: OutletBoardRow[];
  counts: { without_settlement: number; open: number; being_corrected: number; closed: number };
}

/** What the ficha of a boca shows Rendiciones users (R7-4). */
export interface OutletSettlementsSummary {
  outlet_id: number;
  open_count: number;
  open_settlements: OutletSettlementRef[];
  last_settlement: OutletSettlementRef | null;
  remitted_not_consolidated: string;
}

const BASE = "/settlements";

function query(filters: OutletSettlementFilters) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  return params.toString();
}

export const outletSettlementApi = {
  list: (filters: OutletSettlementFilters) =>
    apiFetch<OutletSettlementPage>(`${BASE}/outlet-settlements?${query(filters)}`),
  get: (id: number) => apiFetch<Settlement>(`${BASE}/outlet-settlements/${id}`),
  /** Rendiciones › Bocas: every boca on a business day. */
  board: (businessDate: string) =>
    apiFetch<OutletBoard>(`${BASE}/outlet-settlements/board?business_date=${encodeURIComponent(businessDate)}`),
  /** «Operación de Rendiciones» of a boca's ficha. */
  summary: (outletId: number) => apiFetch<OutletSettlementsSummary>(`${BASE}/outlets/${outletId}/settlements-summary`),
};

export { outletSettlementQueryKey };
