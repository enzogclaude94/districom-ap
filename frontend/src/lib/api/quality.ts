import { apiFetch } from "@/lib/api/client";
import type { Page } from "@/lib/api/imports";

/**
 * Comercial › Datos › Calidad (`/api/v1/quality`, docs/IMPORTS.md §7.1): the data quality issues the imports open.
 * `viewer` reads; `operator` ignores an open issue with a note; `admin` also marks it resolved by hand with a note.
 * The cause is corrected where it lives (Mapeos, the import): those issues close by themselves.
 */

export type IssueStatus = "open" | "resolved" | "ignored";
export type Severity = "error" | "warning" | "info";
/** Where the cause is corrected; null: no screen yet (Maestros). */
export type Fix = "mapping" | "import" | null;

export interface QualityGroup {
  key: string;
  label: string;
}

export interface QualityCode {
  code: string;
  label: string;
  severity: Severity;
  group: QualityGroup;
  description: string;
  action: string;
  closes_when: string;
  fix: Fix;
}

export interface IssueSource {
  job_id: number | null;
  import_type: string | null;
  import_label: string | null;
  row_id: number | null;
  row_number: number | null;
  sheet: string | null;
}

export interface Issue {
  id: number;
  code: string;
  label: string;
  severity: Severity;
  status: IssueStatus;
  group: QualityGroup;
  /** What it affects, in business words: «Cliente ALMACEN UNO», «Vendedor STORE SYS 05». */
  subject: string | null;
  occurrences: number;
  first_seen_at: string;
  last_seen_at: string;
  source: IssueSource;
  mapping_id: number | null;
  fix: Fix;
  action: string;
  resolved_at: string | null;
  resolved_by_name: string | null;
}

export interface IssueDetail extends Issue {
  description: string;
  closes_when: string;
  facts: Record<string, unknown>;
  resolution_note: string | null;
  resolved_automatically: boolean;
  dedupe_key: string | null;
  entity_type: string | null;
  entity_id: number | null;
}

export interface Counted {
  issues: number;
  occurrences: number;
}

export interface IssueSummary {
  open: Partial<Record<Severity, Counted>>;
  open_total: Counted;
  resolved: Counted;
  ignored: Counted;
}

export interface IssueFilters {
  status?: IssueStatus;
  severity?: Severity;
  code?: string;
  group?: string;
  job_id?: number;
  seen_from?: string;
  seen_to?: string;
  q?: string;
  limit: number;
  offset: number;
}

const MODULE = "commercial";

function query(params: Record<string, string | number | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params))
    if (value !== undefined && value !== "") search.set(key, String(value));
  return `?${search.toString()}`;
}

const post = (path: string, note: string) =>
  apiFetch<IssueDetail>(`/quality/issues${path}`, { method: "POST", body: JSON.stringify({ note }) });

export const qualityApi = {
  codes: () => apiFetch<QualityCode[]>(`/quality/codes${query({ module: MODULE })}`),
  list: (filters: IssueFilters) => apiFetch<Page<Issue>>(`/quality/issues${query({ module: MODULE, ...filters })}`),
  /** The list's filters, the status, severity and page aside: the cards count every status and severity. */
  summary: ({ code, group, job_id, seen_from, seen_to, q }: IssueFilters) =>
    apiFetch<IssueSummary>(
      `/quality/issues/summary${query({ module: MODULE, code, group, job_id, seen_from, seen_to, q })}`,
    ),
  get: (id: number) => apiFetch<IssueDetail>(`/quality/issues/${id}`),
  ignore: (id: number, note: string) => post(`/${id}/ignore`, note),
  resolve: (id: number, note: string) => post(`/${id}/resolve`, note),
};

export const qualityQueryKey = ["commercial", "quality"] as const;
