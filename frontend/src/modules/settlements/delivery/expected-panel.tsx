"use client";

import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime, formatMoney } from "@/lib/format";
import { cashQueryKey } from "@/modules/settlements/cash/api";

import {
  deliveryApi,
  deliveryQueryKey,
  outletSettlementQueryKey,
  type ExpectedHistory,
  type ExpectedPending,
  type Settlement,
} from "./api";
import { deliveryErrorView } from "./errors";
import { ActionDialog, TextField } from "./form";
import { EXPECTED_OUTCOME_LABELS, EXPECTED_OUTCOME_TONES, expectedOriginText } from "./labels";
import { useWriteAction } from "./use-delivery-action";

export const PENDING_TITLE = "Store Sys informó otro importe esperado";
export const PENDING_ADMIN_NOTE =
  "La rendición está en corrección: solo un administrador de Rendiciones puede decidir esta actualización.";

/** «Origen: Store Sys · Ref. LIQ-4512 rev. 2», under the expected amount; nothing while it is not loaded. */
export function ExpectedOriginNote({ settlement }: { settlement: Settlement }) {
  const origin = settlement.expected_origin;
  if (!origin) return null;
  return (
    <span className="text-muted-foreground" data-testid="expected-origin">
      Origen: {expectedOriginText(origin)}
    </span>
  );
}

const amountText = (value: string | null) => (value === null ? "sin cargar" : formatMoney(value));

/**
 * A Store Sys update waiting for a decision (R9-1): the expected amount does not change, and the settlement does
 * not close, until someone accepts it or keeps the amount loaded with a reason.
 */
export function ExpectedPendingBanner({
  settlement,
  canOperate,
  isAdmin,
}: {
  settlement: Settlement;
  canOperate: boolean;
  isAdmin: boolean;
}) {
  const [deciding, setDeciding] = useState<"accept" | "keep" | null>(null);
  const pending = settlement.expected_pending;
  if (!pending) return null;
  const allowed = pending.requires_admin ? isAdmin : canOperate;
  return (
    <div
      role="status"
      className="flex flex-col gap-3 rounded-md border border-warning/40 bg-warning-soft px-4 py-3 text-sm sm:flex-row sm:items-start"
      data-testid="expected-pending"
    >
      <RefreshCw className="mt-0.5 hidden size-4 shrink-0 text-warning sm:block" aria-hidden />
      <div className="flex-1 space-y-1">
        <p className="font-medium">{PENDING_TITLE}</p>
        <p>
          Cargado: <span className="tabular-nums">{amountText(pending.current_amount)}</span> · Store Sys:{" "}
          <span className="font-medium tabular-nums">{formatMoney(pending.expected_amount)}</span> (Ref.{" "}
          {pending.external_reference} rev. {pending.revision}, recibido el {formatDateTime(pending.received_at)}).
        </p>
        <p className="text-muted-foreground">Hasta que lo decidas, la rendición no se puede cerrar.</p>
        {pending.requires_admin && <p className="text-muted-foreground">{PENDING_ADMIN_NOTE}</p>}
      </div>
      {allowed && (
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => setDeciding("accept")}>Aceptar el de Store Sys</Button>
          <Button variant="secondary" onClick={() => setDeciding("keep")}>
            Mantener el cargado
          </Button>
        </div>
      )}
      {deciding && <DecisionDialog pending={pending} to={deciding} onClose={() => setDeciding(null)} />}
    </div>
  );
}

function DecisionDialog({
  pending,
  to,
  onClose,
}: {
  pending: ExpectedPending;
  to: "accept" | "keep";
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const action = useWriteAction<ExpectedHistory>({
    slot: `expected:${pending.event_id}:${to}`,
    write: (request) =>
      to === "accept"
        ? deliveryApi.acceptExpected(pending.event_id)
        : deliveryApi.keepExpected(pending.event_id, String(request.body.reason)),
    invalidate: [deliveryQueryKey, outletSettlementQueryKey, cashQueryKey],
    isUncertain: (error) => Boolean(deliveryErrorView(error).uncertain),
    onDone: onClose,
  });
  const accept = to === "accept";
  return (
    <ActionDialog
      title={accept ? "Aceptar el esperado de Store Sys" : "Mantener el esperado cargado"}
      description={
        accept
          ? `El esperado pasa a ${formatMoney(pending.expected_amount)}. Queda registrado quién lo aceptó y cuándo.`
          : `El esperado sigue en ${amountText(pending.current_amount)}. Indicá por qué no se toma el de Store Sys.`
      }
      submitLabel={accept ? "Aceptar" : "Mantener"}
      action={action}
      onClose={onClose}
      validate={() => (!accept && !reason.trim() ? "Indicá el motivo." : null)}
      build={() => ({
        method: "POST",
        path: `/expected-updates/${pending.event_id}/${to}`,
        body: accept ? {} : { reason: reason.trim() },
        summary: accept ? "Aceptación del esperado de Store Sys" : "Se mantiene el esperado cargado",
      })}
    >
      {accept ? (
        <p className="text-sm">
          Ref. {pending.external_reference} rev. {pending.revision}: {formatMoney(pending.expected_amount)}.
        </p>
      ) : (
        <TextField label="Motivo" value={reason} onChange={setReason} multiline />
      )}
    </ActionDialog>
  );
}

/** Every change of the expected amount, manual or from Store Sys, with who decided what (R9-1). */
export function ExpectedHistorySection({ settlement }: { settlement: Settlement }) {
  const history = useQuery({
    queryKey: [...deliveryQueryKey, "expected-history", settlement.id, settlement.version, settlement.expected_pending],
    queryFn: () => deliveryApi.expectedHistory(settlement.id),
  });
  if (history.isPending) return <Skeleton className="h-20 w-full" aria-label="Cargando importe esperado" />;
  if (history.isError) return <ErrorState error={history.error} onRetry={() => void history.refetch()} />;
  const { events, origin } = history.data;
  return (
    <section aria-label="Historia del importe esperado" className="space-y-2">
      <h3 className="text-sm font-semibold">Importe esperado</h3>
      {events.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="expected-history-empty">
          {origin?.legacy ? expectedOriginText(origin) : "Todavía no se cargó."}
        </p>
      ) : (
        <ol className="space-y-2" data-testid="expected-history">
          {events.map((event) => (
            <li key={event.id} className="rounded-md border border-border bg-surface px-4 py-2 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium tabular-nums">
                  {amountText(event.previous_amount)} → {amountText(event.expected_amount)}
                </span>
                <Badge tone={EXPECTED_OUTCOME_TONES[event.outcome]}>{EXPECTED_OUTCOME_LABELS[event.outcome]}</Badge>
                {event.in_effect && <Badge tone="info">Vigente</Badge>}
                <span className="ml-auto text-xs text-muted-foreground tabular-nums">
                  {formatDateTime(event.received_at)}
                  {event.received_by_name && ` · ${event.received_by_name}`}
                </span>
              </div>
              <p className="text-xs text-muted-foreground">
                {event.source === "STORE_SYS"
                  ? `Store Sys · Ref. ${event.external_reference} rev. ${event.revision}`
                  : "Manual"}
                {event.reason && ` · Motivo: ${event.reason}`}
              </p>
              {event.decided_at && (
                <p className="text-xs text-muted-foreground">
                  {EXPECTED_OUTCOME_LABELS[event.outcome]} por {event.decided_by_name ?? "—"} el{" "}
                  {formatDateTime(event.decided_at)}
                  {event.decision_reason && `. Motivo: ${event.decision_reason}`}
                </p>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
