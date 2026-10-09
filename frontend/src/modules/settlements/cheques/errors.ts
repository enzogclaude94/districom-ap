import { ApiError } from "@/lib/api/client";
import { cashErrorView } from "@/modules/settlements/cash/errors";
import { pendingMessage } from "@/modules/settlements/pending/errors";

/** What the user reads for each error of the cheque screens. Never codes or internal text. */
export interface ChequeErrorView {
  message: string;
  /** The request may or may not have been recorded: retrying the same request is safe. */
  uncertain?: boolean;
  conflict?: boolean;
  /** Another cheque of the same bank and number may be this one: the user confirms, with a reason. */
  possibleDuplicate?: boolean;
}

const UNCERTAIN =
  "No se pudo confirmar si la operación se registró. Volvé a intentar: si ya se había registrado, no se va a duplicar.";

const MESSAGES: Record<string, string> = {
  cheque_presentation_expired:
    "El cheque está vencido para su presentación: no se puede depositar. Puede devolverse a quien lo entregó.",
  cheque_has_activity:
    "El cheque tuvo movimientos posteriores (por ejemplo, ya se depositó, acreditó o rechazó). Para corregir este documento, primero hay que revertir los posteriores, en orden inverso. No se registró ningún ajuste.",
  pending_has_activity:
    "El pendiente que creó este documento ya tiene cobros o resoluciones. Para revertirlo, primero hay que revertir esos cobros o resoluciones desde el pendiente. No se registró ningún ajuste.",
  cheque_not_in_portfolio: "El cheque ya no está en cartera. Actualizamos los datos para que veas dónde está.",
  cheque_not_deposited: "El cheque no está depositado. Actualizamos los datos para que veas dónde está.",
  cheque_not_rejectable: "El cheque no está depositado ni acreditado: no se puede registrar un rechazo.",
  bank_account_mismatch: "Un cheque se acredita en el mismo banco donde se depositó.",
  responsible_required: "Elegí quién es responsable de cobrar el nuevo pendiente.",
};

export function chequeErrorView(error: unknown, accountName?: (id: number) => string | undefined): ChequeErrorView {
  if (!(error instanceof ApiError) || error.status === 0 || error.status >= 500) {
    return { message: UNCERTAIN, uncertain: true };
  }
  if (error.code === "idempotency_conflict") {
    return {
      message:
        "Esta operación ya se había registrado con otros datos. Revisá el cheque; si querés registrar algo distinto, empezá de nuevo.",
      conflict: true,
    };
  }
  if (error.code === "cheque_possible_duplicate") {
    return { message: error.message, possibleDuplicate: true };
  }
  const known = MESSAGES[error.code];
  if (known) return { message: known };
  if (error.code === "insufficient_funds") return { message: cashErrorView(error, accountName).message };
  const pending = pendingMessage(error);
  if (pending) return { message: pending };
  switch (error.code) {
    case "forbidden":
      return { message: error.message || "No tenés permiso para realizar esta operación." };
    case "validation_error":
      return { message: "Revisá los datos ingresados." };
    default:
      // Business errors of this API carry a message written for the user (backend `AppError`).
      return { message: error.message || "No se pudo completar la operación." };
  }
}
