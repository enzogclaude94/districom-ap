import type { BadgeProps } from "@/components/ui/badge";
import type { ImportJobStatus, ImportRowStatus, Severity } from "@/lib/api/imports";

/** STORE SYS reports the screen imports in F1.3b, in the order an operator loads them. AXUM arrives in F1.4. */
export const STORE_SYS_TYPES = [
  {
    key: "commercial.store_sys.customers",
    label: "Clientes",
    hint: "Maestro de clientes («clientes.xls»): una foto del día.",
    snapshot: true,
    scope: false,
  },
  {
    key: "commercial.store_sys.products",
    label: "Artículos",
    hint: "Maestro de artículos («lista de articulos.xls»): una foto del día.",
    snapshot: true,
    scope: false,
  },
  {
    key: "commercial.store_sys.documents",
    label: "Comprobantes",
    hint: "Comprobantes emitidos («comprobantes emitidos.xls»).",
    snapshot: false,
    scope: false,
  },
  {
    key: "commercial.store_sys.sales_lines",
    label: "Ventas",
    hint: "Ventas artículos/clientes («ventas.xls»), de uno o varios días.",
    snapshot: false,
    scope: true,
  },
] as const;

export type StoreSysTypeKey = (typeof STORE_SYS_TYPES)[number]["key"];
export const SALES_TYPE: StoreSysTypeKey = "commercial.store_sys.sales_lines";
export const CUSTOMERS_TYPE: StoreSysTypeKey = "commercial.store_sys.customers";
export const PRODUCTS_TYPE: StoreSysTypeKey = "commercial.store_sys.products";
export const DOCUMENTS_TYPE: StoreSysTypeKey = "commercial.store_sys.documents";

export function typeLabel(key: string | null, fallback?: string | null) {
  return STORE_SYS_TYPES.find((t) => t.key === key)?.label ?? fallback ?? "Sin tipo reconocido";
}

export const STATUS_LABELS: Record<ImportJobStatus, string> = {
  pending: "Para revisar",
  processing: "Procesando",
  completed: "Cargada",
  completed_with_warnings: "Cargada con avisos",
  failed: "No se pudo leer",
  duplicate: "Ya estaba cargada",
  cancelled: "Descartada",
};

export const STATUS_TONES: Record<ImportJobStatus, NonNullable<BadgeProps["tone"]>> = {
  pending: "info",
  processing: "info",
  completed: "success",
  completed_with_warnings: "warning",
  failed: "danger",
  duplicate: "neutral",
  cancelled: "neutral",
};

export const ROW_STATUS_LABELS: Record<ImportRowStatus, string> = {
  valid: "Correcta",
  warning: "Con aviso",
  rejected: "Rechazada",
  duplicate: "Repetida",
  pending_resolution: "Pendiente de vincular",
  skipped: "Ignorada",
};

export const SEVERITY_LABELS: Record<Severity, string> = {
  error: "Rechazadas",
  warning: "Avisos",
  info: "Para tener en cuenta",
};

export const SEVERITY_TONES: Record<Severity, NonNullable<BadgeProps["tone"]>> = {
  error: "danger",
  warning: "warning",
  info: "neutral",
};

/** Why a confirmation needs an admin (sales replacement, docs/IMPORTS.md §5.3). */
export const BLOCK_REASONS: Record<string, string> = {
  new_scope_not_full:
    "Este archivo no está declarado como export completo, así que no se puede saber si reemplaza todo lo cargado en esos días.",
  previous_scope_not_full: "Lo que ya está cargado en esos días vino de un export filtrado o sin alcance declarado.",
  days_without_rows: "Hay días del período ya cargados que este archivo no trae.",
};
