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

/** Every mean: cash from a cash box, a transfer from a bank, a cheque of the portfolio (R6-2). */
export const ENABLED_METHODS: PaymentMethod[] = ["CASH", "TRANSFER", "CHEQUE"];

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

/** Concepts as the third party's ficha groups them (R6-3). */
export const BREAKDOWN_CONCEPT_LABELS: Record<PaymentConcept, string> = {
  MERCHANDISE: "Mercadería",
  EXPENSE: "Gastos/servicios",
  REFUND: "Devoluciones",
  OTHER: "Otros",
};

export const BREAKDOWN_METHOD_LABELS: Record<PaymentMethod, string> = {
  CASH: "Efectivo",
  TRANSFER: "Transferencia",
  CHEQUE: "Cheques",
};

/** Said wherever totals of a third party are shown: they are payments made, never a debt. */
export const NOT_A_DEBT =
  "Estos importes muestran pagos registrados. DistriCom no está calculando deuda pendiente con este tercero.";
