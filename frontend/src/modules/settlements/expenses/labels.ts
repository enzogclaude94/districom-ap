import { formatQuantity } from "@/modules/settlements/delivery/fuel";

import type { ExpenseItem, ExpenseOrigin, ExpenseState } from "./api";

export const ORIGIN_LABELS: Record<ExpenseOrigin, string> = {
  DIRECT: "Gasto directo",
  SETTLEMENT: "De rendición",
};

export const STATE_LABELS: Record<ExpenseState, string> = {
  IN_EFFECT: "Vigente",
  VOIDED: "Anulado",
  REVERSED: "Revertido",
};

export const STATE_TONES: Record<ExpenseState, "neutral" | "warning"> = {
  IN_EFFECT: "neutral",
  VOIDED: "warning",
  REVERSED: "warning",
};

/** "Combustible · AA 123 BB · 45,678 l" style line for a fuel expense; null for any other. */
export function fuelSummary(expense: Pick<ExpenseItem, "fuel" | "legacy_fuel_without_detail">): string | null {
  if (expense.legacy_fuel_without_detail) return "Sin detalle de carga (anterior a R5‑2)";
  if (!expense.fuel) return null;
  return `${expense.fuel.vehicle_plate} · ${formatQuantity(expense.fuel.liters)} l · ${expense.fuel.employee_name}`;
}

const EVENT_LABELS: Record<string, string> = {
  "settlements.expense.created": "Gasto registrado",
  "settlements.expense.reversed": "Gasto revertido",
  "settlements.expense.corrected": "Corrección descriptiva",
};

export const eventLabel = (type: string) => EVENT_LABELS[type] ?? type;
