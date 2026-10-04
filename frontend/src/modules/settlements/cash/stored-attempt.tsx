"use client";

import { AlertTriangle } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";

import { cashErrorView } from "./errors";
import type { useCashOperation } from "./use-cash-operation";

type CashOperation = ReturnType<typeof useCashOperation>;

/** Readable line for a stored attempt: "Mover entre cajas · $ 1.500,00 · Caja principal → Caja de cambio · 04/10/2026". */
export function summarize(title: string, body: Record<string, unknown>, names: (id: number) => string | undefined) {
  const accounts = Object.entries(body)
    .filter(([key, value]) => key.endsWith("account_id") && typeof value === "number")
    .map(([, value]) => names(value as number) ?? `cuenta n.º ${String(value)}`);
  const parts = [title];
  if (typeof body.amount === "string") parts.push(formatMoney(body.amount));
  if (accounts.length) parts.push(accounts.join(" → "));
  if (typeof body.business_date === "string") parts.push(formatDate(body.business_date));
  return parts.join(" · ");
}

/**
 * Shown instead of a form when an earlier attempt of the same operation has an unknown outcome: it can be
 * retried with its original id (never duplicated) or, after a clear warning, abandoned.
 */
export function StoredAttempt({
  operation,
  names,
  onClose,
}: {
  operation: CashOperation;
  names: (id: number) => string | undefined;
  onClose: () => void;
}) {
  const [confirmAbandon, setConfirmAbandon] = useState(false);
  const stored = operation.stored!;
  const pending = operation.mutation.isPending;
  const error = operation.mutation.error ? cashErrorView(operation.mutation.error, names) : null;

  return (
    <div className="space-y-4">
      <div className="flex gap-2 rounded-md border border-warning/40 bg-warning-soft px-3 py-2 text-sm">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
        <div className="space-y-1">
          <p className="font-medium">Hay una operación sin confirmar</p>
          <p>
            El {formatDateTime(stored.savedAt)} se intentó registrar <strong>{stored.summary}</strong>, pero no se pudo
            confirmar si quedó registrada. Reintentala: si ya se había registrado, no se duplica.
          </p>
        </div>
      </div>

      {error && (
        <div role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
          {error.message}
        </div>
      )}

      {confirmAbandon ? (
        <div className="space-y-3 rounded-md border border-danger/30 px-3 py-3 text-sm" role="alertdialog">
          <p className="font-medium">¿Descartar el intento?</p>
          <p>
            Si esa operación sí se registró y después cargás otra igual, el movimiento quedaría duplicado. Antes de
            descartar, revisá los movimientos de las cuentas involucradas.
          </p>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirmAbandon(false)}>
              Volver
            </Button>
            <Button variant="secondary" onClick={operation.abandonStored}>
              Descartar el intento
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cerrar
          </Button>
          <Button variant="secondary" disabled={pending} onClick={() => setConfirmAbandon(true)}>
            Descartar…
          </Button>
          <Button disabled={pending} onClick={operation.retryStored}>
            {pending ? "Reintentando…" : "Reintentar la misma operación"}
          </Button>
        </div>
      )}
    </div>
  );
}
