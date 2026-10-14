"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Store } from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { outletsApi, outletsQueryKey } from "@/lib/api/outlets";
import { cn } from "@/lib/cn";
import { formatDate, formatMoney } from "@/lib/format";
import type { FinancialState } from "@/modules/settlements/delivery/api";
import { selectClass } from "@/modules/settlements/delivery/form";
import {
  FINANCIAL_STATE_LABELS,
  FINANCIAL_STATE_TONES,
  differenceText,
  differenceTone,
  settlementTitle,
} from "@/modules/settlements/delivery/labels";

import { outletSettlementApi, outletSettlementQueryKey, type OutletSettlementFilters } from "./api";

const PAGE_SIZE = 20;

/**
 * «Todas las rendiciones» of Rendiciones › Bocas: boca settlements by date, boca and state (R7-2). An open one
 * shows no difference: it exists only once the settlement is closed (R7-3).
 */
export function OutletSettlementList({ initialOutletId }: { initialOutletId?: number }) {
  const ids = { from: useId(), to: useId(), outlet: useId(), state: useId() };
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const canOperate = roleAtLeast(moduleRole(me.data, "settlements"), "operator");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [outletId, setOutletId] = useState<number | undefined>(initialOutletId);
  const [state, setState] = useState<FinancialState | undefined>();
  const [page, setPage] = useState(0);

  const filters: OutletSettlementFilters = {
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    outlet_id: outletId,
    financial_state: state,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const list = useQuery({
    queryKey: [...outletSettlementQueryKey, "list", filters],
    queryFn: () => outletSettlementApi.list(filters),
    placeholderData: keepPreviousData,
  });
  // Deactivated bocas too: their past settlements are still listed.
  const outlets = useQuery({ queryKey: [...outletsQueryKey, "all"], queryFn: () => outletsApi.list(true) });
  const filtered = Boolean(dateFrom || dateTo || outletId || state);
  const filter =
    <T,>(set: (value: T) => void) =>
    (value: T) => {
      set(value);
      setPage(0);
    };

  return (
    <>
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
            <Label htmlFor={ids.outlet}>Boca</Label>
            <select
              id={ids.outlet}
              className={selectClass}
              value={outletId ?? ""}
              onChange={(event) => filter(setOutletId)(event.target.value ? Number(event.target.value) : undefined)}
            >
              <option value="">Todas</option>
              {(outlets.data ?? []).map((outlet) => (
                <option key={outlet.id} value={outlet.id}>
                  {outlet.name}
                  {!outlet.active && " (desactivada)"}
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
          icon={Store}
          title={filtered ? "No hay rendiciones con esos filtros" : "Todavía no hay rendiciones de boca"}
          description={
            filtered
              ? "Probá con otras fechas, otra boca u otro estado."
              : canOperate
                ? "Creá la primera con «Nueva rendición»."
                : "Cuando se carguen rendiciones de boca, van a aparecer acá."
          }
        />
      ) : (
        <Card>
          <CardContent className="pt-3">
            <Table stacked aria-label="Rendiciones de boca">
              <TableHeader>
                <TableRow>
                  <TableHead>Fecha</TableHead>
                  <TableHead>Boca</TableHead>
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
                    <TableCell data-label="Fecha" className="whitespace-nowrap tabular-nums">
                      {formatDate(item.business_date)}
                    </TableCell>
                    <TableCell data-label="Boca" className="whitespace-nowrap">
                      {item.outlet_name}
                    </TableCell>
                    <TableCell data-label="Rendición" className="whitespace-nowrap">
                      {settlementTitle(item.sequence_number)}
                      {item.external_reference && (
                        <span className="block text-xs text-muted-foreground">{item.external_reference}</span>
                      )}
                    </TableCell>
                    <TableCell data-label="Esperado" className="text-right whitespace-nowrap tabular-nums">
                      {item.expected_amount === null ? (
                        <span className="text-warning">Sin cargar</span>
                      ) : (
                        formatMoney(item.expected_amount)
                      )}
                    </TableCell>
                    <TableCell data-label="Justificado" className="text-right whitespace-nowrap tabular-nums">
                      {formatMoney(item.justified)}
                    </TableCell>
                    <TableCell data-label="Diferencia" className="whitespace-nowrap">
                      {item.difference === null ? (
                        "—"
                      ) : (
                        <Badge tone={differenceTone(item.difference)}>{differenceText(item.difference)}</Badge>
                      )}
                    </TableCell>
                    <TableCell data-label="Estado" className="whitespace-nowrap">
                      <Badge tone={FINANCIAL_STATE_TONES[item.financial_state]}>
                        {FINANCIAL_STATE_LABELS[item.financial_state]}
                      </Badge>
                      {item.administrative_zero && (
                        <span className="block text-xs text-muted-foreground">Cierre en cero</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <Link
                        href={`/rendiciones/boca/${item.id}`}
                        className="whitespace-nowrap text-primary hover:underline"
                        aria-label={`Abrir ${settlementTitle(item.sequence_number)} de ${item.outlet_name} del ${formatDate(item.business_date)}`}
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
    </>
  );
}
