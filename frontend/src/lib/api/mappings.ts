import { apiFetch } from "@/lib/api/client";
import type { Page } from "@/lib/api/imports";

/**
 * Comercial › Datos › Mapeos (`/api/v1/commercial/mappings`, docs/IMPORTS.md §6.4). `viewer` reads the queue;
 * `operator` confirms, rejects or excludes an open mapping (also several confirmations at once, all or nothing);
 * `admin` reassigns a confirmed one, links an excluded one and excludes a confirmed one. Every decision re-resolves
 * the facts that took their identity from the mapping; the numbers always come from the backend.
 */

export type MappingStatus = "pending" | "ambiguous" | "confirmed" | "rejected" | "excluded";
export type MappingEntityType = "customer" | "employee";

export interface FactCount {
  kind: string;
  /** Plural business noun: «eventos de no compra». */
  label: string;
  /** Every row, superseded or voided history included (all of them follow the decision). */
  total: number;
  linked: number;
  /** The current rows: what the screen counts first. */
  current: number;
  current_linked: number;
}

/** A customer or employee as Mapeos shows it: never CUIT, phone nor address. A viewer gets only the name. */
export interface LinkTarget {
  id: number;
  name: string;
  active: boolean;
  legal_name: string | null;
  store_sys_code: string | null;
  zone: string | null;
  functions: string[];
  is_salesperson: boolean;
}

export interface Resolution {
  facts_updated: Record<string, number>;
  issues_closed: number;
}

export interface Mapping {
  id: number;
  entity_type: MappingEntityType;
  source_system: string;
  namespace: string;
  external_id: string;
  external_label: string | null;
  entity_id: number | null;
  status: MappingStatus;
  method: string | null;
  candidates: number[];
  decided_by: number | null;
  decided_at: string | null;
  decision_reason: string | null;
  created_at: string;
  updated_at: string;
  kind_label: string | null;
  first_seen_job_id: number | null;
  last_seen_job_id: number | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  decided_by_name: string | null;
  entity: LinkTarget | null;
  candidate_entities: LinkTarget[];
  facts: FactCount[];
  resolution?: Resolution | null;
}

export interface MappingImpactFact extends FactCount {
  on_target: number;
  changes_on_link: number;
  changes_on_unlink: number;
  current_changes_on_link: number;
  current_changes_on_unlink: number;
}

export interface MappingImpact {
  mapping_id: number;
  status: MappingStatus;
  entity_id: number | null;
  facts: MappingImpactFact[];
  on_link: number;
  on_unlink: number;
  current_on_link: number;
  current_on_unlink: number;
}

export interface BatchResult {
  confirmed: number;
  failed: number;
  items: { mapping_id: number; entity_id: number; facts_updated: Record<string, number> }[];
  facts_updated: Record<string, number>;
  issues_closed: number;
}

export interface MappingFilters {
  status?: MappingStatus;
  source_system?: string;
  entity_type?: MappingEntityType;
  namespace?: string;
  q?: string;
  limit: number;
  offset: number;
}

function query(params: Record<string, string | number | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params))
    if (value !== undefined && value !== "") search.set(key, String(value));
  const text = search.toString();
  return text ? `?${text}` : "";
}

const post = <T>(path: string, body: unknown) =>
  apiFetch<T>(`/commercial${path}`, { method: "POST", body: JSON.stringify(body) });

export const mappingsApi = {
  list: (filters: MappingFilters) => apiFetch<Page<Mapping>>(`/commercial/mappings${query({ ...filters })}`),
  /** The same filters as the queue, the status and the page aside: the cards count every status. */
  summary: ({ source_system, entity_type, namespace, q }: MappingFilters) =>
    apiFetch<{ counts: Record<MappingStatus, number> }>(
      `/commercial/mappings/summary${query({ source_system, entity_type, namespace, q })}`,
    ),
  get: (id: number) => apiFetch<Mapping>(`/commercial/mappings/${id}`),
  impact: (id: number, entityId?: number) =>
    apiFetch<MappingImpact>(`/commercial/mappings/${id}/impact${query({ entity_id: entityId })}`),
  targets: (id: number, q: string) =>
    apiFetch<{ items: LinkTarget[] }>(`/commercial/mappings/${id}/targets${query({ q: q.trim() || undefined })}`),
  confirm: (id: number, body: { entity_id: number; reason?: string; expected_status: MappingStatus }) =>
    post<Mapping>(`/mappings/${id}/confirm`, body),
  reassign: (id: number, body: { entity_id: number; reason: string; expected_status: MappingStatus }) =>
    post<Mapping>(`/mappings/${id}/reassign`, body),
  reject: (id: number, body: { reason?: string; expected_status: MappingStatus }) =>
    post<Mapping>(`/mappings/${id}/reject`, body),
  exclude: (id: number, body: { reason: string; expected_status: MappingStatus }) =>
    post<Mapping>(`/mappings/${id}/exclude`, body),
  batchConfirm: (items: { mapping_id: number; entity_id: number }[]) =>
    post<BatchResult>("/mappings/batch-confirm", { items }),
};

export const mappingsQueryKey = ["commercial", "mappings"] as const;
