import { apiFetch } from "@/lib/api/client";

export interface Outlet {
  id: number;
  /** Mandatory, unique and immutable: the boca's identity next to its id. */
  code: string;
  name: string;
  description: string | null;
  active: boolean;
  created_at: string;
  created_by_name: string;
  updated_at: string;
  updated_by_name: string;
}

export interface OutletEvent {
  id: number;
  event_type: "created" | "name_changed" | "description_changed" | "deactivated" | "reactivated" | string;
  occurred_at: string;
  user_name: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface OutletDetail extends Outlet {
  history: OutletEvent[];
}

export interface OutletInput {
  code: string;
  name: string;
  description?: string | null;
}

/** The code is not here: it never changes. An empty description clears it. */
export interface OutletPatch {
  name?: string;
  description?: string;
  active?: boolean;
}

/** Bocas de expendio of the platform: every user reads them, only platform admins administer them. */
export const outletsApi = {
  list: (includeInactive = false) => apiFetch<Outlet[]>(`/outlets${includeInactive ? "?include_inactive=true" : ""}`),
  get: (id: number) => apiFetch<OutletDetail>(`/outlets/${id}`),
  create: (input: OutletInput) => apiFetch<Outlet>("/outlets", { method: "POST", body: JSON.stringify(input) }),
  update: (id: number, patch: OutletPatch) =>
    apiFetch<Outlet>(`/outlets/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
};

export const outletsQueryKey = ["outlets"] as const;
