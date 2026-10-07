import { ApiError } from "@/lib/api/client";
import { formatMoney } from "@/lib/format";
import { cashErrorView } from "@/modules/settlements/cash/errors";

/** What the user reads for each error of a pending collection. Never codes or internal text. */
export interface PendingErrorView {
  message: string;
  /** The request may or may not have been recorded: retrying the same request is safe. */
  uncertain?: boolean;
  /** The request id was already used for different data: only a new action can continue. */
  conflict?: boolean;
}

type AccountName = (id: number) => string | undefined;

function detail(error: ApiError, key: string): string | undefined {
  const details = error.details;
  if (typeof details !== "object" || details === null) return undefined;
  const value = (details as Record<string, unknown>)[key];
  return typeof value === "string" ? value : undefined;
}

const UNCERTAIN =
  "No se pudo confirmar si la operación se registró. Volvé a intentar: si ya se había registrado, no se va a duplicar.";

/**
 * Messages of the pending collection codes, also used by the settlement screen (add, correct and void a
 * pending in its settlement). `null` for any other code.
 */
export function pendingMessage(error: ApiError, reversing = false, accountName?: AccountName): string | null {
  switch (error.code) {
    case "pending_not_in_effect":
      return "El pendiente no está vigente en Cajas: todavía no se cerró su rendición o fue anulado. No se puede cobrar ni resolver.";
    case "pending_amount_exceeds_balance": {
      const balance = detail(error, "balance");
      return balance
        ? `El importe supera el saldo pendiente (${formatMoney(balance)}). Actualizamos el pendiente para que veas el saldo actual.`
        : "El importe supera el saldo pendiente.";
    }
    case "invalid_destination":
      return "El efectivo entra en una caja activa y la transferencia en un banco activo. Elegí otra cuenta de destino.";
    case "invalid_receipt_date":
      return "La fecha no puede ser anterior a la fecha de la rendición del pendiente.";
    case "future_business_date":
      return "La fecha de negocio no puede ser posterior a hoy.";
    case "receipt_already_reversed":
      return "Este cobro ya fue revertido.";
    case "resolution_already_reversed":
      return "Esta resolución ya fue revertida.";
    case "resolution_allocation_mismatch":
      return "Las partes tienen que sumar exactamente el importe a resolver, y cada empleado o la empresa puede aparecer una sola vez.";
    case "invalid_allocation":
      return "Cada parte es de la empresa o de un empleado elegido.";
    case "pending_has_activity":
      return "El pendiente ya tiene cobros o resoluciones: para quitarlo, primero hay que revertirlos desde el pendiente.";
    case "pending_below_applied": {
      const applied = detail(error, "applied");
      return applied
        ? `El pendiente ya tiene ${formatMoney(applied)} cobrados o resueltos: su importe no puede quedar por debajo.`
        : "El importe no puede quedar por debajo de lo ya cobrado o resuelto.";
    }
    case "pending_customer_locked":
      return "El pendiente ya tiene cobros o resoluciones de ese cliente: el cliente no se puede cambiar.";
    case "pending_voided":
    case "pending_already_voided":
      return "El pendiente ya está anulado.";
    case "invalid_expected_date":
      return "La fecha esperada de cobro no puede ser anterior a la fecha de la rendición.";
    case "employee_not_found":
      return "El empleado elegido no existe o no está activo.";
    case "invalid_alert_hours":
      return "Ingresá una cantidad de horas dentro del rango permitido.";
    case "insufficient_funds":
      return reversing
        ? "El reverso no se puede realizar: dejaría inconsistente el saldo histórico de una cuenta, porque ese dinero ya se usó en movimientos posteriores. Revisá los movimientos de Cajas; no se registró ningún ajuste."
        : cashErrorView(error, accountName).message;
    default:
      return null;
  }
}

export function pendingErrorView(error: unknown, reversing = false, accountName?: AccountName): PendingErrorView {
  if (!(error instanceof ApiError) || error.status === 0 || error.status >= 500) {
    return { message: UNCERTAIN, uncertain: true };
  }
  if (error.code === "idempotency_conflict") {
    return {
      message:
        "Esta operación ya se había registrado con otros datos. Revisá el pendiente; si querés registrar algo distinto, empezá de nuevo.",
      conflict: true,
    };
  }
  const known = pendingMessage(error, reversing, accountName);
  if (known) return { message: known };
  switch (error.code) {
    case "forbidden":
      return { message: "No tenés permiso para realizar esta operación." };
    case "validation_error":
      return { message: "Revisá los datos ingresados." };
    case "reason_required":
      return { message: "Indicá el motivo." };
    default:
      // Business errors of this API carry a message written for the user (backend `AppError`).
      return { message: error.message || "No se pudo completar la operación." };
  }
}
