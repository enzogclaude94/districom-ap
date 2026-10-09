import { formatDate } from "@/lib/format";
import { settlementTitle } from "@/modules/settlements/delivery/labels";
import { expenseHref } from "@/modules/settlements/expenses/api";

import type { FuelLoadRow, FuelOrigin, FuelState } from "./api";

export const ORIGIN_LABELS: Record<FuelOrigin, string> = {
  SETTLEMENT: "Rendición",
  DIRECT: "Gasto directo",
};

export const STATE_LABELS: Record<FuelState, string> = {
  OPEN_SETTLEMENT: "En rendición abierta",
  PUBLISHED: "Publicado",
  IN_CORRECTION: "En corrección",
  VOIDED: "Anulado",
  REVERSED: "Revertido",
};

export const STATE_TONES: Record<FuelState, "neutral" | "info" | "success" | "warning"> = {
  OPEN_SETTLEMENT: "info",
  PUBLISHED: "success",
  IN_CORRECTION: "warning",
  VOIDED: "neutral",
  REVERSED: "neutral",
};

/** Where the load comes from and the screen that holds it: a settlement or a direct expense, never mixed. */
export function origin(row: FuelLoadRow): { label: string; href: string } {
  if (row.origin === "SETTLEMENT" && row.settlement_id !== null) {
    return {
      label: `${settlementTitle(row.settlement_sequence ?? row.settlement_id)} · ${formatDate(row.business_date)}`,
      href: `/rendiciones/repartos/${row.settlement_id}`,
    };
  }
  return {
    label: `Gasto directo n.º ${row.expense_id} · ${row.paid_from_account_name ?? "—"}`,
    href: expenseHref(row.expense_id),
  };
}

function grouped(value: string, minPlaces: number, maxPlaces: number) {
  const [integer = "0", fraction = ""] = value.replace(/^-/, "").split(".");
  const kept = fraction.slice(0, maxPlaces).replace(/0+$/, "").padEnd(minPlaces, "0");
  const digits = (integer.replace(/^0+(?=\d)/, "") || "0").replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return kept ? `${digits},${kept}` : digits;
}

/** "1.250,500 l": liters always with their three decimals, as they are stored. */
export const liters = (value: string) => `${grouped(value, 3, 3)} l`;

/** "$ 1.499,40/l" or "$ 1.234,5678/l": a price per liter keeps up to four decimals, at least two. */
export const pricePerLiter = (value: string) => `$ ${grouped(value, 2, 4)}/l`;

/** What a correction proposes against the close in effect, in one line; null when it proposes nothing. */
export function correctionNote(row: FuelLoadRow): string | null {
  if (row.state !== "IN_CORRECTION") return null;
  if (row.proposed_void) return "La corrección la anula. Hasta el nuevo cierre sigue vigente el anterior.";
  if (!row.in_effect) return "Agregada en la corrección: no suma hasta el nuevo cierre.";
  if (!row.proposed_change) return "Sin cambios respecto del cierre vigente.";
  const was = row.in_effect;
  return `Cierre vigente: ${liters(was.liters)} · ${pricePerLiter(was.price_per_liter)} · ${was.vehicle_plate} · ${was.employee_name}. El cambio aplica al nuevo cierre.`;
}
