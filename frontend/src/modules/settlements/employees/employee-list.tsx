"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Users } from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/cn";
import { formatDate, formatMoney } from "@/lib/format";

import { employeeFinanceApi, employeeFinanceQueryKey, type EmployeeFinanceRow } from "./api";
import { READING_LABELS, READING_TONES, SALARY_NOTICE, employeeFinanceHref } from "./labels";

const PAGE_SIZE = 25;

export function TermSummary({ row }: { row: Pick<EmployeeFinanceRow, "current_term" | "next_term"> }) {
  const { current_term: current, next_term: next } = row;
  return (
    <span>
      {current ? (
        <span className="tabular-nums">
          {formatMoney(current.base_salary)}{" "}
          <span className="text-muted-foreground">desde {formatDate(current.valid_from)}</span>
        </span>
      ) : (
        <span className="text-muted-foreground">Sin condición</span>
      )}
      {next && (
        <span className="block text-xs text-muted-foreground">
          Desde {formatDate(next.valid_from)}: {formatMoney(next.base_salary)}
        </span>
      )}
    </span>
  );
}

/** `/rendiciones/empleados`: each employee with their internal balance and current salary term. */
export function EmployeeFinanceList() {
  const searchId = useId();
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const params = { q: q.trim() || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE };
  const list = useQuery({
    queryKey: [...employeeFinanceQueryKey, "list", params],
    queryFn: () => employeeFinanceApi.list(params),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHeader
        title="Empleados"
        description="Cuenta interna de cada empleado con la empresa y su condición salarial. Área reservada."
      />
      <p className="text-sm text-muted-foreground">{SALARY_NOTICE}</p>

      <Card>
        <CardContent className="pt-5">
          <div className="max-w-sm space-y-1.5">
            <Label htmlFor={searchId}>Empleado</Label>
            <Input
              id={searchId}
              type="search"
              placeholder="Buscar por nombre…"
              value={q}
              onChange={(event) => {
                setQ(event.target.value);
                setPage(0);
              }}
            />
          </div>
        </CardContent>
      </Card>

      {list.isPending ? (
        <div className="space-y-2" aria-label="Cargando empleados">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.data.total === 0 ? (
        <EmptyState
          icon={Users}
          title={q.trim() ? "No hay empleados con ese nombre" : "Todavía no hay empleados"}
          description={
            q.trim() ? "Probá con otra parte del nombre." : "Los empleados se cargan en Empleados de la plataforma."
          }
        />
      ) : (
        <Card>
          <CardContent className="pt-3">
            <Table stacked>
              <TableHeader>
                <TableRow>
                  <TableHead>Empleado</TableHead>
                  <TableHead className="text-right">Saldo cuenta interna</TableHead>
                  <TableHead>Lectura</TableHead>
                  <TableHead>Sueldo base interno</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.data.items.map((row) => (
                  <TableRow key={row.employee_id} data-testid={`employee-finance-${row.employee_id}`}>
                    <TableCell data-label="Empleado">
                      <Link
                        href={employeeFinanceHref(row.employee_id)}
                        className="font-medium text-primary hover:underline"
                      >
                        {row.full_name}
                      </Link>
                      {!row.active && <span className="ml-2 text-xs text-muted-foreground">(inactivo)</span>}
                    </TableCell>
                    <TableCell data-label="Saldo" className="text-right font-medium whitespace-nowrap tabular-nums">
                      {formatMoney(row.balance)}
                    </TableCell>
                    <TableCell data-label="Lectura">
                      <Badge tone={READING_TONES[row.balance_reading]}>{READING_LABELS[row.balance_reading]}</Badge>
                    </TableCell>
                    <TableCell data-label="Sueldo base">
                      <TermSummary row={row} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <div
              className={cn(
                "mt-3 flex items-center justify-between gap-2 text-sm text-muted-foreground",
                list.isPlaceholderData && "opacity-60",
              )}
            >
              <span className="tabular-nums">
                {page * PAGE_SIZE + 1}–{page * PAGE_SIZE + list.data.items.length} de {list.data.total}
              </span>
              <div className="flex gap-2">
                <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
                  Anterior
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={(page + 1) * PAGE_SIZE >= list.data.total}
                  onClick={() => setPage(page + 1)}
                >
                  Siguiente
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </>
  );
}
