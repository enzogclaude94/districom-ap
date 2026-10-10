import { ApiError, apiFetch } from "@/lib/api/client";

export interface ThirdParty {
  id: number;
  name: string;
  /** 11 digits as stored, or null: the CUIT is optional. */
  tax_id: string | null;
  /** «30-71234567-1», computed by the backend. */
  tax_id_display: string | null;
  notes: string | null;
  active: boolean;
  created_at: string;
  created_by_name: string;
  updated_at: string;
  updated_by_name: string;
}

export interface ThirdPartyEvent {
  id: number;
  event_type: "created" | "name_changed" | "tax_id_changed" | "notes_changed" | "activated" | "deactivated" | string;
  occurred_at: string;
  user_name: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface ThirdPartyDetail extends ThirdParty {
  history: ThirdPartyEvent[];
}

/** Answer to «posible duplicado»: it is another third party, and why (kept in its history). */
export interface DuplicateConfirmation {
  confirm_not_duplicate?: boolean;
  duplicate_reason?: string;
}

export interface ThirdPartyInput extends DuplicateConfirmation {
  name: string;
  tax_id?: string | null;
  notes?: string | null;
}

export interface ThirdPartyPatch extends DuplicateConfirmation {
  name?: string;
  /** An empty string removes the CUIT. */
  tax_id?: string;
  notes?: string;
  active?: boolean;
}

/** A homonym the backend found: same name, and at least one of the two without CUIT. */
export interface DuplicateCandidate {
  third_party_id: number;
  name: string;
  tax_id_display: string | null;
  active: boolean;
}

/** The candidates of a `third_party_possible_duplicate` error, or null for any other error. */
export function duplicateCandidates(error: unknown): DuplicateCandidate[] | null {
  if (!(error instanceof ApiError) || error.code !== "third_party_possible_duplicate") return null;
  const details = error.details as { candidates?: unknown } | null;
  return Array.isArray(details?.candidates) ? (details.candidates as DuplicateCandidate[]) : [];
}

function query(includeInactive: boolean, q?: string) {
  const params = new URLSearchParams();
  if (includeInactive) params.set("include_inactive", "true");
  if (q?.trim()) params.set("q", q.trim());
  const text = params.toString();
  return text ? `?${text}` : "";
}

/** Third parties of the platform: every user reads them, only platform admins administer them. */
export const thirdPartiesApi = {
  list: (includeInactive = false, q?: string) => apiFetch<ThirdParty[]>(`/third-parties${query(includeInactive, q)}`),
  get: (id: number) => apiFetch<ThirdPartyDetail>(`/third-parties/${id}`),
  create: (input: ThirdPartyInput) =>
    apiFetch<ThirdParty>("/third-parties", { method: "POST", body: JSON.stringify(input) }),
  update: (id: number, patch: ThirdPartyPatch) =>
    apiFetch<ThirdParty>(`/third-parties/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
};

export const thirdPartiesQueryKey = ["third-parties"] as const;
export const thirdPartyHref = (id: number) => `/terceros/${id}`;

/** Same rule as the backend: spaces, dots, hyphens and slashes are ignored. */
export const normalizeTaxId = (raw: string) => raw.replace(/[\s./-]+/g, "");

/** Whether 11 digits are a CUIT/CUIL with a valid check digit, the same computation as the backend. */
export function isValidTaxId(digits: string): boolean {
  if (!/^\d{11}$/.test(digits)) return false;
  const weights = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2];
  const sum = weights.reduce((total, weight, i) => total + weight * Number(digits[i]), 0);
  const rest = 11 - (sum % 11);
  const check = rest === 11 ? 0 : rest;
  return check !== 10 && check === Number(digits[10]);
}
