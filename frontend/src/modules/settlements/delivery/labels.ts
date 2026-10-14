import type { BadgeProps } from "@/components/ui/badge";
import { formatMoney } from "@/lib/format";

import type { DifferenceKind, FinancialState, TransferStatus } from "./api";

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

/** "Faltante $ 500,00", "Sobrante $ 200,00" or "Balanceada": the sign of a difference in words. */
export function differenceText(difference: string) {
  if (/^-?0*(\.0*)?$/.test(difference)) return DIFFERENCE_LABELS.BALANCED;
  return difference.startsWith("-")
    ? `${DIFFERENCE_LABELS.SHORTAGE} ${formatMoney(difference.slice(1))}`
    : `${DIFFERENCE_LABELS.SURPLUS} ${formatMoney(difference)}`;
}

export function differenceTone(difference: string): Tone {
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
