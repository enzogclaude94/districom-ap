import type { BadgeProps } from "@/components/ui/badge";
import { formatMoney } from "@/lib/format";

import type { DifferenceKind, ExpectedOrigin, ExpectedOutcome, FinancialState, TransferStatus } from "./api";

type Tone = NonNullable<BadgeProps["tone"]>;

export const FINANCIAL_STATE_LABELS: Record<FinancialState, string> = {
  NOT_CLOSED: "Sin cerrar",
  CLOSED: "Cerrada",
  BEING_CORRECTED: "En corrección",
};

export const FINANCIAL_STATE_TONES: Record<FinancialState, Tone> = {
  NOT_CLOSED: "info",
  CLOSED: "success",
  BEING_CORRECTED: "warning",
};

export const TRANSFER_STATUS_LABELS: Record<TransferStatus, string> = {
  DECLARED: "Declarada",
  VERIFIED: "Verificada",
  REJECTED: "Rechazada",
};

export const TRANSFER_STATUS_TONES: Record<TransferStatus, Tone> = {
  DECLARED: "warning",
  VERIFIED: "success",
  REJECTED: "danger",
};

export const DIFFERENCE_LABELS: Record<DifferenceKind, string> = {
  SHORTAGE: "Faltante",
  SURPLUS: "Sobrante",
  BALANCED: "Balanceada",
};

/**
 * "Faltante $ 500,00", "Sobrante $ 200,00" or "Balanceada": the sign of a difference in words; "—" without an
 * expected amount, when no difference exists yet (R9-1).
 */
export function differenceText(difference: string | null) {
  if (difference === null) return "—";
  if (/^-?0*(\.0*)?$/.test(difference)) return DIFFERENCE_LABELS.BALANCED;
  return difference.startsWith("-")
    ? `${DIFFERENCE_LABELS.SHORTAGE} ${formatMoney(difference.slice(1))}`
    : `${DIFFERENCE_LABELS.SURPLUS} ${formatMoney(difference)}`;
}

export function differenceTone(difference: string | null): Tone {
  if (difference === null) return "neutral";
  if (/^-?0*(\.0*)?$/.test(difference)) return "success";
  return difference.startsWith("-") ? "danger" : "warning";
}

/** "Rendición n.º 2" */
export const settlementTitle = (sequence: number) => `Rendición n.º ${sequence}`;

/** The final count of a boca (R7-4, Enzo's words): only what stayed in it after the partial deliveries. */
export const COUNT_HELP =
  "Contá solamente el efectivo que quedó físicamente en la Boca después de las entregas parciales.";
export const COUNT_EXAMPLE =
  "Ejemplo: se cobraron $ 900.000 y ya se entregaron $ 700.000; quedan $ 200.000 en la Boca: el conteo final es $ 200.000.";

/** The board's mark of a settlement with a Store Sys update to decide (R9-1, P6). */
export const EXPECTED_UPDATE_BADGE = "Store Sys pendiente";
export const LEGACY_ORIGIN = "Manual · anterior a integración Store Sys";

/** "Store Sys · Ref. LIQ-4512 rev. 2", "Manual" or, loaded before the integration, its own words (R9-1). */
export function expectedOriginText(origin: ExpectedOrigin): string {
  if (origin.legacy) return LEGACY_ORIGIN;
  if (origin.source === "MANUAL") return "Manual";
  return `Store Sys · Ref. ${origin.external_reference} rev. ${origin.revision}`;
}

export const EXPECTED_OUTCOME_LABELS: Record<ExpectedOutcome, string> = {
  MANUAL: "Carga manual",
  APPLIED: "Aplicado",
  MATCHED: "Coincide con el cargado",
  PENDING_CONFIRMATION: "Pendiente de confirmación",
  CONFIRMED: "Aceptado",
  KEPT: "Se mantuvo el cargado",
  REJECTED_CLOSED: "Llegó con la rendición cerrada",
  SUPERSEDED: "Reemplazado por una revisión posterior",
};

export const EXPECTED_OUTCOME_TONES: Record<ExpectedOutcome, Tone> = {
  MANUAL: "neutral",
  APPLIED: "success",
  MATCHED: "success",
  PENDING_CONFIRMATION: "warning",
  CONFIRMED: "success",
  KEPT: "neutral",
  REJECTED_CLOSED: "danger",
  SUPERSEDED: "neutral",
};
