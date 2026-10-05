import { ApiError } from "@/lib/api/client";
import { formatDate, formatMoney } from "@/lib/format";

/** What the user reads for each error of the Cajas API. Never codes, SQLSTATE or internal text. */
export interface CashErrorView {
  message: string;
  /** The request may or may not have been recorded: retrying with the same request id is safe. */
  uncertain?: boolean;
  /** The request id was already used for different data: only a new operation can continue. */
  conflict?: boolean;
  /** Existing account for `account_name_taken`. */
  existingAccountId?: number;
}

type AccountName = (id: number) => string | undefined;

function detail<T>(error: ApiError, key: string): T | undefined {
  const details = error.details;
  if (typeof details !== "object" || details === null || !(key in details)) return undefined;
  return (details as Record<string, unknown>)[key] as T;
}

function insufficientFunds(error: ApiError, accountName: AccountName, businessDate?: string): string {
  const accountId = detail<number>(error, "account_id");
  const name = (accountId !== undefined && accountName(accountId)) || "la cuenta";
  const base = `No hay saldo suficiente en ${name} para realizar este movimiento.`;
  const on = detail<string>(error, "business_date");
  const balance = detail<string>(error, "balance");
  if (!on || balance === undefined) return base;
  if (businessDate && on !== businessDate) {
    return `${base} Por los movimientos posteriores a la fecha elegida, el ${formatDate(on)} quedaría en negativo (saldo de ese día: ${formatMoney(balance)}).`;
  }
  return `${base} Saldo disponible al ${formatDate(on)}: ${formatMoney(balance)}.`;
}

export function cashErrorView(
  error: unknown,
  accountName: AccountName = () => undefined,
  businessDate?: string,
): CashErrorView {
  if (!(error instanceof ApiError)) return { message: "No se pudo completar la operación.", uncertain: true };
  if (error.status === 0 || error.status >= 500) {
    return {
      message:
        "No se pudo confirmar si la operación se registró. Volvé a intentar: si ya se había registrado, no se va a duplicar.",
      uncertain: true,
    };
  }
  switch (error.code) {
    case "insufficient_funds":
      return { message: insufficientFunds(error, accountName, businessDate) };
    case "idempotency_conflict":
      return {
        message:
          "Esta operación ya se había registrado con otros datos. Revisá los movimientos; si querés cargar una operación distinta, empezá una nueva.",
        conflict: true,
      };
    case "operation_already_reversed":
      return { message: "Esta operación ya fue revertida." };
    case "operation_owned_by_document":
      return { message: "Esta operación es el cierre de una rendición: se corrige reabriendo la rendición." };
    case "cannot_reverse_reversal":
      return { message: "Un reverso no se puede revertir. Si hace falta, registrá una operación nueva." };
    case "account_inactive":
      return { message: "Una de las cuentas está dada de baja y no admite movimientos." };
    case "account_name_taken": {
      const existing = detail<number>(error, "account_id");
      const active = detail<boolean>(error, "active");
      const name = existing !== undefined ? accountName(existing) : undefined;
      const which = name ? `«${name}»` : "con un nombre equivalente";
      return {
        message:
          active === false
            ? `Ya existe la cuenta ${which}, dada de baja. No se crea otra con un nombre parecido.`
            : `Ya existe la cuenta ${which}. No se crea otra con un nombre parecido.`,
        existingAccountId: existing,
      };
    }
    case "invalid_legs":
      return { message: "Las cuentas elegidas no corresponden a esta operación." };
    case "future_business_date":
      return { message: "La fecha de negocio no puede ser posterior a hoy." };
    case "reason_required":
      return { message: "Indicá el motivo." };
    case "invalid_amount":
      return { message: "El importe no es válido." };
    case "invalid_account_name":
      return { message: "Ingresá un nombre." };
    case "account_not_found":
      return { message: "La cuenta ya no existe. Actualizá la pantalla." };
    case "operation_not_found":
      return { message: "La operación no existe." };
    case "forbidden":
      return { message: "No tenés permiso para realizar esta operación." };
    case "validation_error":
      return { message: "Revisá los datos ingresados." };
    case "invalid_ledger_operation":
      return { message: "La operación no cumple las reglas de cajas y no se registró." };
    default:
      return { message: "No se pudo completar la operación." };
  }
}
