"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Bell, CalendarX } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import { settlementTitle } from "@/modules/settlements/delivery/labels";

import { chequeHref } from "@/modules/settlements/cheques/api";

import { EcheqDecisionDialog, ReceiptDialog, ResolveDialog, ResponsibleDialog, ReverseDialog } from "./actions";
import {
  pendingApi,
  pendingDetailKey,
  type Pending,
  type DeclaredEcheq,
  type PendingDetail as Detail,
  type PendingEvent,
  type Receipt,
  type Resolution,
} from "./api";
import { CorrectionNote } from "./settlement-pendings";
import { METHOD_LABELS, ORIGIN_LABELS, STATE_LABELS, STATE_TONES, ageText, eventLabel, pendingTitle } from "./labels";

type Open =
  | { kind: "receipt" | "resolve" | "responsible" }
  | { kind: "reverse-receipt"; item: Receipt }
  | { kind: "reverse-resolution"; item: Resolution }
  | { kind: "echeq"; item: DeclaredEcheq; to: "VERIFIED" | "VERIFICATION_REJECTED" }
  | null;

const operationHref = (id: number) => `/rendiciones/cajas/operaciones/${id}`;

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

/** "En alerta · 2 días" and, apart, "Fecha esperada vencida": two different rules, never mixed. */
export function AlertMarks({ pending }: { pending: Pending }) {
  return (
    <>
      {pending.alert && (
        <span className="block" data-testid="alert-mark">
          <Badge tone="warning">
            <Bell className="size-3" aria-hidden />
            En alerta · {ageText(pending.age_hours)}
          </Badge>
          {pending.balance && (
            <span className="block text-xs text-muted-foreground tabular-nums">
              Saldo pendiente: {formatMoney(pending.balance)}
            </span>
          )}
        </span>
      )}
      {pending.expected_date_overdue && (
        <span className="mt-1 block" data-testid="overdue-mark">
          <Badge tone="neutral">
            <CalendarX className="size-3" aria-hidden />
            Fecha esperada vencida
          </Badge>
        </span>
      )}
    </>
  );
}

function Reversed({ reversal }: { reversal: NonNullable<Receipt["reversal"]> }) {
  return (
    <div className="space-y-0.5">
      <Badge tone="warning">Revertido</Badge>
      <p className="text-xs text-muted-foreground">
        {reversal.reversed_by_name} · {formatDateTime(reversal.reversed_at)}
        <span className="block">Motivo: {reversal.reason}</span>
        <Link href={operationHref(reversal.operation_id)} className="text-primary hover:underline">
          Ver el reverso en Cajas
        </Link>
      </p>
    </div>
  );
}

function eventDetail(event: PendingEvent): string | null {
  const before = event.before ?? {};
  const after = event.after ?? {};
  const value = (record: Record<string, unknown>, key: string) => {
    const v = record[key];
    if (v === null || v === undefined) return "—";
    if (key === "amount") return formatMoney(String(v));
    if (key === "expected_date") return formatDate(String(v));
    return String(v);
  };
  switch (event.event_type) {
    case "AMOUNT_CHANGED":
      return `${value(before, "amount")} → ${value(after, "amount")}`;
    case "CUSTOMER_CHANGED":
      return `${value(before, "customer_name")} → ${value(after, "customer_name")}`;
    case "EXPECTED_DATE_CHANGED":
      return `${value(before, "expected_date")} → ${value(after, "expected_date")}`;
    case "RESPONSIBLE_CHANGED": {
      const from = before.responsible_name ?? before.responsible_employee_id;
      const to = after.responsible_name ?? after.responsible_employee_id;
      return from !== undefined && to !== undefined ? `${String(from)} → ${String(to)}` : null;
    }
    case "PUBLISHED":
    case "WITHDRAWN":
      return typeof after.amount === "string" || typeof before.amount === "string"
        ? `${formatMoney(String(after.amount ?? before.amount))} · versión ${event.settlement_version}`
        : `Versión ${event.settlement_version}`;
    case "CREATED":
      return typeof after.amount === "string"
        ? `${formatMoney(after.amount)} · ${String(after.customer_name ?? "")}`
        : null;
    default:
      return null;
  }
}

/** Where the pending was born, for the page header. */
function originText(pending: Detail) {
  if (pending.settlement_sequence_number !== null) {
    return `${settlementTitle(pending.settlement_sequence_number)} de ${pending.driver_name ?? ""} del ${formatDate(pending.settlement_business_date)}`;
  }
  const cheque = pending.origin_cheque_id !== null ? ` #${pending.origin_cheque_id}` : "";
  return `${ORIGIN_LABELS[pending.origin_type]}${cheque} del ${formatDate(pending.origin_business_date)}`;
}

/** Functional history from `pending_collection_events` (never from the audit log), newest last. */
function History({ pending }: { pending: Detail }) {
  if (pending.events.length === 0) return <p className="text-sm text-muted-foreground">Sin eventos.</p>;
  return (
    <ol className="space-y-3" aria-label="Historia del pendiente">
      {pending.events.map((event) => {
        const detail = eventDetail(event);
        return (
          <li key={event.id} className="border-l-2 border-border pl-3 text-sm">
            <p className="font-medium">{eventLabel(event.event_type, pending.origin_type)}</p>
            {detail && <p className="tabular-nums">{detail}</p>}
            {event.reason && <p className="text-muted-foreground">Motivo: {event.reason}</p>}
            <p className="text-xs text-muted-foreground">
              {event.created_by_name} · {formatDateTime(event.created_at)}
              {event.close_operation_id !== null && (
                <>
                  {" · "}
                  <Link href={operationHref(event.close_operation_id)} className="text-primary hover:underline">
                    Ver cierre en Cajas
                  </Link>
                </>
              )}
            </p>
          </li>
        );
      })}
    </ol>
  );
}

/** `/rendiciones/pendientes/[id]`: origin, financial state, receipts, resolutions and history of a pending. */
export function PendingDetailView({ pendingId }: { pendingId: number }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const query = useQuery({ queryKey: pendingDetailKey(pendingId), queryFn: () => pendingApi.get(pendingId) });
  const role = moduleRole(me.data, "settlements");
  const canOperate = roleAtLeast(role, "operator");
  const isAdmin = roleAtLeast(role, "admin");
  const [dialog, setDialog] = useState<Open>(null);
  const close = () => setDialog(null);

  const back = (
    <Link
      href="/rendiciones/pendientes"
      className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="size-4" aria-hidden />
      Pendientes
    </Link>
  );

  if (query.isPending) {
    return (
      <div className="space-y-3" aria-label="Cargando pendiente">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-60 w-full" />
      </div>
    );
  }
  if (query.isError) {
    return (
      <>
        {back}
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      </>
    );
  }

  const pending = query.data;
  const declared = pending.echeqs.filter((e) => e.verification_status === "DECLARED" && !e.voided);
  const owes = pending.state === "OPEN" || pending.state === "PARTIAL";
  const inEffect = pending.amount_in_effect !== null;

  return (
    <>
      {back}
      <PageHeader
        title={`${pendingTitle(pending.id)} · ${pending.customer_in_effect ?? pending.customer_name}`}
        description={originText(pending)}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={STATE_TONES[pending.state]} data-testid="pending-state">
              {STATE_LABELS[pending.state]}
            </Badge>
            {canOperate && owes && <Button onClick={() => setDialog({ kind: "receipt" })}>Registrar cobro</Button>}
            {isAdmin && owes && (
              <Button variant="secondary" onClick={() => setDialog({ kind: "resolve" })}>
                Resolver saldo
              </Button>
            )}
            {isAdmin && inEffect && (
              <Button variant="secondary" onClick={() => setDialog({ kind: "responsible" })}>
                Cambiar responsable
              </Button>
            )}
          </div>
        }
      />

      {pending.load_change && (
        <div role="status" className="rounded-md border border-warning/40 bg-warning-soft px-4 py-3 text-sm">
          La rendición está siendo corregida. Lo vigente en Cajas no cambia hasta confirmar el nuevo cierre.
          <CorrectionNote pending={pending} />
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Estado financiero</CardTitle>
            <CardDescription>
              Según el cierre vigente de la rendición. Los saldos los calcula el sistema.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-4">
              <Field label="Importe vigente">
                <span className="tabular-nums" data-testid="amount-in-effect">
                  {pending.amount_in_effect ? formatMoney(pending.amount_in_effect) : "No está en Cajas"}
                </span>
              </Field>
              <Field label="Saldo">
                <span className="font-semibold tabular-nums" data-testid="pending-balance">
                  {pending.balance !== null ? formatMoney(pending.balance) : "—"}
                </span>
              </Field>
              <Field label="Cobrado">
                <span className="tabular-nums" data-testid="pending-collected">
                  {formatMoney(pending.collected)}
                </span>
              </Field>
              <Field label="Resuelto">
                <span className="tabular-nums" data-testid="pending-resolved">
                  {formatMoney(pending.resolved)}
                </span>
              </Field>
              <Field label="Estado">{STATE_LABELS[pending.state]}</Field>
              <Field label="Antigüedad">{ageText(pending.age_hours)}</Field>
              <div className="col-span-2">
                <AlertMarks pending={pending} />
              </div>
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Origen</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-4">
              <Field label="Cliente">{pending.customer_in_effect ?? pending.customer_name}</Field>
              {pending.settlement_id !== null && pending.settlement_sequence_number !== null ? (
                <>
                  <Field label="Rendición">
                    <Link
                      href={`/rendiciones/repartos/${pending.settlement_id}`}
                      className="text-primary hover:underline"
                    >
                      {settlementTitle(pending.settlement_sequence_number)} del{" "}
                      {formatDate(pending.settlement_business_date)}
                    </Link>
                  </Field>
                  <Field label="Repartidor">{pending.driver_name}</Field>
                </>
              ) : (
                <>
                  <Field label="Origen">
                    <span data-testid="pending-origin">{ORIGIN_LABELS[pending.origin_type]}</span>
                  </Field>
                  <Field label="Cheque">
                    {pending.origin_cheque_id !== null ? (
                      <Link href={chequeHref(pending.origin_cheque_id)} className="text-primary hover:underline">
                        Cheque #{pending.origin_cheque_id}
                      </Link>
                    ) : (
                      "—"
                    )}{" "}
                    del {formatDate(pending.origin_business_date)}
                  </Field>
                </>
              )}
              <Field label="Responsable">
                <span data-testid="pending-responsible">{pending.responsible_name}</span>
              </Field>
              <Field label="Motivo">{pending.reason}</Field>
              <Field label="Fecha esperada">{formatDate(pending.expected_date)}</Field>
            </dl>
          </CardContent>
        </Card>
      </div>

      {declared.length > 0 && (
        <Card data-testid="declared-echeqs">
          <CardHeader>
            <CardTitle>ECHEQ declarados</CardTitle>
            <CardDescription>
              Este ECHEQ todavía no fue verificado. El saldo pendiente continúa vigente.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y divide-border">
              {declared.map((echeq) => (
                <li
                  key={echeq.cheque_id}
                  className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm"
                  data-testid={`declared-echeq-${echeq.cheque_id}`}
                >
                  <span>
                    <Link href={chequeHref(echeq.cheque_id)} className="text-primary hover:underline">
                      ECHEQ n.º {echeq.cheque_number}
                    </Link>{" "}
                    · <span className="tabular-nums">{formatMoney(echeq.amount)}</span>{" "}
                    <Badge tone="warning">Pendiente de verificación</Badge>
                  </span>
                  {canOperate && (
                    <span className="flex gap-2">
                      <Button size="sm" onClick={() => setDialog({ kind: "echeq", item: echeq, to: "VERIFIED" })}>
                        Verificar
                      </Button>
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => setDialog({ kind: "echeq", item: echeq, to: "VERIFICATION_REJECTED" })}
                      >
                        Rechazar verificación
                      </Button>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Cobros</CardTitle>
        </CardHeader>
        <CardContent>
          {pending.receipts.length === 0 ? (
            <p className="py-3 text-center text-sm text-muted-foreground">Sin cobros.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-right">Importe</TableHead>
                  <TableHead>Medio</TableHead>
                  <TableHead>Cuenta destino</TableHead>
                  <TableHead>Fecha de negocio</TableHead>
                  <TableHead>Registrado</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead>
                    <span className="sr-only">Acciones</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pending.receipts.map((receipt) => (
                  <TableRow key={receipt.id} data-testid={`receipt-${receipt.id}`}>
                    <TableCell className="text-right whitespace-nowrap tabular-nums">
                      {formatMoney(receipt.amount)}
                    </TableCell>
                    <TableCell>{METHOD_LABELS[receipt.method]}</TableCell>
                    <TableCell>{receipt.destination_name}</TableCell>
                    <TableCell className="tabular-nums">{formatDate(receipt.business_date)}</TableCell>
                    <TableCell className="text-xs whitespace-nowrap">
                      {receipt.created_by_name}
                      <span className="block text-muted-foreground">{formatDateTime(receipt.created_at)}</span>
                    </TableCell>
                    <TableCell>
                      {receipt.reversal ? (
                        <Reversed reversal={receipt.reversal} />
                      ) : (
                        <Badge tone="success">Vigente</Badge>
                      )}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      <Link
                        href={operationHref(receipt.operation_id)}
                        className="px-2 text-sm text-primary hover:underline"
                      >
                        Ver en Cajas
                      </Link>
                      {receipt.cheque_id !== null && (
                        <Link
                          href={chequeHref(receipt.cheque_id)}
                          className="px-2 text-sm text-primary hover:underline"
                        >
                          Ver cheque
                        </Link>
                      )}
                      {isAdmin && !receipt.reversal && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setDialog({ kind: "reverse-receipt", item: receipt })}
                        >
                          Revertir
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Resoluciones</CardTitle>
          <CardDescription>Saldo que no se cobró y alguien absorbió: la empresa o empleados.</CardDescription>
        </CardHeader>
        <CardContent>
          {pending.resolutions.length === 0 ? (
            <p className="py-3 text-center text-sm text-muted-foreground">Sin resoluciones.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-right">Importe</TableHead>
                  <TableHead>Partes</TableHead>
                  <TableHead>Motivo</TableHead>
                  <TableHead>Fecha de negocio</TableHead>
                  <TableHead>Registrada</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead>
                    <span className="sr-only">Acciones</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pending.resolutions.map((resolution) => (
                  <TableRow key={resolution.id} data-testid={`resolution-${resolution.id}`}>
                    <TableCell className="text-right whitespace-nowrap tabular-nums">
                      {formatMoney(resolution.amount)}
                    </TableCell>
                    <TableCell className="text-sm">
                      {resolution.allocations.map((part, i) => (
                        <span key={i} className="block whitespace-nowrap">
                          {part.target === "COMPANY" ? "Empresa (Incobrables)" : part.employee_name}:{" "}
                          <span className="tabular-nums">{formatMoney(part.amount)}</span>
                        </span>
                      ))}
                    </TableCell>
                    <TableCell>{resolution.reason}</TableCell>
                    <TableCell className="tabular-nums">{formatDate(resolution.business_date)}</TableCell>
                    <TableCell className="text-xs whitespace-nowrap">
                      {resolution.created_by_name}
                      <span className="block text-muted-foreground">{formatDateTime(resolution.created_at)}</span>
                    </TableCell>
                    <TableCell>
                      {resolution.reversal ? (
                        <Reversed reversal={resolution.reversal} />
                      ) : (
                        <Badge tone="success">Vigente</Badge>
                      )}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      <Link
                        href={operationHref(resolution.operation_id)}
                        className="px-2 text-sm text-primary hover:underline"
                      >
                        Ver en Cajas
                      </Link>
                      {isAdmin && !resolution.reversal && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setDialog({ kind: "reverse-resolution", item: resolution })}
                        >
                          Revertir
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Historia</CardTitle>
        </CardHeader>
        <CardContent>
          <History pending={pending} />
        </CardContent>
      </Card>

      {dialog?.kind === "receipt" && <ReceiptDialog pendingId={pending.id} onClose={close} />}
      {dialog?.kind === "resolve" && <ResolveDialog pending={pending} onClose={close} />}
      {dialog?.kind === "echeq" && (
        <EcheqDecisionDialog pending={pending} echeq={dialog.item} to={dialog.to} onClose={close} />
      )}
      {dialog?.kind === "responsible" && <ResponsibleDialog pending={pending} onClose={close} />}
      {dialog?.kind === "reverse-receipt" && (
        <ReverseDialog pending={pending} target={{ kind: "receipt", item: dialog.item }} onClose={close} />
      )}
      {dialog?.kind === "reverse-resolution" && (
        <ReverseDialog pending={pending} target={{ kind: "resolution", item: dialog.item }} onClose={close} />
      )}
    </>
  );
}
