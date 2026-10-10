import type { PaymentConcept, PaymentMethod, PaymentState } from "./api";

export const CONCEPT_LABELS: Record<PaymentConcept, string> = {
  MERCHANDISE: "Pago por mercadería",
  EXPENSE: "Pago de gasto",
  REFUND: "Devolución",
  OTHER: "Otro pago",
};

/** Concepts an operator records; OTHER is for an admin of Rendiciones only (the backend checks it again). */
export const OPERATOR_CONCEPTS: PaymentConcept[] = ["MERCHANDISE", "EXPENSE", "REFUND"];

export const METHOD_LABELS: Record<PaymentMethod, string> = {
  CASH: "Efectivo",
  TRANSFER: "Transferencia",
  CHEQUE: "Cheque",
};

/** Means enabled in R6-1. Cheques arrive with R6-2. */
export const ENABLED_METHODS: PaymentMethod[] = ["CASH", "TRANSFER"];

export const STATE_LABELS: Record<PaymentState, string> = {
  IN_EFFECT: "Vigente",
  REVERSED: "Revertido",
};

export const STATE_TONES: Record<PaymentState, "neutral" | "warning"> = {
  IN_EFFECT: "neutral",
  REVERSED: "warning",
};

const EVENT_LABELS: Record<string, string> = {
  created: "Pago registrado",
  reversed: "Pago revertido",
  corrected: "Corrección descriptiva",
};

export const eventLabel = (type: string) => EVENT_LABELS[type] ?? type;
