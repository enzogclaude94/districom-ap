"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ClipboardList, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
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
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { cn } from "@/lib/cn";
import { formatDate, formatMoney } from "@/lib/format";

import { deliveryApi, deliveryQueryKey, type FinancialState, type SettlementFilters } from "./api";
import { selectClass } from "./form";
import {
  EXPECTED_UPDATE_BADGE,
  FINANCIAL_STATE_LABELS,
  FINANCIAL_STATE_TONES,
  differenceText,
  differenceTone,
  settlementTitle,
} from "./labels";
import { NewSettlementDialog } from "./new-settlement-dialog";

const PAGE_SIZE = 20;

/** `/rendiciones/repartos`: settlements by date, driver and state, with their difference. */
export function DeliveryList() {
  const router = useRouter();
  const ids = { from: useId(), to: useId(), driver: useId(), state: useId() };
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const canOperate = roleAtLeast(moduleRole(me.data, "settlements"), "operator");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [employeeId, setEmployeeId] = useState<number | undefined>();
  const [state, setState] = useState<FinancialState | undefined>();
  const [page, setPage] = useState(0);
  const [creating, setCreating] = useState(false);

  const filters: SettlementFilters = {
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    employee_id: employeeId,
    financial_state: state,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const list = useQuery({
    queryKey: [...deliveryQueryKey, "list", filters],
    queryFn: () => deliveryApi.list(filters),
    placeholderData: keepPreviousData,
  });
  const employees = useQuery({ queryKey: [...deliveryQueryKey, "employees"], queryFn: deliveryApi.employees });
  const filtered = Boolean(dateFrom || dateTo || employeeId || state);
  const filter =
    <T,>(set: (value: T) => void) =>
    (value: T) => {
      set(value);
      setPage(0);
    };

  return (
    <>
      <PageHeader
        title="Rendiciones de reparto"
        description="Qué debía rendir cada repartidor, qué rindió y la diferencia de cada rendición."
        actions={
          canOperate && (
            <Button onClick={() => setCreating(true)}>
              <Plus aria-hidden />
              Nueva rendición
            </Button>
          )
        }
      />

      <Card>
        <CardContent className="grid gap-3 pt-5 sm:grid-cols-2 lg:grid-cols-4">
          <div className="space-y-1.5">
            <Label htmlFor={ids.from}>Desde</Label>
            <Input
              id={ids.from}
              type="date"
              value={dateFrom}
              onChange={(event) => filter(setDateFrom)(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.to}>Hasta</Label>
            <Input id={ids.to} type="date" value={dateTo} onChange={(event) => filter(setDateTo)(event.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.driver}>Repartidor</Label>
            <select
              id={ids.driver}
              className={selectClass}
              value={employeeId ?? ""}
              onChange={(event) => filter(setEmployeeId)(event.target.value ? Number(event.target.value) : undefined)}
            >
              <option value="">Todos</option>
              {(employees.data ?? []).map((employee) => (
                <option key={employee.id} value={employee.id}>
                  {employee.full_name}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.state}>Estado</Label>
            <select
              id={ids.state}
              className={selectClass}
              value={state ?? ""}
              onChange={(event) => filter(setState)((event.target.value || undefined) as FinancialState | undefined)}
            >
              <option value="">Todos</option>
              {(Object.keys(FINANCIAL_STATE_LABELS) as FinancialState[]).map((value) => (
                <option key={value} value={value}>
                  {FINANCIAL_STATE_LABELS[value]}
                </option>
              ))}
            </select>
          </div>
        </CardContent>
      </Card>

      {list.isPending ? (
        <div className="space-y-2" aria-label="Cargando rendiciones">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.data.total === 0 ? (
        <EmptyState
          icon={ClipboardList}
          title={filtered ? "No hay rendiciones con esos filtros" : "Todavía no hay rendiciones"}
          description={
            filtered
              ? "Probá con otras fechas, otro repartidor u otro estado."
              : canOperate
                ? "Creá la primera con «Nueva rendición»."
                : "Cuando se carguen rendiciones de reparto, van a aparecer acá."
          }
        />
      ) : (
        <Card>
          <CardContent className="pt-3">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Fecha</TableHead>
                  <TableHead>Repartidor</TableHead>
                  <TableHead>Rendición</TableHead>
                  <TableHead className="text-right">Esperado</TableHead>
                  <TableHead className="text-right">Justificado</TableHead>
                  <TableHead>Diferencia</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead>
                    <span className="sr-only">Abrir</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.data.items.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="whitespace-nowrap tabular-nums">{formatDate(item.business_date)}</TableCell>
                    <TableCell className="whitespace-nowrap">{item.employee_name}</TableCell>
                    <TableCell className="whitespace-nowrap">
                      {settlementTitle(item.sequence_number)}
                      {item.external_reference && (
                        <span className="block text-xs text-muted-foreground">{item.external_reference}</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right whitespace-nowrap tabular-nums">
                      {item.expected_amount ? (
                        formatMoney(item.expected_amount)
                      ) : (
                        <span className="text-warning">Sin cargar</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right whitespace-nowrap tabular-nums">
                      {formatMoney(item.justified)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      <Badge tone={differenceTone(item.difference)}>{differenceText(item.difference)}</Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        <Badge tone={FINANCIAL_STATE_TONES[item.financial_state]}>
                          {FINANCIAL_STATE_LABELS[item.financial_state]}
                        </Badge>
                        {item.expected_update_pending && (
                          <Badge tone="warning" data-testid="expected-update-pending">
                            {EXPECTED_UPDATE_BADGE}
                          </Badge>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Link
                        href={`/rendiciones/repartos/${item.id}`}
                        className="whitespace-nowrap text-primary hover:underline"
                        aria-label={`Abrir ${settlementTitle(item.sequence_number)} de ${item.employee_name} del ${formatDate(item.business_date)}`}
                      >
                        Abrir
                      </Link>
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

      {creating && (
        <NewSettlementDialog
          onClose={() => setCreating(false)}
          onCreated={(settlement) => router.push(`/rendiciones/repartos/${settlement.id}`)}
        />
      )}
    </>
  );
}
