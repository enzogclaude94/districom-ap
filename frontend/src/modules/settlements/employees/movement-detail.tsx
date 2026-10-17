"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { areaRole, authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";

import { EMPLOYEE_FINANCE_AREA, employeeFinanceApi, employeeFinanceQueryKey } from "./api";
import { employeeFinanceHref, movementTitle } from "./labels";
import { MovementLegs, ReverseMovementDialog } from "./movement-dialogs";

/** `/rendiciones/empleados/movimientos/[id]`: one consumption, advance or adjustment, with its legs and reversal. */
export function EmployeeMovementView({ movementId }: { movementId: number }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const areaAdmin = roleAtLeast(areaRole(me.data, EMPLOYEE_FINANCE_AREA), "admin");
  const cashOperator = roleAtLeast(moduleRole(me.data, "settlements"), "operator");
  const [reversing, setReversing] = useState(false);
  const movement = useQuery({
    queryKey: [...employeeFinanceQueryKey, "movement", movementId],
    queryFn: () => employeeFinanceApi.movement(movementId),
  });
  if (movement.isPending) return <Skeleton className="h-40 w-full" />;
  if (movement.isError) return <ErrorState error={movement.error} onRetry={() => void movement.refetch()} />;
  const row = movement.data;
  const reversed = row.status === "REVERSED";
  // Reversing an advance puts money back into its cash box or bank: that also needs operator in Rendiciones.
  const canReverse = areaAdmin && (row.kind !== "ADVANCE" || cashOperator);
  const facts: [string, string][] = [
    ["Fecha", formatDate(row.business_date)],
    ["Importe", formatMoney(row.amount)],
    ["En la cuenta interna", formatMoney(row.employee_amount, { signed: true })],
    [row.kind === "ADJUSTMENT" ? "Motivo" : "Descripción", row.description],
  ];
  if (row.funding_account_name) facts.push(["Entregado desde", row.funding_account_name]);
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
        title={movementTitle(row.kind, row.direction, row.id)}
        description={row.employee_name}
        actions={
          canReverse && !reversed ? (
            <Button variant="secondary" onClick={() => setReversing(true)}>
              Revertir
            </Button>
          ) : undefined
        }
      />
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center gap-2">
          <CardTitle>Movimiento</CardTitle>
          <Badge tone={reversed ? "neutral" : "info"} data-testid="movement-status">
            {reversed ? "Revertido" : "Vigente"}
          </Badge>
        </CardHeader>
        <CardContent className="space-y-4">
          <dl className="divide-y divide-border rounded-md border border-border text-sm">
            {facts.map(([label, value]) => (
              <div key={label} className="flex flex-wrap justify-between gap-x-4 gap-y-0.5 px-3 py-2">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="min-w-0 font-medium break-words tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
          <h3 className="text-sm font-medium">Asiento</h3>
          <MovementLegs legs={row.legs} testId="movement-detail-legs" />
          {reversed && (
            <p className="text-sm text-muted-foreground" data-testid="movement-reversal">
              Revertido por {row.reversed_by_name} el {formatDateTime(row.reversed_at!)}: {row.reversal_reason}. El
              reverso lleva la fecha original ({formatDate(row.business_date)}).
            </p>
          )}
        </CardContent>
      </Card>
      {reversing && <ReverseMovementDialog movement={row} onClose={() => setReversing(false)} />}
    </>
  );
}
