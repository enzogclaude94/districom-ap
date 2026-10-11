import { apiFetch } from "@/lib/api/client";

/**
 * Responsables of the bocas de expendio (`/api/v1/settlements/...`, R7-1). A responsibility is in effect on
 * a day when `valid_from <= day <= valid_to`, both inclusive, with no upper bound while `valid_to` is null.
 */
export interface Responsibility {
  id: number;
  outlet_id: number;
  outlet_code: string;
  outlet_name: string;
  outlet_active: boolean;
  employee_id: number;
  employee_name: string;
  employee_active: boolean;
  valid_from: string;
  valid_to: string | null;
  created_at: string;
  created_by_name: string;
  ended_at: string | null;
  ended_by_name: string | null;
  end_reason: string | null;
}

export interface ResponsibilityPage {
  items: Responsibility[];
  total: number;
}

export interface SelectableEmployee {
  id: number;
  full_name: string;
}

export const responsibilitiesApi = {
  ofOutlet: (outletId: number, params: { on?: string; limit?: number; offset?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.on) query.set("on", params.on);
    if (params.limit !== undefined) query.set("limit", String(params.limit));
    if (params.offset !== undefined) query.set("offset", String(params.offset));
    const suffix = query.size ? `?${query}` : "";
    return apiFetch<ResponsibilityPage>(`/settlements/outlets/${outletId}/responsibilities${suffix}`);
  },
  employees: () => apiFetch<SelectableEmployee[]>("/settlements/outlet-responsibilities/employees"),
  create: (outletId: number, body: { employee_id: number; valid_from: string }) =>
    apiFetch<Responsibility>(`/settlements/outlets/${outletId}/responsibilities`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  end: (id: number, body: { valid_to: string; reason: string }) =>
    apiFetch<Responsibility>(`/settlements/outlet-responsibilities/${id}/end`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

export const responsibilitiesQueryKey = ["settlements", "outlet-responsibilities"] as const;
