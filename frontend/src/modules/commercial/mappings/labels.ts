import type { BadgeProps } from "@/components/ui/badge";
import type { Mapping, MappingEntityType, MappingStatus } from "@/lib/api/mappings";

export const STATUS_LABELS: Record<MappingStatus, string> = {
  pending: "Pendiente",
  ambiguous: "Ambiguo",
  confirmed: "Confirmado",
  rejected: "Rechazado",
  excluded: "Excluido",
};

export const STATUS_TONES: Record<MappingStatus, BadgeProps["tone"]> = {
  pending: "warning",
  ambiguous: "warning",
  confirmed: "success",
  rejected: "danger",
  excluded: "neutral",
};

/** The order of the summary cards: what needs attention first. */
export const SUMMARY_ORDER: MappingStatus[] = ["pending", "ambiguous", "rejected", "confirmed", "excluded"];

export const OPEN: MappingStatus[] = ["pending", "ambiguous"];

/** The four kinds of code Comercial reviews, in business words (never `entity_type=employee`). */
export interface Kind {
  key: string;
  label: string;
  entityType: MappingEntityType;
  sourceSystem: string;
  namespace: string;
  /** How the linked entity is named in a sentence: «este preventista». */
  subject: string;
}

export const KINDS: Kind[] = [
  {
    key: "employee/AXUM/salesperson",
    label: "Preventista AXUM",
    entityType: "employee",
    sourceSystem: "AXUM",
    namespace: "salesperson",
    subject: "este preventista",
  },
  {
    key: "employee/STORE_SYS/seller",
    label: "Vendedor STORE SYS",
    entityType: "employee",
    sourceSystem: "STORE_SYS",
    namespace: "seller",
    subject: "este vendedor",
  },
  {
    key: "customer/AXUM/customer",
    label: "Cliente AXUM",
    entityType: "customer",
    sourceSystem: "AXUM",
    namespace: "customer",
    subject: "este cliente",
  },
  {
    key: "customer/STORE_SYS/customer",
    label: "Cliente STORE SYS",
    entityType: "customer",
    sourceSystem: "STORE_SYS",
    namespace: "customer",
    subject: "este cliente",
  },
];

export const SYSTEM_LABELS: Record<string, string> = { AXUM: "AXUM", STORE_SYS: "STORE SYS" };
export const ENTITY_LABELS: Record<MappingEntityType, string> = {
  customer: "Cliente",
  employee: "Vendedor / preventista",
};

export function kindOf(mapping: Pick<Mapping, "entity_type" | "source_system" | "namespace">): Kind | undefined {
  return KINDS.find((k) => k.key === `${mapping.entity_type}/${mapping.source_system}/${mapping.namespace}`);
}

export function kindLabel(mapping: Mapping) {
  return (
    mapping.kind_label ??
    kindOf(mapping)?.label ??
    `${SYSTEM_LABELS[mapping.source_system] ?? mapping.source_system} · ${mapping.namespace}`
  );
}

export function subjectOf(mapping: Mapping) {
  return kindOf(mapping)?.subject ?? (mapping.entity_type === "customer" ? "este cliente" : "este empleado");
}

export const n = (value: number) => value.toLocaleString("es-AR");

/** «655 eventos de no compra y 12 líneas de venta»: only the kinds with rows. */
export function factsSentence(facts: { label: string; count: number }[]) {
  const parts = facts.filter((f) => f.count > 0).map((f) => `${n(f.count)} ${f.label}`);
  if (parts.length === 0) return null;
  if (parts.length === 1) return parts[0];
  return `${parts.slice(0, -1).join(", ")} y ${parts[parts.length - 1]}`;
}

export function totalFacts(mapping: Mapping) {
  return mapping.facts.reduce((sum, f) => sum + f.total, 0);
}

/** Where the code appears: current rows first, the superseded history apart. */
export function appearsIn(mapping: Mapping) {
  const current = factsSentence(mapping.facts.map((f) => ({ label: f.label, count: f.current })));
  const history = mapping.facts.reduce((sum, f) => sum + f.total - f.current, 0);
  if (!current && !history) return null;
  const tail = history ? ` (y ${n(history)} reemplazados en el historial)` : "";
  return `${current ?? "ningún dato vigente"}${tail}`;
}

/** Superseded or voided rows a decision also updates, so the history never contradicts the mapping. */
export function historyNote(count: number) {
  if (!count) return "";
  return ` También se actualizan ${n(count)} ${count === 1 ? "registro reemplazado" : "registros reemplazados"} del historial.`;
}

export const METHOD_LABELS: Record<string, string> = {
  created_from_master: "Creado desde el maestro",
  exact_external_id: "Mismo código",
  rule: "Por regla",
  manual: "Decisión manual",
};

export const mappingHref = (id: number) => `/comercial/datos/mapeos/${id}`;
