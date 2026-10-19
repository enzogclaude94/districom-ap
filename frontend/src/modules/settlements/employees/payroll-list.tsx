"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ReceiptText } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useRef, useState, type FormEvent } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { areaRole, authApi, currentUserQueryKey, roleAtLeast } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { formatDate, formatMoney, todayIso } from "@/lib/format";
import { newRequestId } from "@/modules/settlements/cash/request-id";
import { selectClass } from "@/modules/settlements/delivery/form";

import {
  EMPLOYEE_FINANCE_AREA,
  employeeFinanceApi,
  employeeFinanceQueryKey,
  type PayrollKind,
  type PayrollRow,
  type PayrollStatus,
} from "./api";
import { Problem } from "./movement-dialogs";
import {
  PAYROLL_KIND_LABELS,
  PAYROLL_NOTICE,
  PAYROLL_STATUS_LABELS,
  PAYROLL_STATUS_TONES,
  payrollHref,
} from "./labels";

const PAGE_SIZE = 25;

/** The period, inclusive. */
export const periodLabel = (row: Pick<PayrollRow, "period_start" | "period_end">) =>
  `${formatDate(row.period_start)} al ${formatDate(row.period_end)}`;

function firstOfMonth(iso: string) {
  return `${iso.slice(0, 8)}01`;
}

function lastOfMonth(iso: string) {
  const [year, month] = iso.split("-").map(Number);
  const day = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return `${iso.slice(0, 8)}${String(day).padStart(2, "0")}`;
}

/** Opens a payroll: employee, kind and an inclusive period (the current month by default). */
export function NewPayrollDialog({
  onClose,
  employee: fixed,
}: {
  onClose: () => void;
  /** Opened from one employee's page: that employee, without the select. */
  employee?: { id: number; name: string };
}) {
  const ids = { employee: useId(), kind: useId(), start: useId(), end: useId(), payment: useId() };
  const router = useRouter();
  const queryClient = useQueryClient();
  const requestId = useRef(newRequestId());
  const today = todayIso();
  const [employee, setEmployee] = useState<number | null>(fixed?.id ?? null);
  const [kind, setKind] = useState<PayrollKind>("REGULAR");
  const [start, setStart] = useState(firstOfMonth(today));
  const [end, setEnd] = useState(lastOfMonth(today));
  const [payment, setPayment] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const employees = useQuery({
    queryKey: [...employeeFinanceQueryKey, "list", { limit: 200, offset: 0 }],
    queryFn: () => employeeFinanceApi.list({ limit: 200, offset: 0 }),
    enabled: !fixed,
  });
  const create = useMutation({
    mutationFn: () =>
      employeeFinanceApi.writePayroll("POST", "/payrolls", {
        client_request_id: requestId.current,
        employee_id: employee,
        kind,
        period_start: start,
        period_end: end,
        payment_date: payment || null,
      }),
    onSuccess: (payroll) => {
      void queryClient.invalidateQueries({ queryKey: employeeFinanceQueryKey });
      router.push(payrollHref(payroll.id));
    },
    onError: (error) => {
      // A business refusal will not change on retry with the same id; any other outcome is retried as is.
      if (error instanceof ApiError && error.status < 500) requestId.current = newRequestId();
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const local =
      employee === null
        ? "Elegí el empleado."
        : !start || !end
          ? "Indicá el período."
          : start > end
            ? "El período empieza después de terminar."
            : null;
    setProblem(local);
    if (!local && !create.isPending) create.mutate();
  };
  return (
    <Dialog title="Nueva liquidación interna" description={PAYROLL_NOTICE} onClose={onClose}>
      <form className="space-y-4" onSubmit={submit} noValidate>
        <div className="space-y-1.5">
          <Label htmlFor={ids.employee}>Empleado</Label>
          {fixed ? (
            <Input id={ids.employee} value={fixed.name} readOnly />
          ) : (
            <select
              id={ids.employee}
              className={selectClass}
              value={employee ?? ""}
              disabled={create.isPending}
              onChange={(event) => setEmployee(event.target.value ? Number(event.target.value) : null)}
            >
              <option value="">Elegí un empleado</option>
              {(employees.data?.items ?? []).map((row) => (
                <option key={row.employee_id} value={row.employee_id}>
                  {row.full_name}
                </option>
              ))}
            </select>
          )}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={ids.kind}>Tipo</Label>
          <select
            id={ids.kind}
            className={selectClass}
            value={kind}
            disabled={create.isPending}
            onChange={(event) => setKind(event.target.value as PayrollKind)}
          >
            <option value="REGULAR">Regular (aplica cuotas de préstamos; no se superpone)</option>
            <option value="EXTRAORDINARY">Extraordinaria (sin cuotas; puede superponerse)</option>
          </select>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor={ids.start}>Desde</Label>
            <Input id={ids.start} type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.end}>Hasta (inclusive)</Label>
            <Input id={ids.end} type="date" min={start} value={end} onChange={(e) => setEnd(e.target.value)} />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={ids.payment}>Fecha de pago prevista (opcional)</Label>
          <Input id={ids.payment} type="date" value={payment} onChange={(e) => setPayment(e.target.value)} />
        </div>
        {(problem || create.error) && (
          <Problem>
            {problem ?? (create.error instanceof ApiError ? create.error.message : "No se pudo crear. Reintentá.")}
          </Problem>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? "Creando…" : "Crear liquidación"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/** `/rendiciones/empleados/liquidaciones`: every internal payroll, newest period first (R8-4). */
export function PayrollList() {
  const statusId = useId();
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const canCreate = roleAtLeast(areaRole(me.data, EMPLOYEE_FINANCE_AREA), "operator");
  const [status, setStatus] = useState<PayrollStatus | "">("");
  const [page, setPage] = useState(0);
  const [creating, setCreating] = useState(false);
  const params = { status: status || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE };
  const list = useQuery({
    queryKey: [...employeeFinanceQueryKey, "payrolls", params],
    queryFn: () => employeeFinanceApi.payrolls(params),
    placeholderData: keepPreviousData,
  });
  const total = list.data?.total ?? 0;
  return (
    <>
      <PageHeader
        title="Liquidaciones internas"
        description={`${PAYROLL_NOTICE} Haberes, descuentos y créditos aplicados de la cuenta interna.`}
        actions={canCreate ? <Button onClick={() => setCreating(true)}>Nueva liquidación</Button> : undefined}
      />
      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="max-w-xs space-y-1.5">
            <Label htmlFor={statusId}>Estado</Label>
            <select
              id={statusId}
              className={selectClass}
              value={status}
              onChange={(event) => {
                setStatus(event.target.value as PayrollStatus | "");
                setPage(0);
              }}
            >
              <option value="">Todas</option>
              {(Object.keys(PAYROLL_STATUS_LABELS) as PayrollStatus[]).map((key) => (
                <option key={key} value={key}>
                  {PAYROLL_STATUS_LABELS[key]}
                </option>
              ))}
            </select>
          </div>
          {list.isPending ? (
            <Skeleton className="h-40 w-full" />
          ) : list.isError ? (
            <ErrorState error={list.error} onRetry={() => void list.refetch()} />
          ) : list.data.items.length === 0 ? (
            <EmptyState
              icon={ReceiptText}
              title="Sin liquidaciones"
              description="Creá la primera desde «Nueva liquidación»."
            />
          ) : (
            <Table stacked aria-label="Liquidaciones internas">
              <TableHeader>
                <TableRow>
                  <TableHead>Empleado</TableHead>
                  <TableHead>Período</TableHead>
                  <TableHead>Tipo</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead className="text-right">Haberes</TableHead>
                  <TableHead className="text-right">Descuentos</TableHead>
                  <TableHead className="text-right">Créditos</TableHead>
                  <TableHead className="text-right">Neto</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.data.items.map((row) => (
                  <TableRow key={row.id} data-testid={`payroll-row-${row.id}`}>
                    <TableCell data-label="Empleado">
                      <Link href={payrollHref(row.id)} className="font-medium hover:underline">
                        {row.employee_name}
                      </Link>
                    </TableCell>
                    <TableCell data-label="Período" className="whitespace-nowrap tabular-nums">
                      {periodLabel(row)}
                    </TableCell>
                    <TableCell data-label="Tipo">{PAYROLL_KIND_LABELS[row.kind]}</TableCell>
                    <TableCell data-label="Estado">
                      <Badge tone={PAYROLL_STATUS_TONES[row.status]}>{PAYROLL_STATUS_LABELS[row.status]}</Badge>
                      {row.in_correction && <span className="block text-xs text-muted-foreground">En corrección</span>}
                      {row.totals_from_draft && row.status === "OPEN" && (
                        <span className="block text-xs text-muted-foreground">Importes del borrador</span>
                      )}
                    </TableCell>
                    <TableCell data-label="Haberes" className="text-right tabular-nums">
                      {formatMoney(row.totals.credits)}
                    </TableCell>
                    <TableCell data-label="Descuentos" className="text-right tabular-nums">
                      {formatMoney(row.totals.deductions)}
                    </TableCell>
                    <TableCell data-label="Créditos" className="text-right tabular-nums">
                      {formatMoney(row.totals.credits_applied)}
                    </TableCell>
                    <TableCell data-label="Neto" className="text-right font-medium tabular-nums">
                      {formatMoney(row.totals.net)}
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
      {creating && <NewPayrollDialog onClose={() => setCreating(false)} />}
    </>
  );
}
