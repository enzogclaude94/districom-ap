"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowLeft, Wallet } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { areaRole, authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";

import {
  EMPLOYEE_FINANCE_AREA,
  employeeFinanceApi,
  employeeFinanceQueryKey,
  type CompensationTerm,
  type MovementKind,
} from "./api";
import {
  LOAN_STATUS_LABELS,
  MOVEMENT_READING,
  ORIGIN_LABELS,
  READING_LABELS,
  READING_TONES,
  SALARY_NOTICE,
  TERM_STATUS_LABELS,
  documentOf,
  employeeLoanHref,
  loanTitle,
} from "./labels";
import { LoanDialog } from "./loan-dialogs";
import { MovementDialog } from "./movement-dialogs";
import { EndTermDialog, NewTermDialog } from "./term-dialogs";

const PAGE_SIZE = 20;

function AccountMovements({ employeeId }: { employeeId: number }) {
  const [page, setPage] = useState(0);
  const params = { limit: PAGE_SIZE, offset: page * PAGE_SIZE };
  const movements = useQuery({
    queryKey: [...employeeFinanceQueryKey, "movements", employeeId, params],
    queryFn: () => employeeFinanceApi.movements(employeeId, params),
    placeholderData: keepPreviousData,
  });
  if (movements.isPending) return <Skeleton className="h-24 w-full" />;
  if (movements.isError) return <ErrorState error={movements.error} onRetry={() => void movements.refetch()} />;
  if (movements.data.total === 0) {
    return (
      <EmptyState
        icon={Wallet}
        title="Sin movimientos"
        description="Cuando se le asigne una rendición o un pendiente, o se registre un consumo, un adelanto, un ajuste o un préstamo, el movimiento aparece acá."
      />
    );
  }
  return (
    <>
      <Table stacked aria-label="Movimientos de la cuenta interna">
        <TableHeader>
          <TableRow>
            <TableHead>Fecha</TableHead>
            <TableHead>Origen</TableHead>
            <TableHead>Documento</TableHead>
            <TableHead className="text-right">Importe</TableHead>
            <TableHead>Lectura</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {movements.data.items.map((movement) => {
            const document = documentOf(movement);
            return (
              <TableRow key={movement.entry_id} data-testid={`account-movement-${movement.entry_id}`}>
                <TableCell data-label="Fecha" className="whitespace-nowrap tabular-nums">
                  {formatDate(movement.business_date)}
                </TableCell>
                <TableCell data-label="Origen">
                  <span className="block">{ORIGIN_LABELS[movement.origin]}</span>
                  {movement.description && (
                    <span className="block text-sm break-words text-muted-foreground">{movement.description}</span>
                  )}
                </TableCell>
                <TableCell data-label="Documento">
                  {document.href ? (
                    <Link href={document.href} className="text-primary hover:underline">
                      {document.label}
                    </Link>
                  ) : (
                    document.label
                  )}
                  {movement.document_reversed && (
                    <Badge tone="neutral" className="ml-2">
                      Revertido
                    </Badge>
                  )}
                </TableCell>
                <TableCell data-label="Importe" className="text-right whitespace-nowrap tabular-nums">
                  {formatMoney(movement.amount, { signed: true })}
                </TableCell>
                <TableCell data-label="Lectura" className="text-sm text-muted-foreground">
                  {MOVEMENT_READING[movement.reading]}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {movements.data.total > PAGE_SIZE && (
        <div
          className={cn(
            "mt-3 flex items-center justify-between gap-2 text-sm text-muted-foreground",
            movements.isPlaceholderData && "opacity-60",
          )}
        >
          <span className="tabular-nums">
            {page * PAGE_SIZE + 1}–{page * PAGE_SIZE + movements.data.items.length} de {movements.data.total}
          </span>
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
              Anterior
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={(page + 1) * PAGE_SIZE >= movements.data.total}
              onClick={() => setPage(page + 1)}
            >
              Siguiente
            </Button>
          </div>
        </div>
      )}
    </>
  );
}

/** The employee's loans (R8-3): each with its own balance, from the loan, never from the internal account. */
function Loans({ employeeId, canGive, onGive }: { employeeId: number; canGive: boolean; onGive: () => void }) {
  const loans = useQuery({
    queryKey: [...employeeFinanceQueryKey, "loans", employeeId],
    queryFn: () => employeeFinanceApi.loans(employeeId),
  });
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <CardTitle>Préstamos</CardTitle>
          <CardDescription>Sin interés, en cuotas. Cada préstamo suma una vez a la cuenta interna.</CardDescription>
        </div>
        {canGive && (
          <Button size="sm" onClick={onGive}>
            Dar préstamo
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {loans.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : loans.isError ? (
          <ErrorState error={loans.error} onRetry={() => void loans.refetch()} />
        ) : loans.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">Sin préstamos.</p>
        ) : (
          <Table stacked aria-label="Préstamos del empleado">
            <TableHeader>
              <TableRow>
                <TableHead>Préstamo</TableHead>
                <TableHead>Fecha</TableHead>
                <TableHead className="text-right">Importe</TableHead>
                <TableHead className="text-right">Saldo</TableHead>
                <TableHead>Próximo vencimiento</TableHead>
                <TableHead>Estado</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loans.data.map((loan) => (
                <TableRow key={loan.id} data-testid={`loan-row-${loan.id}`}>
                  <TableCell data-label="Préstamo">
                    <Link href={employeeLoanHref(loan.id)} className="text-primary hover:underline">
                      {loanTitle(loan.id)}
                    </Link>
                    {loan.description && (
                      <span className="block text-sm break-words text-muted-foreground">{loan.description}</span>
                    )}
                  </TableCell>
                  <TableCell data-label="Fecha" className="whitespace-nowrap tabular-nums">
                    {formatDate(loan.business_date)}
                  </TableCell>
                  <TableCell data-label="Importe" className="text-right whitespace-nowrap tabular-nums">
                    {formatMoney(loan.principal)}
                  </TableCell>
                  <TableCell data-label="Saldo" className="text-right whitespace-nowrap tabular-nums">
                    {formatMoney(loan.balance)}
                  </TableCell>
                  <TableCell data-label="Próximo vencimiento" className="whitespace-nowrap tabular-nums">
                    {loan.next_due_on ? formatDate(loan.next_due_on) : "—"}
                  </TableCell>
                  <TableCell data-label="Estado">
                    <Badge tone={loan.status === "ACTIVE" ? "info" : "neutral"}>
                      {LOAN_STATUS_LABELS[loan.status]}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function TermHistory({ employeeId, canEdit, active }: { employeeId: number; canEdit: boolean; active: boolean }) {
  const [creating, setCreating] = useState(false);
  const [ending, setEnding] = useState<CompensationTerm | null>(null);
  const terms = useQuery({
    queryKey: [...employeeFinanceQueryKey, "terms", employeeId],
    queryFn: () => employeeFinanceApi.terms(employeeId),
  });
  const open = terms.data?.some((term) => term.valid_to === null) ?? false;
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <CardTitle>Condición salarial</CardTitle>
          <CardDescription>{SALARY_NOTICE}</CardDescription>
        </div>
        {canEdit && active && !open && terms.isSuccess && (
          <Button size="sm" onClick={() => setCreating(true)}>
            Cargar condición
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {terms.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : terms.isError ? (
          <ErrorState error={terms.error} onRetry={() => void terms.refetch()} />
        ) : terms.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">Sin condición salarial cargada.</p>
        ) : (
          <Table stacked aria-label="Historial de condiciones salariales">
            <TableHeader>
              <TableRow>
                <TableHead>Vigencia</TableHead>
                <TableHead className="text-right">Sueldo base mensual</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead>Detalle</TableHead>
                <TableHead>
                  <span className="sr-only">Acciones</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {terms.data.map((term) => (
                <TableRow key={term.id} data-testid={`compensation-term-${term.id}`}>
                  <TableCell data-label="Vigencia" className="whitespace-nowrap tabular-nums">
                    {formatDate(term.valid_from)} → {term.valid_to ? formatDate(term.valid_to) : "sin fin"}
                  </TableCell>
                  <TableCell data-label="Sueldo base" className="text-right font-medium whitespace-nowrap tabular-nums">
                    {formatMoney(term.base_salary)}
                  </TableCell>
                  <TableCell data-label="Estado">
                    <Badge tone={term.status === "CURRENT" ? "info" : "neutral"}>
                      {TERM_STATUS_LABELS[term.status]}
                    </Badge>
                  </TableCell>
                  <TableCell data-label="Detalle" className="text-sm text-muted-foreground">
                    <span className="block">
                      Cargada por {term.created_by_name} el {formatDateTime(term.created_at)}
                    </span>
                    {term.notes && <span className="block">{term.notes}</span>}
                    {term.ended_at && (
                      <span className="block">
                        Terminada por {term.ended_by_name} el {formatDateTime(term.ended_at)}: {term.end_reason}
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    {canEdit && term.valid_to === null && (
                      <Button variant="ghost" size="sm" onClick={() => setEnding(term)}>
                        Terminar
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {canEdit && open && (
          <p className="mt-3 text-xs text-muted-foreground">
            Para un cambio de sueldo, terminá la condición vigente y cargá la nueva desde el día siguiente.
          </p>
        )}
      </CardContent>
      {creating && <NewTermDialog employeeId={employeeId} onClose={() => setCreating(false)} />}
      {ending && <EndTermDialog term={ending} onClose={() => setEnding(null)} />}
    </Card>
  );
}

/** `/rendiciones/empleados/[id]`: the internal account (read-only) and the salary terms of one employee. */
export function EmployeeFinanceDetailView({ employeeId }: { employeeId: number }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const role = areaRole(me.data, EMPLOYEE_FINANCE_AREA);
  const canEdit = roleAtLeast(role, "admin");
  const canOperate = roleAtLeast(role, "operator");
  // An advance or a loan moves a cash box or the bank: it also needs operator in Rendiciones (ADR 010 D9).
  const canAdvance = canEdit && roleAtLeast(moduleRole(me.data, "settlements"), "operator");
  const [registering, setRegistering] = useState<MovementKind | null>(null);
  const [lending, setLending] = useState(false);
  const detail = useQuery({
    queryKey: [...employeeFinanceQueryKey, "detail", employeeId],
    queryFn: () => employeeFinanceApi.detail(employeeId),
  });
  const back = (
    <Link
      href="/rendiciones/empleados"
      className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="size-4" aria-hidden />
      Empleados
    </Link>
  );
  if (detail.isPending) return <Skeleton className="h-40 w-full" />;
  if (detail.isError) {
    return (
      <>
        {back}
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      </>
    );
  }
  const employee = detail.data;
  return (
    <>
      {back}
      <PageHeader
        title={employee.full_name}
        description={
          employee.active
            ? employee.hire_date
              ? `Ingreso: ${formatDate(employee.hire_date)}`
              : undefined
            : "Empleado inactivo: su historia sigue visible."
        }
      />

      <Card>
        <CardHeader>
          <CardTitle>Cuenta interna</CardTitle>
          <CardDescription>
            Saldo neto con la empresa. Positivo: el empleado debe a la empresa. Negativo: la empresa le debe.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <p className="font-display text-2xl font-semibold tabular-nums" data-testid="internal-balance">
              {formatMoney(employee.balance)}
            </p>
            <Badge tone={READING_TONES[employee.balance_reading]}>{READING_LABELS[employee.balance_reading]}</Badge>
          </div>
          {canOperate && (
            <div className="flex flex-wrap gap-2">
              <Button size="sm" onClick={() => setRegistering("CONSUMPTION")}>
                Registrar consumo
              </Button>
              {canAdvance && (
                <Button size="sm" variant="secondary" onClick={() => setRegistering("ADVANCE")}>
                  Entregar adelanto
                </Button>
              )}
              {canEdit && (
                <Button size="sm" variant="secondary" onClick={() => setRegistering("ADJUSTMENT")}>
                  Registrar ajuste
                </Button>
              )}
            </div>
          )}
          <h3 className="text-sm font-medium">Movimientos de la cuenta interna</h3>
          <AccountMovements employeeId={employeeId} />
        </CardContent>
      </Card>

      <Loans employeeId={employeeId} canGive={canAdvance} onGive={() => setLending(true)} />
      <TermHistory employeeId={employeeId} canEdit={canEdit} active={employee.active} />
      {lending && (
        <LoanDialog employeeId={employeeId} employeeName={employee.full_name} onClose={() => setLending(false)} />
      )}
      {registering && (
        <MovementDialog
          kind={registering}
          employeeId={employeeId}
          employeeName={employee.full_name}
          onClose={() => setRegistering(null)}
        />
      )}
    </>
  );
}
