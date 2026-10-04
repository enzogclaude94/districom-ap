"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useId, useState, type FormEvent, type ReactNode } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { cashApi, cashQueryKey, reversePath, type Operation } from "@/modules/settlements/cash/api";
import { formatDate, formatDateTime, formatMoney, todayIso } from "@/lib/format";

import { cashErrorView } from "./errors";
import { ACCOUNT_KIND_LABELS, OPERATION_LABELS, sourceLabel } from "./labels";
import { StoredAttempt } from "./stored-attempt";
import { useCashOperation } from "./use-cash-operation";

const operationHref = (id: number) => `/rendiciones/cajas/operaciones/${id}`;

function ReverseDialog({ operation, onClose }: { operation: Operation; onClose: () => void }) {
  const reasonId = useId();
  const dateId = useId();
  const [reason, setReason] = useState("");
  const [businessDate, setBusinessDate] = useState(todayIso);
  const [problem, setProblem] = useState<string | null>(null);
  const [reversal, setReversal] = useState<Operation | null>(null);
  const reverse = useCashOperation(`reverse:${operation.id}`, setReversal);
  const pending = reverse.mutation.isPending;
  const names = (id: number) => operation.entries.find((entry) => entry.account_id === id)?.account_name;
  const error = reverse.mutation.error ? cashErrorView(reverse.mutation.error, names, businessDate) : null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const local = !reason.trim()
      ? "Indicá el motivo."
      : !businessDate || businessDate > todayIso()
        ? "La fecha de negocio no puede ser posterior a hoy."
        : null;
    setProblem(local);
    if (!local) {
      reverse.submit((requestId) => ({
        path: reversePath(operation.id),
        body: { client_request_id: requestId, reason: reason.trim(), business_date: businessDate },
        summary: `Reverso de la operación n.º ${operation.id} · ${formatDate(businessDate)}`,
      }));
    }
  };

  if (reversal) {
    return (
      <Dialog title="Operación revertida" onClose={onClose}>
        <div className="space-y-4 text-sm">
          <p role="status">
            Se registró el reverso n.º {reversal.id}. La operación original sigue en el historial, marcada como
            revertida.
          </p>
          <div className="flex justify-end gap-2">
            <Link
              href={operationHref(reversal.id)}
              className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              Ver el reverso
            </Link>
            <Button variant="secondary" onClick={onClose}>
              Cerrar
            </Button>
          </div>
        </div>
      </Dialog>
    );
  }

  if (reverse.stored) {
    return (
      <Dialog title="Revertir operación" onClose={onClose}>
        <StoredAttempt operation={reverse} names={names} onClose={onClose} />
      </Dialog>
    );
  }

  return (
    <Dialog title="Revertir operación" onClose={onClose}>
      <form className="space-y-4" onSubmit={submit} noValidate>
        <div className="space-y-1 rounded-md border border-border bg-muted px-3 py-2 text-sm">
          <p className="font-medium">El movimiento original no se borra.</p>
          <p className="text-muted-foreground">
            Se crea un movimiento inverso por los mismos importes, que deja los saldos como antes de esta operación. Las
            dos quedan en el historial y auditadas con tu usuario.
          </p>
        </div>
        <fieldset disabled={pending} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor={reasonId}>Motivo</Label>
            <textarea
              id={reasonId}
              maxLength={500}
              className="flex min-h-20 w-full rounded-md border border-border bg-surface px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={dateId}>Fecha de negocio del reverso</Label>
            <Input
              id={dateId}
              type="date"
              max={todayIso()}
              className="w-44 tabular-nums"
              value={businessDate}
              onChange={(event) => setBusinessDate(event.target.value)}
            />
          </div>
        </fieldset>
        {(problem || error) && (
          <div
            role="alert"
            className="flex gap-2 rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            <p>{problem ?? error?.message}</p>
          </div>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          {error?.conflict ? (
            <Button variant="secondary" onClick={reverse.restart}>
              Empezar de nuevo
            </Button>
          ) : (
            <Button type="submit" disabled={pending}>
              {pending ? "Revirtiendo…" : error?.uncertain ? "Reintentar" : "Revertir operación"}
            </Button>
          )}
        </div>
      </form>
    </Dialog>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

/** One operation with its ledger legs, including technical accounts, so it shows where the value came from and went. */
export function OperationDetail({ operationId }: { operationId: number }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const query = useQuery({
    queryKey: [...cashQueryKey, "operation", operationId],
    queryFn: () => cashApi.operation(operationId),
  });
  const [reversing, setReversing] = useState(false);
  const isAdmin = roleAtLeast(moduleRole(me.data, "settlements"), "admin");

  const back = (
    <Link href="/rendiciones/cajas" className="inline-flex items-center gap-1 text-sm text-primary hover:underline">
      <ArrowLeft className="size-4" aria-hidden />
      Volver a Cajas
    </Link>
  );

  if (query.isPending) {
    return (
      <div className="space-y-4" aria-label="Cargando operación">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }
  if (query.isError) {
    if (query.error instanceof ApiError && query.error.status === 404) {
      return (
        <>
          {back}
          <EmptyState title="La operación no existe" description="Puede que el enlace sea incorrecto." />
        </>
      );
    }
    return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  }

  const operation = query.data;
  const canReverse = isAdmin && operation.type !== "REVERSAL" && operation.reversed_by_operation_id === null;

  return (
    <>
      {back}
      <PageHeader
        title={`${OPERATION_LABELS[operation.type]} n.º ${operation.id}`}
        description={operation.description ?? undefined}
        actions={
          canReverse && (
            <Button variant="secondary" onClick={() => setReversing(true)}>
              <RotateCcw aria-hidden />
              Revertir operación
            </Button>
          )
        }
      />

      {operation.reversed_by_operation_id !== null && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-warning/40 bg-warning-soft px-4 py-3 text-sm">
          <Badge tone="warning">Revertida</Badge>
          <span>Esta operación fue revertida y ya no afecta los saldos.</span>
          <Link href={operationHref(operation.reversed_by_operation_id)} className="text-primary hover:underline">
            Ver el reverso n.º {operation.reversed_by_operation_id}
          </Link>
        </div>
      )}
      {operation.reverses_operation_id !== null && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-muted px-4 py-3 text-sm">
          <span>Este movimiento revierte la operación</span>
          <Link href={operationHref(operation.reverses_operation_id)} className="text-primary hover:underline">
            n.º {operation.reverses_operation_id}
          </Link>
        </div>
      )}

      <Card>
        <CardContent className="pt-5">
          <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Tipo">{OPERATION_LABELS[operation.type]}</Field>
            <Field label="Fecha de negocio">
              <span className="tabular-nums">{formatDate(operation.business_date)}</span>
            </Field>
            <Field label="Registrada por">{operation.created_by_name}</Field>
            <Field label="Fecha y hora de registro">
              <span className="tabular-nums">{formatDateTime(operation.created_at)}</span>
            </Field>
            <Field label={operation.type === "ADJUSTMENT" || operation.type === "REVERSAL" ? "Motivo" : "Descripción"}>
              {operation.description ?? "—"}
            </Field>
            <Field label="Origen">{sourceLabel(operation.source_type, operation.source_id)}</Field>
          </dl>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Movimientos del libro</CardTitle>
          <p className="text-sm text-muted-foreground">
            Cada operación suma cero: lo que sale de una cuenta entra en otra. Saldo inicial y Ajustes son cuentas
            técnicas que explican el origen del valor; no son dinero disponible.
          </p>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Cuenta</TableHead>
                <TableHead>Tipo de cuenta</TableHead>
                <TableHead className="text-right">Importe</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {operation.entries.map((entry) => (
                <TableRow key={entry.account_id}>
                  <TableCell>{entry.account_name}</TableCell>
                  <TableCell className="text-muted-foreground">{ACCOUNT_KIND_LABELS[entry.account_kind]}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatMoney(entry.amount, { signed: true })}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {reversing && <ReverseDialog operation={operation} onClose={() => setReversing(false)} />}
    </>
  );
}
