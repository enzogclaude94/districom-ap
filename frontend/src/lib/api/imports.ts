import { apiFetch } from "@/lib/api/client";

/**
 * Generic import API (`/api/v1/imports`, docs/IMPORTS.md §8). Each type belongs to a module, whose roles decide:
 * `viewer` reads, `operator` uploads, confirms and cancels, `admin` answers a blocked confirmation and reprocesses.
 */

export type ImportJobStatus =
  "pending" | "processing" | "completed" | "completed_with_warnings" | "failed" | "duplicate" | "cancelled";
export type ImportRowStatus = "valid" | "warning" | "rejected" | "duplicate" | "pending_resolution" | "skipped";
export type Severity = "error" | "warning" | "info";
export type ScopeDeclaration = "FULL" | "FILTERED";
export type ImportDecision = "replace" | "additional";

export interface ImportType {
  key: string;
  name: string;
  module_key: string;
  source_system: string;
  can_import: boolean;
}

export interface RowCounts {
  detected: number;
  processed: number;
  valid: number;
  rejected: number;
  duplicate: number;
  skipped: number;
  pending_resolution: number;
  inserted: number;
  updated: number;
  unchanged: number;
  superseded: number;
  warnings: number;
  errors: number;
}

export interface ImportFile {
  id: number;
  original_filename: string;
  size_bytes: number;
  detected_format: string | null;
  sha256: string;
  uploaded_at: string;
}

/** What the sales importer would do to the lines already loaded in the file's days (docs/IMPORTS.md §5.3). */
export interface ReplacementPlan {
  action: "insert" | "replace" | "blocked";
  overlap_days: string[];
  uncovered_days: string[];
  reasons: string[];
  replaces: { job_id: number; scope: string | null; days: string[]; lines: number }[];
  lines: number;
}

export interface ImportJobDetail {
  id: number;
  import_type: string | null;
  import_type_name: string | null;
  source_system: string | null;
  status: ImportJobStatus;
  parser_version: string | null;
  file: ImportFile;
  created_by_name: string;
  confirmed_by_name: string | null;
  created_at: string;
  analyzed_at: string | null;
  confirmed_at: string | null;
  finished_at: string | null;
  counts: RowCounts;
  snapshot_date: string | null;
  period_from: string | null;
  period_to: string | null;
  scope: { declared?: ScopeDeclaration | null; note?: string | null; days?: string[] } | null;
  /** Reader, detection and the importer's preview (`metadata.preview`): counts and labels, never row values. */
  metadata: { preview?: Record<string, unknown>; declared?: Record<string, unknown> } & Record<string, unknown>;
  failure_reason: string | null;
  failure_detail: string | null;
  reprocess_of_job_id: number | null;
  duplicate_of_job_id: number | null;
  can_confirm: boolean;
  can_decide: boolean;
  can_reprocess: boolean;
}

export interface ImportJobItem {
  id: number;
  import_type: string | null;
  import_type_name: string | null;
  status: ImportJobStatus;
  original_filename: string;
  created_by_name: string;
  created_at: string;
  finished_at: string | null;
  rows_detected: number;
  /** Data rows: the detected ones minus the title and header rows the importer skips. */
  rows_processed: number;
  rows_rejected: number;
  failure_reason: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
}

export interface ImportMessage {
  code: string;
  severity: Severity;
  field: string | null;
  message: string;
  rows: number;
}

export interface RowMessage {
  code: string;
  severity: Severity;
  text: string;
  column?: string | null;
}

/** A row of the file in staging. The screen shows its number, state, key and messages, never its raw cells. */
export interface ImportRow {
  id: number;
  sheet: string;
  row_number: number;
  status: ImportRowStatus;
  outcome: string | null;
  business_key: string | null;
  entity_type: string | null;
  entity_id: number | null;
  messages: RowMessage[];
}

export interface QualityIssue {
  id: number;
  code: string;
  severity: Severity;
  status: string;
  occurrences: number;
}

export interface UploadInput {
  file: File;
  importType?: string;
  snapshotDate?: string;
  scope?: ScopeDeclaration;
  scopeNote?: string;
}

export interface JobFilters {
  /** Only this module's types (and the files no type recognised). */
  module?: string;
  import_type?: string;
  status?: ImportJobStatus;
  limit: number;
  offset: number;
}

export interface RowFilters {
  status?: ImportRowStatus;
  code?: string;
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

export const importsApi = {
  types: () => apiFetch<ImportType[]>("/imports/types"),
  list: (filters: JobFilters) => apiFetch<Page<ImportJobItem>>(`/imports${query({ ...filters })}`),
  get: (id: number) => apiFetch<ImportJobDetail>(`/imports/${id}`),
  upload: ({ file, importType, snapshotDate, scope, scopeNote }: UploadInput) => {
    const form = new FormData();
    form.set("file", file);
    if (importType) form.set("import_type", importType);
    if (snapshotDate) form.set("snapshot_date", snapshotDate);
    if (scope) form.set("scope", scope);
    if (scope && scopeNote?.trim()) form.set("scope_note", scopeNote.trim());
    return apiFetch<ImportJobDetail>("/imports", { method: "POST", body: form });
  },
  confirm: (id: number, decision?: { decision: ImportDecision; reason: string }) =>
    apiFetch<ImportJobDetail>(`/imports/${id}/confirm`, {
      method: "POST",
      body: decision ? JSON.stringify(decision) : undefined,
    }),
  cancel: (id: number) => apiFetch<ImportJobDetail>(`/imports/${id}/cancel`, { method: "POST" }),
  rows: (id: number, filters: RowFilters) => apiFetch<Page<ImportRow>>(`/imports/${id}/rows${query({ ...filters })}`),
  messages: (id: number) => apiFetch<ImportMessage[]>(`/imports/${id}/messages`),
  issues: (id: number) => apiFetch<QualityIssue[]>(`/imports/${id}/issues`),
};

export const importsQueryKey = ["imports"] as const;
