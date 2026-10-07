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
  CHEQUE_OPENING_BALANCE: "Saldo inicial de cheque",
  CHEQUE_OWNER_CONTRIBUTION: "Aporte de socio con cheque",
  REVERSAL: "Reverso",
};

/** Operations that belong to a document (a settlement, a pending collection): corrected from it, never here. */
export const DOCUMENT_OPERATION_TYPES: readonly OperationType[] = [
  "SETTLEMENT_CLOSE",
  "PENDING_COLLECTION",
  "PENDING_WRITE_OFF",
  "CHEQUE_OPENING_BALANCE",
  "CHEQUE_OWNER_CONTRIBUTION",
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
  CHEQUE_PORTFOLIO: "Valores: cartera de cheques",
  CHEQUES_IN_TRANSIT: "Valores: cheques depositados",
};

/** Accounts that hold money or partner contributions. Technical counterparts never show as available money. */
export const OPERATIONAL_KINDS: readonly AccountKind[] = ["CASH_BOX", "BANK", "OWNER_EQUITY"];

const SOURCE_LABELS: Record<string, string> = {
  settlement: "Rendición",
  pending_collection: "Pendiente de cobro",
  cheque: "Cheque",
};

/** Where an operation came from: entered by hand on this screen, or a document such as a settlement. */
export function sourceLabel(sourceType: string | null, sourceId: number | null) {
  if (!sourceType) return "Carga manual";
  const label = SOURCE_LABELS[sourceType];
  if (label) return sourceId === null ? label : `${label} #${sourceId}`;
  return sourceId === null ? sourceType : `${sourceType} #${sourceId}`;
}

/** Screen of the document an operation came from, when it has one (a pending collection: R3). */
export function sourceHref(sourceType: string | null, sourceId: number | null): string | null {
  if (sourceType === "pending_collection" && sourceId !== null) return `/rendiciones/pendientes/${sourceId}`;
  return null;
}
