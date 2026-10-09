import { ApiError } from "@/lib/api/client";
import { cashErrorView } from "@/modules/settlements/cash/errors";
import { deliveryErrorView, type DeliveryErrorView } from "@/modules/settlements/delivery/errors";

/**
 * What the user reads for each error of the payments API. Business errors carry a message written for the
 * user by the backend; insufficient funds names the account and, for a past date, the day that would go
 * negative.
 */
export function paymentErrorView(
  error: unknown,
  accountName?: (id: number) => string | undefined,
  businessDate?: string,
): DeliveryErrorView {
  if (error instanceof ApiError) {
    if (error.code === "insufficient_funds")
      return { message: cashErrorView(error, accountName, businessDate).message };
    if (error.code === "idempotency_conflict") {
      return {
        message:
          "Este pago ya se había registrado con otros datos. Revisá el listado de pagos; si querés cargar uno distinto, empezá de nuevo.",
        conflict: true,
      };
    }
  }
  return deliveryErrorView(error, accountName);
}
