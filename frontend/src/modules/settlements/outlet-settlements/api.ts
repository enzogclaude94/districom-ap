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
  /** «N rendiciones abiertas» of a boca's page. */
  openCount: (outletId: number) =>
    apiFetch<{ outlet_id: number; open_count: number }>(`${BASE}/outlets/${outletId}/settlements-summary`),
};

export { outletSettlementQueryKey };
