import { apiFetch } from "@/lib/api/client";

/** Fuel history API (`/api/v1/settlements/fuel-loads`, R5-4): read-only, one row per real fuel load. */

export type FuelOrigin = "SETTLEMENT" | "DIRECT";
/**
 * OPEN_SETTLEMENT: loaded in a settlement never closed, no financial effect yet. PUBLISHED: in the close in
 * effect of its settlement, or a direct expense in effect. IN_CORRECTION: its settlement was reopened; the
 * previous close still holds. VOIDED: a settlement expense voided. REVERSED: a direct expense reversed.
 */
export type FuelState = "OPEN_SETTLEMENT" | "PUBLISHED" | "IN_CORRECTION" | "VOIDED" | "REVERSED";

export interface FuelInEffect {
  vehicle_id: number;
  vehicle_plate: string;
  employee_id: number;
  employee_name: string;
  liters: string;
  price_per_liter: string;
  amount: string;
}

export interface FuelLoadRow {
  id: number;
  expense_id: number;
  origin: FuelOrigin;
  state: FuelState;
  business_date: string;
  vehicle_id: number;
  vehicle_plate: string;
  vehicle_description: string;
  vehicle_active: boolean;
  employee_id: number;
  employee_name: string;
  employee_active: boolean;
  liters: string;
  price_per_liter: string;
  amount: string;
  odometer_km: number | null;
  settlement_id: number | null;
  settlement_sequence: number | null;
  settlement_driver_name: string | null;
  paid_from_account_name: string | null;
  created_by_name: string;
  created_at: string;
  /** IN_CORRECTION only: what the close in effect holds (null for a load added in the correction). */
  in_effect: FuelInEffect | null;
  proposed_change: boolean;
  proposed_void: boolean;
}

/** The fuel in effect among the filtered loads, computed by the backend. */
export interface FuelSummary {
  liters: string;
  amount: string;
  /** amount / liters, to the cent; null when no liter is in effect. */
  weighted_price_per_liter: string | null;
}

export interface FuelLoadPage {
  items: FuelLoadRow[];
  total: number;
  limit: number;
  offset: number;
  summary: FuelSummary;
}

export interface FuelEmployee {
  id: number;
  full_name: string;
  active: boolean;
}

export interface FuelFilters {
  vehicle_id?: number;
  employee_id?: number;
  date_from?: string;
  date_to?: string;
  origin?: FuelOrigin;
  state?: FuelState;
  limit: number;
  offset: number;
}

const BASE = "/settlements/fuel-loads";

function query(filters: FuelFilters) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  return params.toString();
}

export const fuelApi = {
  list: (filters: FuelFilters) => apiFetch<FuelLoadPage>(`${BASE}?${query(filters)}`),
  employees: () => apiFetch<FuelEmployee[]>(`${BASE}/employees`),
};

export const fuelQueryKey = ["settlements", "fuel-loads"] as const;
/** The full history, filtered by one vehicle. */
export const fuelHistoryHref = (vehicleId?: number) =>
  vehicleId === undefined ? "/rendiciones/combustible" : `/rendiciones/combustible?vehiculo=${vehicleId}`;
