import type { BadgeProps } from "@/components/ui/badge";
import { parseAmount } from "@/modules/settlements/cash/amount";
import { pendingHref, pendingTitle } from "@/modules/settlements/pending/labels";
import { settlementTitle } from "@/modules/settlements/delivery/labels";

import type { AccountMovement, BalanceReading, MovementOrigin, TermStatus } from "./api";

export const SALARY_NOTICE = "Sueldo base interno (mensual). No es el recibo de sueldo legal.";

export const READING_LABELS: Record<BalanceReading, string> = {
  OWES_COMPANY: "Debe a la empresa",
  COMPANY_OWES: "La empresa le debe",
  NONE: "Sin saldo",
};

export const READING_TONES: Record<BalanceReading, NonNullable<BadgeProps["tone"]>> = {
  OWES_COMPANY: "warning",
  COMPANY_OWES: "info",
  NONE: "neutral",
};

export const ORIGIN_LABELS: Record<MovementOrigin, string> = {
  SETTLEMENT_SHORTAGE: "Faltante de rendición",
  SETTLEMENT_SURPLUS: "Sobrante de rendición",
  SETTLEMENT_CORRECTION: "Corrección de rendición (reverso)",
  PENDING_WRITE_OFF: "Pendiente asignado al empleado",
  PENDING_WRITE_OFF_REVERSAL: "Reverso de pendiente asignado",
  OTHER: "Otro movimiento",
};

export const TERM_STATUS_LABELS: Record<TermStatus, string> = {
  CURRENT: "Vigente",
  FUTURE: "Futura",
  PAST: "Terminada",
};

/** What a signed movement means for the employee: + charges, − credits. */
export const MOVEMENT_READING = { CHARGE: "Le suma deuda", CREDIT: "Le acredita" } as const;

export const employeeFinanceHref = (employeeId: number) => `/rendiciones/empleados/${employeeId}`;

/** The source document of a movement, with its link when it has a screen. */
export function documentOf(movement: AccountMovement): { label: string; href: string | null } {
  const { document_kind: kind, document_id: id, document_number: number } = movement;
  if (kind === null || id === null) return { label: "—", href: null };
  if (kind === "PENDING_COLLECTION") return { label: pendingTitle(id), href: pendingHref(id) };
  const title = number !== null ? settlementTitle(number) : `Rendición #${id}`;
  if (kind === "OUTLET_SETTLEMENT") return { label: `${title} (boca)`, href: `/rendiciones/boca/${id}` };
  return { label: `${title} (reparto)`, href: `/rendiciones/repartos/${id}` };
}

/** A salary typed the Argentine way; unlike a movement amount, $0 is valid (e.g. a trainee). */
export function parseSalary(input: string): string | null {
  const parsed = parseAmount(input);
  if (parsed !== null) return parsed;
  return /^[\s$]*0+([.,]0{1,2})?\s*$/.test(input) ? "0.00" : null;
}
