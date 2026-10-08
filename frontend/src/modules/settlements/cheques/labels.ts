import type { BadgeProps } from "@/components/ui/badge";
import { formatDate } from "@/lib/format";

import type { Cheque, ChequeKind, ChequeOrigin, ChequeState, DocumentType, PaymentType } from "./api";

type Tone = NonNullable<BadgeProps["tone"]>;

export const KIND_LABELS: Record<ChequeKind, string> = { PHYSICAL: "Físico", ECHEQ: "ECHEQ" };
export const PAYMENT_TYPE_LABELS: Record<PaymentType, string> = { COMMON: "Común", DEFERRED: "Diferido" };

export const ORIGIN_LABELS: Record<ChequeOrigin, string> = {
  SETTLEMENT: "Rendición",
  OPENING_BALANCE: "Saldo inicial",
  OWNER_CONTRIBUTION: "Aporte de socio",
  PENDING_RECEIPT: "Cobro de pendiente",
};

/** The same names the backend gives (`state_label`), for the filter before any cheque is read. */
export const STATE_LABELS: Record<ChequeState, string> = {
  DECLARED: "Declarado",
  VERIFICATION_REJECTED: "Verificación rechazada",
  RECEIVED: "Recibido",
  IN_PORTFOLIO: "En cartera",
  DEPOSITED: "Depositado",
  CREDITED: "Acreditado",
  REJECTED: "Rechazado",
  RETURNED: "Devuelto",
  VOIDED: "Anulado",
};

export const STATE_TONES: Record<ChequeState, Tone> = {
  DECLARED: "warning",
  VERIFICATION_REJECTED: "danger",
  RECEIVED: "info",
  IN_PORTFOLIO: "info",
  DEPOSITED: "info",
  CREDITED: "success",
  REJECTED: "danger",
  RETURNED: "warning",
  VOIDED: "neutral",
};

export const DOCUMENT_LABELS: Record<DocumentType, string> = {
  DEPOSIT: "Depósito",
  CREDIT: "Acreditación",
  REJECTION: "Rechazo",
  RETURN: "Devolución",
  PENDING_RECEIPT: "Cobro de pendiente",
};

export const REVERSED_LABELS: Record<Exclude<DocumentType, "PENDING_RECEIPT">, string> = {
  DEPOSIT: "Depósito revertido",
  CREDIT: "Acreditación revertida",
  REJECTION: "Rechazo revertido",
  RETURN: "Devolución revertida",
};

export const EVENT_LABELS: Record<string, string> = {
  RECEIVED: "Recibido",
  CORRECTED: "Corregido",
  VERIFIED: "ECHEQ verificado",
  VERIFICATION_REJECTED: "Verificación rechazada",
  VOIDED: "Anulado",
  PUBLISHED: "Entró a cartera",
  WITHDRAWN: "Salió de cartera por una corrección",
  DESCRIPTIVE_CORRECTED: "Datos descriptivos corregidos",
  DEPOSITED: "Depositado",
  DEPOSIT_REVERSED: "Depósito revertido",
  CREDITED: "Acreditado",
  CREDIT_REVERSED: "Acreditación revertida",
  REJECTED: "Rechazado por el banco",
  REJECTION_REVERSED: "Rechazo revertido",
  RETURNED: "Devuelto",
  RETURN_REVERSED: "Devolución revertida",
  COLLECTED: "Recibido como cobro de un pendiente",
  COLLECTION_REVERSED: "Cobro revertido",
};

/** "N.º 00012345 · Banco de la Nación Argentina". */
export function chequeTitle(cheque: Pick<Cheque, "cheque_number" | "bank_name">) {
  return `N.º ${cheque.cheque_number} · ${cheque.bank_name}`;
}

/**
 * Why a cheque of the portfolio cannot be deposited today, or null when it can: never before it is payable,
 * never after its last presentation day (the backend refuses both, whatever the role).
 */
export function depositBlocker(cheque: Cheque, today: string): string | null {
  if (cheque.state !== "IN_PORTFOLIO") return "No está en cartera.";
  if (cheque.presentation_expired)
    return `Vencido: se podía presentar hasta el ${formatDate(cheque.presentation_deadline)}.`;
  if (cheque.payable_from > today) return `Disponible para depósito desde el ${formatDate(cheque.payable_from)}.`;
  return null;
}

/** «Vence en N días» while it is near its deadline. */
export function expiringText(cheque: Cheque) {
  if (!cheque.expiring_soon || cheque.expires_in_days === null) return null;
  if (cheque.expires_in_days === 0) return "Vence hoy";
  return cheque.expires_in_days === 1 ? "Vence mañana" : `Vence en ${cheque.expires_in_days} días`;
}
