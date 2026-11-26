import type { BadgeProps } from "@/components/ui/badge";

/** Business words for the masters' codes (F1.7). The backend validates the values; these only name them. */

export const MASTERS = "/comercial/datos/maestros";

export const SECTIONS = [
  { key: "clientes", label: "Clientes" },
  { key: "productos", label: "Productos" },
  { key: "categorias", label: "Categorías y familias" },
  { key: "zonas", label: "Zonas" },
  { key: "vendedores", label: "Vendedores" },
  { key: "listas", label: "Listas de precios" },
  { key: "comprobantes", label: "Tipos de comprobante" },
  { key: "motivos", label: "Motivos de no compra" },
] as const;

export const ZONE_TYPES: Record<string, string> = {
  PRESALE: "Preventa",
  BANDERA: "Bandera",
  OUTLET: "Boca de expendio",
  OTHER: "Otra",
  UNCLASSIFIED: "Sin clasificar",
};

export const ZONE_ORIGINS: Record<string, string> = { RULE: "Por regla", MANUAL: "Manual", NONE: "Sin configurar" };
export const ZONE_ORIGIN_TONES: Record<string, BadgeProps["tone"]> = {
  RULE: "info",
  MANUAL: "success",
  NONE: "warning",
};

/** ISO weekdays, 1 = lunes. */
export const WEEKDAYS: [number, string, string][] = [
  [1, "Lu", "lunes"],
  [2, "Ma", "martes"],
  [3, "Mi", "miércoles"],
  [4, "Ju", "jueves"],
  [5, "Vi", "viernes"],
  [6, "Sá", "sábado"],
  [7, "Do", "domingo"],
];

export function daysLabel(days: number[]) {
  if (days.length === 0) return "—";
  return WEEKDAYS.filter(([d]) => days.includes(d))
    .map(([, short]) => short)
    .join(" · ");
}

export const PRODUCT_KINDS: Record<string, string> = {
  PRODUCT: "Producto",
  CONTAINER: "Envase",
  CRATE: "Cajón",
  SERVICE: "Servicio",
  BONUS: "Bonificación",
  COMBO: "Combo",
  PROMO: "Promoción",
  MERCH: "Material POP",
  UNCLASSIFIED: "Sin clasificar",
};

export const CATEGORY_ORIGINS: Record<string, string> = {
  MANUAL: "Manual",
  SOURCE_MAPPING: "Por rubro",
  NONE: "Sin categoría",
};

export const DOCUMENT_CLASSES: Record<string, string> = {
  INVOICE: "Factura",
  CREDIT_NOTE: "Nota de crédito",
  DEBIT_NOTE: "Nota de débito",
  RETURN: "Devolución",
  RECEIPT: "Recibo",
  INTERNAL_MOVEMENT: "Movimiento interno",
  EMPLOYEE_VOUCHER: "Vale de personal",
  OTHER: "Otro",
  UNCLASSIFIED: "Sin clasificar",
};

export const PRICE_LIST_KINDS: Record<string, string> = {
  PRESALE_OFFICIAL: "Preventa oficial",
  OUTLET: "Boca de expendio",
  PROMO_VOLUME: "Promoción por volumen",
  SPECIAL: "Especial",
  BANDERA: "Bandera",
  UNASSIGNED: "Sin asignar",
};

export const signLabel = (sign: number) => (sign > 0 ? "+1 suma" : sign < 0 ? "−1 resta" : "0 no suma");
