"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { areaRole, authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";

import { EMPLOYEE_FINANCE_AREA, employeeFinanceApi, employeeFinanceQueryKey, type LoanInstallment } from "./api";
import { INSTALLMENT_STATUS_LABELS, LOAN_STATUS_LABELS, employeeFinanceHref, loanTitle } from "./labels";
import { DeferInstallmentDialog, ReverseLoanDialog } from "./loan-dialogs";
import { MovementLegs } from "./movement-dialogs";

const replacement = (item: { number: number; amount: string; due_on: string }) =>
  `Cuota ${item.number} · ${formatMoney(item.amount)} · ${formatDate(item.due_on)}`;

/** Each deferral as it happened, oldest first; the original installment is never hidden (Enzo's point 13). */
function History({ installments }: { installments: LoanInstallment[] }) {
  const deferred = installments
    .filter((i) => i.status === "DEFERRED")
    .sort((a, b) => (a.deferred_at ?? "").localeCompare(b.deferred_at ?? ""));
  if (deferred.length === 0) return <p className="text-sm text-muted-foreground">Sin postergaciones.</p>;
  return (
    <ol className="space-y-3" data-testid="loan-history">
      {deferred.map((i) => (
        <li
          key={i.id}
          className="rounded-md border border-border px-3 py-2 text-sm"
          data-testid={`deferral-${i.number}`}
        >
          <p className="font-medium">
            Cuota {i.number} · {formatMoney(i.amount)} · vencía {formatDate(i.due_on)}
          </p>
          <p className="text-muted-foreground">
            Postergada el {formatDateTime(i.deferred_at!)} por {i.deferred_by_name}
          </p>
          <p className="break-words">Motivo: {i.deferred_reason}</p>
          <p>Reemplazada por: {i.replaced_by.map(replacement).join(", ")}</p>
        </li>
      ))}
    </ol>
  );
}

/** `/rendiciones/empleados/prestamos/[id]`: one loan with its schedule, deferrals and reversal (R8-3). */
export function EmployeeLoanView({ loanId }: { loanId: number }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const areaAdmin = roleAtLeast(areaRole(me.data, EMPLOYEE_FINANCE_AREA), "admin");
  const cashOperator = roleAtLeast(moduleRole(me.data, "settlements"), "operator");
  const [reversing, setReversing] = useState(false);
  const [deferring, setDeferring] = useState<LoanInstallment | null>(null);
  const loan = useQuery({
    queryKey: [...employeeFinanceQueryKey, "loan", loanId],
    queryFn: () => employeeFinanceApi.loan(loanId),
  });
  if (loan.isPending) return <Skeleton className="h-40 w-full" />;
  if (loan.isError) return <ErrorState error={loan.error} onRetry={() => void loan.refetch()} />;
  const row = loan.data;
  const active = row.status === "ACTIVE";
  // Reversing returns the money to its cash box or bank: that also needs operator in Rendiciones (as an advance).
  const canReverse = areaAdmin && cashOperator && active;
  const canDefer = areaAdmin && active;
  const facts: [string, string][] = [
    ["Fecha", formatDate(row.business_date)],
    ["Importe prestado", formatMoney(row.principal)],
    ["Entregado desde", row.funding_account_name],
    ["Cuotas", `${row.installment_count} · la primera vence el ${formatDate(row.first_due_on)}`],
    ["Saldo del préstamo", formatMoney(row.balance)],
  ];
  if (row.description) facts.push(["Descripción", row.description]);
  if (row.reference) facts.push(["Referencia", row.reference]);
  facts.push(["Registrado", `${row.created_by_name} · ${formatDateTime(row.created_at)}`]);
  return (
    <>
      <Link
        href={employeeFinanceHref(row.employee_id)}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden />
        {row.employee_name}
      </Link>
      <PageHeader
        title={loanTitle(row.id)}
        description={`${row.employee_name} · sin interés`}
        actions={
          canReverse ? (
            <Button variant="secondary" onClick={() => setReversing(true)}>
              Revertir
            </Button>
          ) : undefined
        }
      />
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center gap-2">
          <CardTitle>Préstamo</CardTitle>
          <Badge tone={active ? "info" : "neutral"} data-testid="loan-status">
            {LOAN_STATUS_LABELS[row.status]}
          </Badge>
        </CardHeader>
        <CardContent className="space-y-4">
          <dl className="divide-y divide-border rounded-md border border-border text-sm" data-testid="loan-facts">
            {facts.map(([label, value]) => (
              <div key={label} className="flex flex-wrap justify-between gap-x-4 gap-y-0.5 px-3 py-2">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="min-w-0 font-medium break-words tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
          <h3 className="text-sm font-medium">Asiento</h3>
          <MovementLegs legs={row.legs} testId="loan-detail-legs" />
          {!active && (
            <p className="text-sm text-muted-foreground" data-testid="loan-reversal">
              Revertido por {row.reversed_by_name} el {formatDateTime(row.reversed_at!)}: {row.reversal_reason}. El
              reverso lleva la fecha original ({formatDate(row.business_date)}); las cuotas programadas se cancelaron.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Cronograma</CardTitle>
          <CardDescription>
            Las cuotas programadas suman el saldo del préstamo. Una cuota postergada queda como historia.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table stacked aria-label="Cronograma de cuotas">
            <TableHeader>
              <TableRow>
                <TableHead>Cuota</TableHead>
                <TableHead>Vencimiento</TableHead>
                <TableHead className="text-right">Importe</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead>
                  <span className="sr-only">Acciones</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {row.installments.map((i) => (
                <TableRow key={i.id} data-testid={`installment-${i.number}`}>
                  <TableCell data-label="Cuota" className="tabular-nums">
                    {i.number}
                    {i.deferred_from_number !== null && (
                      <span className="block text-xs text-muted-foreground">
                        reemplaza a la {i.deferred_from_number}
                      </span>
                    )}
                  </TableCell>
                  <TableCell data-label="Vencimiento" className="whitespace-nowrap tabular-nums">
                    {formatDate(i.due_on)}
                  </TableCell>
                  <TableCell data-label="Importe" className="text-right whitespace-nowrap tabular-nums">
                    {formatMoney(i.amount)}
                  </TableCell>
                  <TableCell data-label="Estado">
                    <Badge tone={i.status === "SCHEDULED" ? "info" : "neutral"}>
                      {INSTALLMENT_STATUS_LABELS[i.status]}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {canDefer && i.status === "SCHEDULED" && (
                      <Button variant="ghost" size="sm" onClick={() => setDeferring(i)}>
                        Postergar
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Historia</CardTitle>
        </CardHeader>
        <CardContent>
          <History installments={row.installments} />
        </CardContent>
      </Card>
      {reversing && <ReverseLoanDialog loan={row} onClose={() => setReversing(false)} />}
      {deferring && <DeferInstallmentDialog loan={row} installment={deferring} onClose={() => setDeferring(null)} />}
    </>
  );
}
