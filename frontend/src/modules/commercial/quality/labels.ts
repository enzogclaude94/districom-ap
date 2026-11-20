import type { BadgeProps } from "@/components/ui/badge";
import type { Issue, IssueSource, IssueStatus, Severity } from "@/lib/api/quality";

import { importHref } from "../imports/upload-form";
import { mappingHref } from "../mappings/labels";

export const SEVERITY_LABELS: Record<Severity, string> = {
  error: "Error",
  warning: "Advertencia",
  info: "Información",
};

export const SEVERITY_PLURALS: Record<Severity, string> = {
  error: "Errores",
  warning: "Advertencias",
  info: "Informativos",
};

export const SEVERITY_TONES: Record<Severity, BadgeProps["tone"]> = {
  error: "danger",
  warning: "warning",
  info: "info",
};

/** Most serious first: the order of the cards and of the queue. */
export const SEVERITIES: Severity[] = ["error", "warning", "info"];

export const STATUS_LABELS: Record<IssueStatus, string> = {
  open: "Abierto",
  resolved: "Resuelto",
  ignored: "Ignorado",
};

export const STATUS_PLURALS: Record<IssueStatus, string> = {
  open: "Abiertos",
  resolved: "Resueltos",
  ignored: "Ignorados",
};

export const STATUS_TONES: Record<IssueStatus, BadgeProps["tone"]> = {
  open: "warning",
  resolved: "success",
  ignored: "neutral",
};

export const qualityHref = (id: number) => `/comercial/datos/calidad/${id}`;

export const n = (value: number) => value.toLocaleString("es-AR");

/** «STORE SYS · Ventas, importación #12, fila 40». */
export function originText(source: IssueSource) {
  if (source.job_id === null) return null;
  const parts = [`${source.import_label ?? "Importación"} · importación #${source.job_id}`];
  if (source.row_number !== null) parts.push(`fila ${n(source.row_number)}`);
  return parts.join(", ");
}

/** Where a person corrects the cause, when a screen for it exists. Never a guess: only what the code declares. */
export function fixLink(issue: Pick<Issue, "fix" | "mapping_id" | "source">) {
  if (issue.fix === "mapping")
    return issue.mapping_id !== null
      ? { href: mappingHref(issue.mapping_id), label: "Corregir en Mapeos" }
      : { href: "/comercial/datos/mapeos", label: "Ir a Mapeos" };
  if (issue.fix === "import" && issue.source.job_id !== null)
    return { href: importHref(issue.source.job_id), label: "Ver la importación" };
  return null;
}
