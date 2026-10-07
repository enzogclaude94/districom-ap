import type { BadgeProps } from "@/components/ui/badge";

import type { LoadChange, PendingState, ReceiptMethod } from "./api";

type Tone = NonNullable<BadgeProps["tone"]>;

/** Readable names of the derived financial state (Enzo's R3-3). */
export const STATE_LABELS: Record<PendingState, string> = {
  DRAFT: "En rendición abierta",
  OPEN: "Pendiente",
  PARTIAL: "Parcial",
  COLLECTED: "Cobrado",
  RESOLVED: "Resuelto",
  VOIDED: "Anulado",
};

export const STATE_TONES: Record<PendingState, Tone> = {
  DRAFT: "neutral",
  OPEN: "info",
  PARTIAL: "info",
  COLLECTED: "success",
  RESOLVED: "success",
  VOIDED: "neutral",
};

export const LOAD_CHANGE_LABELS: Record<LoadChange, string> = {
  ADDED: "Agregado en esta corrección",
  MODIFIED: "Modificado en esta corrección",
  MARKED_FOR_REMOVAL: "Marcado para quitar en esta corrección",
};

export const METHOD_LABELS: Record<ReceiptMethod, string> = {
  CASH: "Efectivo",
  TRANSFER: "Transferencia",
};

/** "5 h", "1 día", "3 días": how long ago the pending was registered. */
export function ageText(hours: number) {
  if (hours < 24) return `${hours} h`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "1 día" : `${days} días`;
}

/** "Pendiente de cobro #12": the same name Cajas uses for the source of its operations. */
export const pendingTitle = (id: number) => `Pendiente de cobro #${id}`;

export const pendingHref = (id: number) => `/rendiciones/pendientes/${id}`;

/** What each entry of `pending_collection_events` means for the user. */
export const EVENT_LABELS: Record<string, string> = {
  CREATED: "Pendiente registrado",
  AMOUNT_CHANGED: "Importe corregido",
  CUSTOMER_CHANGED: "Cliente corregido",
  RESPONSIBLE_CHANGED: "Cambio de responsable",
  REASON_CHANGED: "Motivo corregido",
  EXPECTED_DATE_CHANGED: "Fecha esperada corregida",
  VOIDED: "Anulado en la rendición",
  PUBLISHED: "Publicado en Cajas por el cierre",
  WITHDRAWN: "Quitado de Cajas por un nuevo cierre",
  RECEIPT_RECORDED: "Cobro registrado",
  RECEIPT_REVERSED: "Cobro revertido",
  RESOLVED: "Saldo resuelto",
  RESOLUTION_REVERSED: "Resolución revertida",
};
