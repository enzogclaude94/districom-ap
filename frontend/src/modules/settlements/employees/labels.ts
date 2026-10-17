import type { BadgeProps } from "@/components/ui/badge";
import { formatMoney } from "@/lib/format";
import { parseAmount } from "@/modules/settlements/cash/amount";
import { pendingHref, pendingTitle } from "@/modules/settlements/pending/labels";
import { settlementTitle } from "@/modules/settlements/delivery/labels";

import type { AccountMovement, BalanceReading, Direction, MovementKind, MovementOrigin, TermStatus } from "./api";

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
  CONSUMPTION: "Consumo",
  ADVANCE: "Adelanto",
  ADJUSTMENT_CHARGE: "Ajuste en contra",
  ADJUSTMENT_CREDIT: "Ajuste a favor",
  CONSUMPTION_REVERSAL: "Reverso de consumo",
  ADVANCE_REVERSAL: "Reverso de adelanto",
  ADJUSTMENT_REVERSAL: "Reverso de ajuste",
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
export const employeeMovementHref = (movementId: number) => `/rendiciones/empleados/movimientos/${movementId}`;

export const MOVEMENT_KIND_LABELS: Record<MovementKind, string> = {
  CONSUMPTION: "Consumo",
  ADVANCE: "Adelanto",
  ADJUSTMENT: "Ajuste",
};

/** The name of one movement of R8-2, adjustments by direction. */
export function movementTitle(kind: MovementKind, direction: Direction | null, id?: number) {
  const name =
    kind === "ADJUSTMENT"
      ? direction === "CREDIT"
        ? "Ajuste a favor"
        : "Ajuste en contra"
      : MOVEMENT_KIND_LABELS[kind];
  return id === undefined ? name : `${name} n.º ${id}`;
}

/** What an adjustment means for the employee, in words, before any account is named (Enzo, R8-2 point 11). */
export function adjustmentEffect(direction: Direction, amount: string) {
  return direction === "CHARGE"
    ? `El empleado deberá ${formatMoney(amount)} más a la empresa.`
    : `La empresa reconocerá ${formatMoney(amount)} a favor del empleado.`;
}

/** Adjustments are the exception, never the way to record what has its own movement (ADR 010, R8-2). */
export const ADJUSTMENT_NOTICE =
  "El ajuste es excepcional y correctivo: no reemplaza un consumo, un adelanto, un préstamo, un sueldo ni un premio.";

/** The source document of a movement, with its link when it has a screen. */
export function documentOf(movement: AccountMovement): { label: string; href: string | null } {
  const { document_kind: kind, document_id: id, document_number: number } = movement;
  if (kind === null || id === null) return { label: "—", href: null };
  if (kind === "EMPLOYEE_MOVEMENT") return { label: `Movimiento n.º ${id}`, href: employeeMovementHref(id) };
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
