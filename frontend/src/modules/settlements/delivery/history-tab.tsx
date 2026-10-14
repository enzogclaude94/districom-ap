"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import type { ReactNode } from "react";

import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";

import { deliveryApi, deliveryQueryKey, type History, type Leg, type Settlement } from "./api";
import { DIFFERENCE_LABELS } from "./labels";

interface Event {
  at: string;
  /** Order of events recorded in the same instant: a reversal, then its new close, then its difference. */
  rank: number;
  key: string;
  title: string;
  by?: string;
  badges?: ReactNode;
  children?: ReactNode;
}

const operationLink = (id: number) => (
  <Link href={`/rendiciones/cajas/operaciones/${id}`} className="text-primary hover:underline">
    operación n.º {id}
  </Link>
);

function Legs({ legs }: { legs: Leg[] }) {
  return (
    <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
      {legs.map((leg, i) => (
        <li key={i} className="flex justify-between gap-4 sm:max-w-md">
          <span>{leg.account_name}</span>
          <span className="tabular-nums">{formatMoney(leg.amount, { signed: true })}</span>
        </li>
      ))}
    </ul>
  );
}

const expectedText = (value: string | null) => (value === null ? "sin cargar" : formatMoney(value));

/** True when `at` falls after a reopening and before the next close: the settlement was being corrected. */
function duringCorrection(at: string, history: History) {
  const time = Date.parse(at);
  const reopened = history.reopenings.map((r) => Date.parse(r.reopened_at)).filter((r) => r <= time);
  if (reopened.length === 0) return false;
  const since = Math.max(...reopened);
  return !history.closings.some((c) => {
    const closed = Date.parse(c.closed_at);
    return closed > since && closed <= time;
  });
}

/** What a boca adds to the history (R7-4): its expected amount, its remittances and their reversals. */
function outletEvents(settlement: Settlement, history: History): Event[] {
  const events: Event[] = [];
  const changes = history.expected_changes ?? [];
  changes.forEach((change, i) => {
    events.push({
      at: change.changed_at,
      rank: 0,
      key: `expected-${i}`,
      title:
        change.before === null ? "Esperado cargado" : change.after === null ? "Esperado borrado" : "Esperado corregido",
      by: change.changed_by_name ?? undefined,
      children: (
        <p>
          Esperado (Store Sys): {expectedText(change.before)} → {expectedText(change.after)}.
        </p>
      ),
    });
  });
  for (const remittance of settlement.remittances ?? []) {
    const correcting = duringCorrection(remittance.created_at, history);
    events.push({
      at: remittance.created_at,
      rank: 0,
      key: `remittance-${remittance.id}`,
      title: `Entrega parcial n.º ${remittance.id} registrada${correcting ? " durante la corrección" : ""}`,
      by: remittance.created_by_name,
      children: (
        <p>
          {formatMoney(remittance.amount)} a {remittance.destination_account_name}
          {remittance.delivered_by_name && `, entregó ${remittance.delivered_by_name}`}
          {remittance.reference && ` · ${remittance.reference}`}. Hora de la entrega{" "}
          {formatTime(remittance.delivered_at)}.
        </p>
      ),
    });
    if (remittance.reversed_at) {
      events.push({
        at: remittance.reversed_at,
        rank: 0,
        key: `remittance-reversal-${remittance.id}`,
        title: `Entrega parcial n.º ${remittance.id} revertida`,
        by: remittance.reversed_by_name ?? undefined,
        children: (
          <p>
            {formatMoney(remittance.amount)} salieron de {remittance.destination_account_name}.
            {remittance.reversal_reason && ` Motivo: ${remittance.reversal_reason}`}
          </p>
        ),
      });
    }
  }
  return events;
}

const formatTime = (iso: string) =>
  new Intl.DateTimeFormat("es-AR", {
    timeZone: "America/Argentina/Buenos_Aires",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(iso));

function ConsolidatedRemittances({ items }: { items: { remittance_id: number; amount: string }[] }) {
  return (
    <details className="mt-1" data-testid="consolidated-remittances">
      <summary className="cursor-pointer text-primary">Entregas consolidadas en este cierre ({items.length})</summary>
      <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
        {items.map((item) => (
          <li key={item.remittance_id} className="flex justify-between gap-4 sm:max-w-md">
            <span>Entrega parcial n.º {item.remittance_id}</span>
            <span className="tabular-nums">{formatMoney(item.amount)}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

/** Every event of the settlement, oldest first, built from its domain tables (never from the audit log). */
export function timeline(settlement: Settlement, history: History): Event[] {
  const outlet = settlement.type === "OUTLET";
  const initialExpected = history.expected_changes?.[0]?.before ?? settlement.expected_amount;
  const events: Event[] = [
    {
      at: settlement.created_at,
      rank: 0,
      key: "created",
      title: outlet ? "Rendición abierta" : "Rendición creada",
      by: settlement.created_by_name,
      children: (
        <p>
          Fecha de negocio {formatDate(settlement.business_date)}.
          {outlet && ` Esperado (Store Sys): ${expectedText(initialExpected ?? null)}.`}
        </p>
      ),
    },
    ...(outlet ? outletEvents(settlement, history) : []),
  ];
  for (const closing of history.closings) {
    if (closing.reversal_operation_id !== null) {
      events.push({
        at: closing.closed_at,
        rank: 1,
        key: `reversal-${closing.settlement_version}`,
        title: `Reverso del cierre de la versión ${closing.settlement_version - 1}`,
        by: closing.closed_by_name,
        children: (
          <p>
            Se revirtió en Cajas con la {operationLink(closing.reversal_operation_id)}, junto con el nuevo cierre y en
            la misma fecha de negocio.
          </p>
        ),
      });
    }
    events.push({
      at: closing.closed_at,
      rank: 2,
      key: `close-${closing.settlement_version}`,
      title:
        (closing.settlement_version === 1 ? "Cierre" : `Nuevo cierre (versión ${closing.settlement_version})`) +
        (closing.operation_id === null ? " en $0" : ""),
      by: closing.closed_by_name,
      badges: closing.in_effect ? (
        <Badge tone="success">Vigente</Badge>
      ) : (
        <Badge>{closing.operation_id === null ? "Reemplazado" : "Revertido"}</Badge>
      ),
      children:
        closing.operation_id === null ? (
          <p>Sin movimientos financieros: se cerró en $0, fecha de negocio {formatDate(closing.business_date)}.</p>
        ) : (
          <>
            <p>
              Publicado en Cajas con la {operationLink(closing.operation_id)}, fecha de negocio{" "}
              {formatDate(closing.business_date)}.
            </p>
            <Legs legs={closing.legs} />
            {outlet && (closing.remittances?.length ?? 0) > 0 && (
              <ConsolidatedRemittances items={closing.remittances!} />
            )}
          </>
        ),
    });
  }
  for (const difference of history.differences) {
    // Recorded in the same transaction as its close: shown right after it, whatever the clock said.
    const closing = history.closings.find((c) => c.operation_id === difference.close_operation_id);
    events.push({
      at: closing?.closed_at ?? difference.created_at,
      rank: 3,
      key: `difference-${difference.id}`,
      title: `${DIFFERENCE_LABELS[difference.kind]} de ${formatMoney(difference.amount)} (versión ${difference.settlement_version})`,
      by: difference.created_by_name,
      badges: difference.superseded_at ? <Badge>Reemplazada</Badge> : <Badge tone="info">Vigente</Badge>,
      children: (
        <ul className="space-y-0.5">
          {difference.allocations.map((allocation, i) => (
            <li key={i}>
              {allocation.target === "COMPANY" ? "La empresa" : allocation.employee_name}:{" "}
              <span className="tabular-nums">{formatMoney(allocation.amount)}</span>
              {allocation.reason && <span className="text-muted-foreground"> · {allocation.reason}</span>}
            </li>
          ))}
        </ul>
      ),
    });
  }
  for (const reopening of history.reopenings) {
    events.push({
      at: reopening.reopened_at,
      rank: 4,
      key: `reopen-${reopening.from_version}`,
      title: `Reapertura (versión ${reopening.from_version} → ${reopening.to_version})`,
      by: reopening.reopened_by_name,
      children: (
        <>
          <p>Motivo: {reopening.reason}</p>
          <p className="text-muted-foreground">
            {reopening.close_operation_id === null ? (
              "El cierre en $0 siguió vigente mientras se corregía; no había nada en Cajas."
            ) : (
              <>
                El cierre ({operationLink(reopening.close_operation_id)}) siguió vigente en Cajas mientras se corregía.
              </>
            )}
          </p>
        </>
      ),
    });
  }
  return events.sort((a, b) => Date.parse(a.at) - Date.parse(b.at) || a.rank - b.rank);
}

export function HistoryTab({ settlement }: { settlement: Settlement }) {
  const history = useQuery({
    queryKey: [...deliveryQueryKey, "history", settlement.id, settlement.version, settlement.status],
    queryFn: () => deliveryApi.history(settlement),
  });
  if (history.isPending) return <Skeleton className="h-40 w-full" aria-label="Cargando historial" />;
  if (history.isError) return <ErrorState error={history.error} onRetry={() => void history.refetch()} />;
  const events = timeline(settlement, history.data);
  return (
    <ol className="space-y-3" aria-label="Historial de la rendición">
      {events.map((event) => (
        <li key={event.key} className="rounded-md border border-border bg-surface px-4 py-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{event.title}</span>
            {event.badges}
            <span className="ml-auto text-xs text-muted-foreground tabular-nums">
              {formatDateTime(event.at)}
              {event.by && ` · ${event.by}`}
            </span>
          </div>
          {event.children && <div className="mt-1 space-y-1">{event.children}</div>}
        </li>
      ))}
    </ol>
  );
}
