import { apiFetch } from "@/lib/api/client";

/**
 * Rendiciones > Integración Store Sys (`settlements.store_sys_import`, R9-1, ADR 011): a protected area. Without an
 * explicit grant every endpoint answers 404, the platform admin included. Reading takes the area; changing an
 * employee's code takes the area's admin, and correcting or removing one takes a reason.
 */

export const STORE_SYS_AREA = "settlements.store_sys_import";
const BASE = "/settlements/store-sys/employee-codes";

export interface EmployeeCode {
  employee_id: number;
  full_name: string;
  active: boolean;
  /** DistriCom's canonical code of the employee in the contract (`employee_store_code`). */
  store_code: string | null;
}

export interface EmployeeCodePage {
  items: EmployeeCode[];
  total: number;
}

export interface EmployeeCodeChange {
  before: string | null;
  after: string | null;
  reason: string | null;
  changed_by_name: string | null;
  changed_at: string;
}

export interface EmployeeCodeFilters {
  q?: string;
  limit: number;
  offset: number;
}

export const storeSysApi = {
  codes: (filters: EmployeeCodeFilters) => {
    const params = new URLSearchParams({ limit: String(filters.limit), offset: String(filters.offset) });
    if (filters.q) params.set("q", filters.q);
    return apiFetch<EmployeeCodePage>(`${BASE}?${params.toString()}`);
  },
  history: (employeeId: number) => apiFetch<EmployeeCodeChange[]>(`${BASE}/${employeeId}/history`),
  setCode: (employeeId: number, storeCode: string | null, reason: string | null) =>
    apiFetch<EmployeeCode>(`${BASE}/${employeeId}`, {
      method: "PUT",
      body: JSON.stringify({ store_code: storeCode, reason }),
    }),
};

export const storeSysQueryKey = ["settlements", "store-sys"] as const;
