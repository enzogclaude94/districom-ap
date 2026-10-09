import { apiFetch } from "@/lib/api/client";

export interface Vehicle {
  id: number;
  /** Normalized plate: what the backend compares and stores. */
  plate: string;
  /** How screens print it ("AB 123 CD"), computed by the backend. */
  plate_display: string;
  description: string;
  active: boolean;
  created_at: string;
  created_by_name: string;
  updated_at: string;
  updated_by_name: string;
}

export interface VehicleEvent {
  id: number;
  event_type: "created" | "plate_changed" | "description_changed" | "activated" | "deactivated" | string;
  occurred_at: string;
  user_name: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface VehicleDetail extends Vehicle {
  history: VehicleEvent[];
}

export interface VehicleInput {
  plate: string;
  description: string;
}

export interface VehiclePatch {
  plate?: string;
  description?: string;
  active?: boolean;
}

/** Vehicles of the platform: every user reads them, only platform admins administer them. */
export const vehiclesApi = {
  list: (includeInactive = false) => apiFetch<Vehicle[]>(`/vehicles${includeInactive ? "?include_inactive=true" : ""}`),
  get: (id: number) => apiFetch<VehicleDetail>(`/vehicles/${id}`),
  create: (input: VehicleInput) => apiFetch<Vehicle>("/vehicles", { method: "POST", body: JSON.stringify(input) }),
  update: (id: number, patch: VehiclePatch) =>
    apiFetch<Vehicle>(`/vehicles/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
};

export const vehiclesQueryKey = ["vehicles"] as const;
