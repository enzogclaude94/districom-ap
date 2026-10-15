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
  CHEQUE_DEPOSIT: "Depósito de cheques",
  CHEQUE_CREDIT: "Acreditación de cheques",
  CHEQUE_REJECTION: "Cheque rechazado",
  CHEQUE_RETURN: "Devolución de cheque",
  DIRECT_EXPENSE: "Gasto directo",
  PAYMENT: "Pago a tercero",
  OUTLET_REMITTANCE: "Entrega parcial de boca",
  REVERSAL: "Reverso",
  EMPLOYEE_RESERVED: "Movimiento de empleado (reservado)",
};

/** Operations that belong to a document (a settlement, a pending, a cheque, an expense, a payment): corrected from it, never here. */
export const DOCUMENT_OPERATION_TYPES: readonly OperationType[] = [
  "SETTLEMENT_CLOSE",
  "PENDING_COLLECTION",
  "PENDING_WRITE_OFF",
  "CHEQUE_OPENING_BALANCE",
  "CHEQUE_OWNER_CONTRIBUTION",
  "CHEQUE_DEPOSIT",
  "CHEQUE_CREDIT",
  "CHEQUE_REJECTION",
  "CHEQUE_RETURN",
  "DIRECT_EXPENSE",
  "PAYMENT",
  "OUTLET_REMITTANCE",
  // Reserved (ADR 010): whoever sees it this way has no area to correct it from.
  "EMPLOYEE_RESERVED",
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
  MERCHANDISE_PAYMENTS: "Pagos realizados",
  THIRD_PARTY_REFUNDS: "Pagos realizados",
  OTHER_PAYMENTS: "Pagos realizados",
  OUTLET_COLLECTIONS: "Cuenta técnica",
  OUTLET_ADVANCES: "Cuenta técnica: entregas a cuenta",
};

/** Accounts that hold money or partner contributions. Technical counterparts never show as available money. */
export const OPERATIONAL_KINDS: readonly AccountKind[] = ["CASH_BOX", "BANK", "OWNER_EQUITY"];

const SOURCE_LABELS: Record<string, string> = {
  settlement: "Rendición",
  pending_collection: "Pendiente de cobro",
  cheque: "Cheque",
  cheque_deposit: "Depósito de cheques",
  cheque_credit: "Acreditación de cheques",
  cheque_rejection: "Rechazo de cheque",
  cheque_return: "Devolución de cheque",
  expense: "Gasto directo",
  payment: "Pago",
  outlet_remittance: "Entrega parcial de boca",
};

/** Where an operation came from: entered by hand on this screen, or a document such as a settlement. */
export function sourceLabel(sourceType: string | null, sourceId: number | null) {
  if (!sourceType) return "Carga manual";
  const label = SOURCE_LABELS[sourceType];
  if (label) return sourceId === null ? label : `${label} #${sourceId}`;
  return sourceId === null ? sourceType : `${sourceType} #${sourceId}`;
}

/** Screen of the document an operation came from, when it has one (a pending collection, a cheque document). */
const DOCUMENT_SCREENS: Record<string, string> = {
  pending_collection: "/rendiciones/pendientes",
  cheque: "/rendiciones/cheques",
  cheque_deposit: "/rendiciones/cheques/documentos/cheque-deposits",
  cheque_credit: "/rendiciones/cheques/documentos/cheque-credits",
  cheque_rejection: "/rendiciones/cheques/documentos/cheque-rejections",
  cheque_return: "/rendiciones/cheques/documentos/cheque-returns",
  expense: "/rendiciones/cajas/gastos",
  payment: "/rendiciones/pagos",
};

export function sourceHref(sourceType: string | null, sourceId: number | null): string | null {
  const screen = sourceType ? DOCUMENT_SCREENS[sourceType] : undefined;
  return screen && sourceId !== null ? `${screen}/${sourceId}` : null;
}
