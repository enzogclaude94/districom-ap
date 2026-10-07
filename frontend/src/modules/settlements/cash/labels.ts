import type { AccountKind, OperationType } from "@/modules/settlements/cash/api";

export const OPERATION_LABELS: Record<OperationType, string> = {
  OPENING_BALANCE: "Saldo inicial",
  CASH_TRANSFER: "Traspaso entre cajas",
  BANK_DEPOSIT: "Depósito en banco",
  BANK_WITHDRAWAL: "Extracción del banco",
  OWNER_CONTRIBUTION: "Aporte de socio",
  OWNER_WITHDRAWAL: "Devolución a socio",
  ADJUSTMENT: "Ajuste administrativo",
  SETTLEMENT_CLOSE: "Cierre de rendición",
  PENDING_COLLECTION: "Cobro de pendiente",
  PENDING_WRITE_OFF: "Resolución de pendiente",
  REVERSAL: "Reverso",
};

/** Operations that belong to a document (a settlement, a pending collection): corrected from it, never here. */
export const DOCUMENT_OPERATION_TYPES: readonly OperationType[] = [
  "SETTLEMENT_CLOSE",
  "PENDING_COLLECTION",
  "PENDING_WRITE_OFF",
];

export const ACCOUNT_KIND_LABELS: Record<AccountKind, string> = {
  CASH_BOX: "Caja",
  BANK: "Banco",
  OWNER_EQUITY: "Aportes de socio",
  OPENING_BALANCE: "Cuenta técnica",
  ADJUSTMENT: "Cuenta técnica",
  DELIVERY_COLLECTIONS: "Cuenta técnica",
  EXPENSE: "Gastos",
  SETTLEMENT_DIFFERENCE: "Cuenta técnica",
  EMPLOYEE: "Cuenta interna de empleado",
  PENDING_COLLECTIONS: "Pendientes de cobro",
  UNCOLLECTIBLE: "Incobrables",
};

/** Accounts that hold money or partner contributions. Technical counterparts never show as available money. */
export const OPERATIONAL_KINDS: readonly AccountKind[] = ["CASH_BOX", "BANK", "OWNER_EQUITY"];

const SOURCE_LABELS: Record<string, string> = { settlement: "Rendición", pending_collection: "Pendiente de cobro" };

/** Where an operation came from: entered by hand on this screen, or a document such as a settlement. */
export function sourceLabel(sourceType: string | null, sourceId: number | null) {
  if (!sourceType) return "Carga manual";
  const label = SOURCE_LABELS[sourceType];
  if (label) return sourceId === null ? label : `${label} #${sourceId}`;
  return sourceId === null ? sourceType : `${sourceType} #${sourceId}`;
}
