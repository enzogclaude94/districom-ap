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
