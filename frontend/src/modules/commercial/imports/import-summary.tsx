import type { ReactNode } from "react";

import type { ImportJobDetail, ReplacementPlan } from "@/lib/api/imports";
import { formatDate } from "@/lib/format";

import { BLOCK_REASONS, CUSTOMERS_TYPE, DOCUMENTS_TYPE, NO_PURCHASE_TYPE, PRODUCTS_TYPE, SALES_TYPE } from "./labels";

/** The importer's preview, read defensively: it is free-form JSON that each importer writes (docs/IMPORTS.md §3). */
type Preview = Record<string, unknown>;
const record = (value: unknown): Preview => (value && typeof value === "object" ? (value as Preview) : {});
const count = (value: unknown) => (typeof value === "number" ? value : 0);
const list = (value: unknown) => (Array.isArray(value) ? value.map(String) : []);
const n = (value: number) => value.toLocaleString("es-AR");
const plural = (value: number, one: string, many: string) => `${n(value)} ${value === 1 ? one : many}`;

export function preview(job: ImportJobDetail): Preview {
  return record(job.metadata.preview);
}

export function replacementOf(job: ImportJobDetail): ReplacementPlan | null {
  const plan = record(preview(job).replacement);
  return typeof plan.action === "string" ? (plan as unknown as ReplacementPlan) : null;
}

/** Days as "01/10/2026" or a short range when they are many. */
export function daysText(days: string[]) {
  if (days.length === 0) return "—";
  if (days.length <= 3) return days.map(formatDate).join(", ");
  return `${formatDate(days[0])} al ${formatDate(days[days.length - 1])}`;
}

export interface Fact {
  label: string;
  value: ReactNode;
  attention?: boolean;
}

function sales(job: ImportJobDetail): Fact[] {
  const p = preview(job);
  const products = record(p.products);
  const customers = record(p.customers);
  const sellers = record(p.sellers);
  const lists = record(p.price_lists);
  const unknownProducts = count(products.unknown ?? p.unknown_products);
  const unresolved = count(customers.unresolved);
  const unmapped = list(sellers.unmapped);
  const unknownLists = list(lists.unknown);
  return [
    {
      label: "Artículos que no están en el maestro",
      value: unknownProducts
        ? `${plural(unknownProducts, "artículo", "artículos")} (${plural(count(products.unknown_lines), "línea", "líneas")})`
        : "Ninguno",
      attention: unknownProducts > 0,
    },
    {
      label: "Clientes que no están en el maestro",
      value: unresolved
        ? `${plural(unresolved, "código", "códigos")} (${plural(count(customers.unresolved_lines), "línea", "líneas")})`
        : count(customers.unresolved_lines)
          ? plural(count(customers.unresolved_lines), "línea", "líneas")
          : "Ninguno",
      attention: count(customers.unresolved_lines) > 0,
    },
    {
      label: "Vendedores sin vincular a un empleado",
      value: unmapped.length
        ? `${unmapped.join(", ")} (${plural(count(sellers.unmapped_lines), "línea", "líneas")})`
        : "Ninguno",
      attention: unmapped.length > 0,
    },
    {
      label: "Listas de precios desconocidas",
      value: unknownLists.length
        ? `${unknownLists.join(", ")} (${plural(count(lists.unknown_lines), "línea", "líneas")})`
        : "Ninguna",
      attention: unknownLists.length > 0,
    },
  ];
}

function customers(job: ImportJobDetail): Fact[] {
  const p = preview(job);
  const c = record(p.customers);
  const zones = list(p.new_zones);
  const sellers = list(p.unknown_sellers);
  return [
    { label: "Clientes nuevos", value: n(count(c.new)) },
    { label: "Clientes con datos cambiados", value: n(count(c.updated)) },
    { label: "Clientes sin cambios", value: n(count(c.unchanged)) },
    {
      label: "Clientes conocidos que no vienen en esta foto",
      value: n(count(p.missing_from_master)),
      attention: count(p.missing_from_master) > 0,
    },
    { label: "Zonas nuevas", value: zones.length ? zones.join(", ") : "Ninguna", attention: zones.length > 0 },
    {
      label: "Vendedores sin vincular a un empleado",
      value: sellers.length ? sellers.join(", ") : "Ninguno",
      attention: sellers.length > 0,
    },
  ];
}

function products(job: ImportJobDetail): Fact[] {
  const p = preview(job);
  const c = record(p.products);
  const prices = record(p.prices);
  return [
    { label: "Artículos nuevos", value: n(count(c.new)) },
    { label: "Artículos con datos cambiados", value: n(count(c.updated)) },
    { label: "Artículos sin cambios", value: n(count(c.unchanged)) },
    {
      label: "Precios",
      value: prices.loaded
        ? `${plural(count(prices.rows_with_price), "precio", "precios")} para la Lista ${String(prices.price_list_number)}`
        : "No se cargan: falta configurar a qué lista corresponde el precio del maestro",
      attention: !prices.loaded,
    },
    {
      label: "Códigos repetidos en el archivo",
      value: n(count(p.duplicate_codes)),
      attention: count(p.duplicate_codes) > 0,
    },
    {
      label: "Artículos conocidos que no vienen en esta foto",
      value: n(count(p.missing_from_master)),
      attention: count(p.missing_from_master) > 0,
    },
  ];
}

function documents(job: ImportJobDetail): Fact[] {
  const p = preview(job);
  const d = record(p.documents);
  const byType = record(p.by_type);
  const types = list(p.new_types);
  return [
    { label: "Comprobantes nuevos", value: n(count(d.new)) },
    { label: "Ya importados antes", value: n(count(d.already_imported)) },
    {
      label: "Con datos distintos a los ya importados (no se pisan)",
      value: n(count(d.changed)),
      attention: count(d.changed) > 0,
    },
    {
      label: "Por tipo",
      value:
        Object.entries(byType)
          .map(([name, value]) => `${name}: ${n(count(record(value).rows))}`)
          .join(" · ") || "—",
    },
    {
      label: "Tipos de comprobante nuevos",
      value: types.length ? types.join(", ") : "Ninguno",
      attention: types.length > 0,
    },
  ];
}

/** Business names of the no-purchase reason categories (DATA_MODEL.md §4.2). */
const REASON_CATEGORIES: Record<string, string> = {
  STOCK: "Tiene stock",
  NO_DISPONIBLE: "No disponible (cerrado, sin encargado, vacaciones)",
  OTRO_CANAL: "Compra por otro canal",
  RESTRICCION_FINANCIERA: "Restricción financiera",
  SIN_CATEGORIA: "Sin categoría",
};

function noPurchases(job: ImportJobDetail): Fact[] {
  const p = preview(job);
  const report = record(p.report);
  const customers = record(p.customers);
  const salespeople = record(p.salespeople);
  const reasons = record(p.reasons);
  const pendingPeople = list(salespeople.pending);
  const byCategory = Object.entries(record(reasons.by_category));
  const pendingCustomers = count(customers.pending) + count(customers.unknown_code) + count(customers.name_mismatch);
  const dateWarnings = count(p.date_warnings);
  return [
    { label: "Filas del reporte (sin títulos ni grupos)", value: n(count(report.data_rows)) },
    {
      label: "Compró (no se cargan: quedan solo en la importación)",
      value: plural(count(report.purchases_skipped), "fila", "filas"),
    },
    { label: "No compras a cargar", value: n(count(report.no_purchase_events)) },
    {
      label: "Clientes vinculados",
      value: `${plural(count(customers.mapped) + count(customers.by_rule), "cliente", "clientes")}${
        count(customers.by_rule) ? ` (${n(count(customers.by_rule))} por la regla de código y nombre de STORE SYS)` : ""
      }`,
    },
    {
      label: "Clientes sin vincular",
      value: pendingCustomers
        ? [
            count(customers.unknown_code) &&
              plural(
                count(customers.unknown_code),
                "código que no existe en STORE SYS",
                "códigos que no existen en STORE SYS",
              ),
            count(customers.name_mismatch) &&
              plural(count(customers.name_mismatch), "código con otro nombre", "códigos con otro nombre"),
            count(customers.pending) && plural(count(customers.pending), "código pendiente", "códigos pendientes"),
          ]
            .filter(Boolean)
            .join(" · ") + ` (${plural(count(customers.unresolved_events), "no compra", "no compras")})`
        : "Ninguno",
      attention: pendingCustomers > 0,
    },
    {
      label: "Preventistas sin vincular a un empleado",
      value: pendingPeople.length ? pendingPeople.map((code) => `Vendedor ${code}`).join(", ") : "Ninguno",
      attention: pendingPeople.length > 0,
    },
    {
      label: "Motivos por categoría",
      value:
        byCategory.map(([code, value]) => `${REASON_CATEGORIES[code] ?? code}: ${n(count(value))}`).join(" · ") || "—",
      attention: count(reasons.unmapped) > 0,
    },
    {
      label: "Horarios para revisar",
      value: dateWarnings
        ? `${plural(dateWarnings, "fila empieza", "filas empiezan")} después de la hora de subida: se toma el día de la subida`
        : "Ninguno",
      attention: dateWarnings > 0,
    },
  ];
}

/** The importer's own summary, in business words. */
export function typeFacts(job: ImportJobDetail): Fact[] {
  switch (job.import_type) {
    case SALES_TYPE:
      return sales(job);
    case CUSTOMERS_TYPE:
      return customers(job);
    case PRODUCTS_TYPE:
      return products(job);
    case DOCUMENTS_TYPE:
      return documents(job);
    case NO_PURCHASE_TYPE:
      return noPurchases(job);
    default:
      return [];
  }
}

/** What the plan replaces, in the words of each report: sales lines or AXUM no-purchases. */
export interface Subject {
  loaded: string;
  one: string;
  many: string;
}
export const SALES_SUBJECT: Subject = { loaded: "ventas", one: "línea ya cargada", many: "líneas ya cargadas" };
export const NO_PURCHASE_SUBJECT: Subject = {
  loaded: "no compras",
  one: "no compra ya cargada",
  many: "no compras ya cargadas",
};

/** What confirming a file does to what is already loaded, in one sentence plus its reasons when blocked. */
export function replacementText(plan: ReplacementPlan, days: string[], subject: Subject = SALES_SUBJECT) {
  if (plan.action === "insert") {
    return {
      title: `Carga ${subject.loaded} de ${plural(days.length, "día", "días")} que todavía no estaban cargados.`,
      reasons: [] as string[],
    };
  }
  const jobs = plan.replaces.map((r) => `#${r.job_id}`).join(", ");
  if (plan.action === "replace") {
    return {
      title: `Reemplaza ${plural(plan.lines, subject.one, subject.many)} de ${plural(plan.overlap_days.length, "día", "días")} (${daysText(plan.overlap_days)}, importación ${jobs}). Las anteriores quedan en la historia como reemplazadas: no se borra nada.`,
      reasons: [],
    };
  }
  const reasons = plan.reasons.map((r) => BLOCK_REASONS[r] ?? r);
  if (plan.uncovered_days.length)
    reasons.push(`Días ya cargados que este archivo no trae: ${daysText(plan.uncovered_days)}.`);
  const kept = plan.kept_other_salespeople ?? 0;
  if (kept)
    reasons.push(
      `Si reemplazás, ${plural(kept, "no compra de otro preventista queda", "no compras de otros preventistas quedan")} como están.`,
    );
  return {
    title: `Se superpone con ${plural(plan.lines, subject.one, subject.many)} de ${plural(plan.overlap_days.length, "día", "días")} (${daysText(plan.overlap_days)}, importación ${jobs}) y DistriCom no puede probar qué reemplaza.`,
    reasons,
  };
}
