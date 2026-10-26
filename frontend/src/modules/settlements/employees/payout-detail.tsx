"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowLeft, Banknote } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { areaRole, authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";

import { EMPLOYEE_FINANCE_AREA, employeeFinanceApi, employeeFinanceQueryKey, type Payout } from "./api";
import {
  PAYOUT_METHOD_LABELS,
  employeeFinanceHref,
  payoutHref,
  payoutTitle,
  payrollHref,
  payrollTitle,
} from "./labels";
import { MovementLegs } from "./movement-dialogs";
import { ReversePayoutDialog } from "./payout-dialogs";

const PAGE_SIZE = 25;

const means = (payout: Payout) =>
  payout.items.map((i) => `${PAYOUT_METHOD_LABELS[i.method]} · ${i.account_name}`).join(" + ");

/** The legs of a payout: each cash box or bank out, the internal account in. */
function legsOf(payout: Payout) {
  return [
    ...payout.items.map((i) => ({
      account_id: i.account_id,
      account_name: i.account_name,
      account_kind: i.method === "CASH" ? "CASH_BOX" : "BANK",
      amount: `-${i.amount}`,
    })),
    {
      account_id: null,
      account_name: `Cuenta interna · ${payout.employee_name}`,
      account_kind: "EMPLOYEE",
      amount: payout.amount,
    },
  ];
}

/** `/rendiciones/empleados/pagos/[id]`: one payout of an internal payroll and its reversal (R8-5). */
export function PayoutView({ payoutId }: { payoutId: number }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const areaAdmin = roleAtLeast(areaRole(me.data, EMPLOYEE_FINANCE_AREA), "admin");
  const cashOperator = roleAtLeast(moduleRole(me.data, "settlements"), "operator");
  const [reversing, setReversing] = useState(false);
  const payout = useQuery({
    queryKey: [...employeeFinanceQueryKey, "payout", payoutId],
    queryFn: () => employeeFinanceApi.payout(payoutId),
  });
  if (payout.isPending) return <Skeleton className="h-40 w-full" />;
  if (payout.isError) return <ErrorState error={payout.error} onRetry={() => void payout.refetch()} />;
  const row = payout.data;
  const facts: [string, string][] = [
    ["Fecha", formatDate(row.business_date)],
    ["Importe", formatMoney(row.amount)],
    ["Liquidación", `${payrollTitle(row.payroll_id)} · ${row.payroll_period} · versión ${row.payroll_version}`],
    ["Medios", means(row)],
  ];
  if (row.reference) facts.push(["Referencia", row.reference]);
  facts.push(["Registrado", `${row.created_by_name} · ${formatDateTime(row.created_at)}`]);
  facts.push(["Operación", `n.º ${row.operation_id}`]);
  return (
    <>
      <Link
        href={payrollHref(row.payroll_id)}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden />
        {payrollTitle(row.payroll_id)}
      </Link>
      <PageHeader
        title={payoutTitle(row.id)}
        description={`${row.employee_name} · pago de liquidación interna`}
        actions={
          areaAdmin && cashOperator && row.in_effect ? (
            <Button variant="secondary" onClick={() => setReversing(true)}>
              Revertir
            </Button>
          ) : undefined
        }
      />
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center gap-2">
          <CardTitle>Pago</CardTitle>
          <Badge tone={row.in_effect ? "success" : "neutral"} data-testid="payout-status">
            {row.in_effect ? "Vigente" : "Revertido"}
          </Badge>
        </CardHeader>
        <CardContent className="space-y-4">
          <dl className="divide-y divide-border rounded-md border border-border text-sm" data-testid="payout-facts">
            {facts.map(([label, value]) => (
              <div key={label} className="flex flex-wrap justify-between gap-x-4 gap-y-0.5 px-3 py-2">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="min-w-0 font-medium break-words tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
          <h3 className="text-sm font-medium">Asiento</h3>
          <MovementLegs legs={legsOf(row)} testId="payout-detail-legs" />
          {!row.in_effect && (
            <p className="text-sm text-muted-foreground" data-testid="payout-reversal">
              Revertido por {row.reversed_by_name} el {formatDateTime(row.reversed_at!)}: {row.reversal_reason}. El
              reverso lleva la fecha original ({formatDate(row.business_date)}).
            </p>
          )}
          <div className="flex flex-wrap gap-4 text-sm">
            <Link href={payrollHref(row.payroll_id)} className="hover:underline">
              Ver liquidación
            </Link>
            <Link href={employeeFinanceHref(row.employee_id)} className="hover:underline">
              Ver cuenta interna de {row.employee_name}
            </Link>
          </div>
        </CardContent>
      </Card>
      {reversing && <ReversePayoutDialog payout={row} onClose={() => setReversing(false)} />}
    </>
  );
}

/** `/rendiciones/empleados/pagos`: every payout of internal payrolls, newest first, reversed ones too (R8-5). */
export function PayoutList() {
  const [page, setPage] = useState(0);
  const params = { limit: PAGE_SIZE, offset: page * PAGE_SIZE };
  const list = useQuery({
    queryKey: [...employeeFinanceQueryKey, "payouts", params],
    queryFn: () => employeeFinanceApi.payouts(params),
    placeholderData: keepPreviousData,
  });
  const total = list.data?.total ?? 0;
  return (
    <>
      <PageHeader
        title="Pagos de liquidaciones"
        description="Lo entregado a cada empleado por sus liquidaciones internas cerradas, en efectivo o por transferencia."
      />
      <Card>
        <CardContent className="space-y-4 pt-6">
          {list.isPending ? (
            <Skeleton className="h-40 w-full" />
          ) : list.isError ? (
            <ErrorState error={list.error} onRetry={() => void list.refetch()} />
          ) : list.data.items.length === 0 ? (
            <EmptyState
              icon={Banknote}
              title="Sin pagos"
              description="Un pago se registra desde una liquidación cerrada con saldo pendiente."
            />
          ) : (
            <Table stacked aria-label="Pagos de liquidaciones">
              <TableHeader>
                <TableRow>
                  <TableHead>Pago</TableHead>
                  <TableHead>Empleado</TableHead>
                  <TableHead>Fecha</TableHead>
                  <TableHead>Medios</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead className="text-right">Importe</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.data.items.map((row) => (
                  <TableRow key={row.id} data-testid={`payout-row-${row.id}`}>
                    <TableCell data-label="Pago">
                      <Link href={payoutHref(row.id)} className="font-medium hover:underline">
                        n.º {row.id}
                      </Link>
                      <Link href={payrollHref(row.payroll_id)} className="block text-xs text-muted-foreground">
                        {row.payroll_period}
                      </Link>
                    </TableCell>
                    <TableCell data-label="Empleado">{row.employee_name}</TableCell>
                    <TableCell data-label="Fecha" className="whitespace-nowrap tabular-nums">
                      {formatDate(row.business_date)}
                    </TableCell>
                    <TableCell data-label="Medios">{means(row)}</TableCell>
                    <TableCell data-label="Estado">
                      <Badge tone={row.in_effect ? "success" : "neutral"}>
                        {row.in_effect ? "Vigente" : "Revertido"}
                      </Badge>
                    </TableCell>
                    <TableCell data-label="Importe" className="text-right font-medium tabular-nums">
                      {formatMoney(row.amount)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {total > PAGE_SIZE && (
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">
                {page * PAGE_SIZE + 1}–{Math.min(total, (page + 1) * PAGE_SIZE)} de {total}
              </span>
              <div className="flex gap-2">
                <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
                  Anterior
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={(page + 1) * PAGE_SIZE >= total}
                  onClick={() => setPage(page + 1)}
                >
                  Siguiente
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </>
  );
}
