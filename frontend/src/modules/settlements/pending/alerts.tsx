"use client";

import { useQuery } from "@tanstack/react-query";
import { Bell } from "lucide-react";
import Link from "next/link";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatMoney } from "@/lib/format";

import { pendingApi, pendingSummaryKey } from "./api";

export const ALERT_LIST_HREF = "/rendiciones/pendientes?alerta=1";

/** Read on every visit (alerts are computed on read, nothing is scheduled), shared by home and menu. */
function useAlertSummary() {
  return useQuery({ queryKey: pendingSummaryKey, queryFn: pendingApi.summary, staleTime: 30_000 });
}

/** Discreet count next to "Pendientes" in the menu; nothing when no pending is in alert. */
export function PendingAlertBadge() {
  const summary = useAlertSummary();
  const count = summary.data?.count ?? 0;
  if (count === 0) return null;
  return (
    <span
      className="rounded-full bg-warning-soft px-1.5 text-[11px] font-medium text-warning tabular-nums"
      aria-label={count === 1 ? "1 pendiente en alerta" : `${count} pendientes en alerta`}
      data-testid="pending-alert-badge"
    >
      {count}
    </span>
  );
}

/** "Pendientes en alerta" card of the Rendiciones home. */
export function PendingAlertCard() {
  const summary = useAlertSummary();
  return (
    <Card className="max-w-md" data-testid="pending-alert-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bell className="size-4 text-muted-foreground" aria-hidden />
          Pendientes en alerta
        </CardTitle>
        {summary.data && (
          <CardDescription>Con saldo y más de {summary.data.alert_hours} h desde que se registraron.</CardDescription>
        )}
      </CardHeader>
      <CardContent className="space-y-2">
        {summary.isPending ? (
          <Skeleton className="h-12 w-full" />
        ) : summary.isError ? (
          <p className="text-sm text-muted-foreground">No se pudo leer el resumen de pendientes.</p>
        ) : summary.data.count === 0 ? (
          <p className="text-sm text-muted-foreground">No hay pendientes en alerta.</p>
        ) : (
          <>
            <p className="font-display text-xl font-semibold tabular-nums" data-testid="alert-count">
              {summary.data.count === 1 ? "1 pendiente en alerta" : `${summary.data.count} pendientes en alerta`}
            </p>
            <p className="text-sm tabular-nums" data-testid="alert-balance">
              {formatMoney(summary.data.balance)} pendientes
            </p>
          </>
        )}
        <Link href={ALERT_LIST_HREF} className="inline-block text-sm text-primary hover:underline">
          Ver pendientes
        </Link>
      </CardContent>
    </Card>
  );
}
