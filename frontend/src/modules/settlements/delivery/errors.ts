import { ApiError } from "@/lib/api/client";
import { cashErrorView } from "@/modules/settlements/cash/errors";

/** What the user reads for each error of the delivery settlements API. Never codes or internal text. */
export interface DeliveryErrorView {
  message: string;
  /** The request may or may not have been recorded: retrying the same request is safe. */
  uncertain?: boolean;
  /** The request id was already used for different data: only a new action can continue. */
  conflict?: boolean;
  /** The settlement changed after the preview the user reviewed: a new preview is needed. */
  stale?: boolean;
}

export const STALE_MESSAGE =
  "La rendición cambió desde que la revisaste. Actualizamos los datos para que vuelvas a confirmarla.";

export function deliveryErrorView(error: unknown, accountName?: (id: number) => string | undefined): DeliveryErrorView {
  if (!(error instanceof ApiError) || error.status === 0 || error.status >= 500) {
    return {
      message:
        "No se pudo confirmar si la operación se registró. Volvé a intentar: si ya se había registrado, no se va a duplicar.",
      uncertain: true,
    };
  }
  switch (error.code) {
    case "settlement_preview_stale":
    case "settlement_concurrent_change":
      return { message: STALE_MESSAGE, stale: true };
    case "idempotency_conflict":
      return {
        message:
          "Esta carga ya se había registrado con otros datos. Revisá la rendición; si querés cargar algo distinto, empezá de nuevo.",
        conflict: true,
      };
    case "insufficient_funds":
    case "operation_owned_by_document":
      return { message: cashErrorView(error, accountName).message };
    case "forbidden":
      return { message: error.message || "No tenés permiso para realizar esta operación." };
    case "validation_error":
      return { message: "Revisá los datos ingresados." };
    default:
      // Business errors of this API carry a message written for the user (backend `AppError`).
      return { message: error.message || "No se pudo completar la operación." };
  }
}
