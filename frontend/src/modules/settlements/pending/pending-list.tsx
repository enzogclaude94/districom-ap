"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { HandCoins } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
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
import { deliveryApi, deliveryQueryKey } from "@/modules/settlements/delivery/api";
import { selectClass } from "@/modules/settlements/delivery/form";
import { settlementTitle } from "@/modules/settlements/delivery/labels";

import { ReceiptDialog } from "./actions";
import { pendingApi, pendingQueryKey, type PendingFilters, type PendingState } from "./api";
import { STATE_LABELS, STATE_TONES, ageText, pendingHref, pendingTitle } from "./labels";
import { AlertMarks } from "./pending-detail";
import { AppliedBreakdown } from "./settlement-pendings";

const PAGE_SIZE = 20;

/** "OWED" is the backend default (OPEN and PARTIAL); "ALL" every state. */
type StateFilter = "OWED" | "ALL" | PendingState;

const STATE_FILTERS: { value: StateFilter; label: string }[] = [
  { value: "OWED", label: "Con saldo (pendiente o parcial)" },
  { value: "ALL", label: "Todos" },
  ...(Object.keys(STATE_LABELS) as PendingState[]).map((value) => ({ value, label: STATE_LABELS[value] })),
];

const AGE_FILTERS = [
  { value: 0, label: "Cualquiera" },
  { value: 24, label: "Más de 24 h" },
  { value: 48, label: "Más de 48 h" },
  { value: 168, label: "Más de 7 días" },
  { value: 720, label: "Más de 30 días" },
];

function statesOf(filter: StateFilter): PendingState[] | undefined {
  if (filter === "OWED") return undefined;
  if (filter === "ALL") return Object.keys(STATE_LABELS) as PendingState[];
  return [filter];
}

/** `/rendiciones/pendientes`: what customers still owe, by default with a balance, paginated by the backend. */
export function PendingList() {
  const params = useSearchParams();
  const ids = {
    customer: useId(),
    driver: useId(),
    responsible: useId(),
    state: useId(),
    age: useId(),
    alert: useId(),
  };
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const canOperate = roleAtLeast(moduleRole(me.data, "settlements"), "operator");
  const [customer, setCustomer] = useState("");
  const [driverId, setDriverId] = useState<number | undefined>();
  const [responsibleId, setResponsibleId] = useState<number | undefined>();
  const [state, setState] = useState<StateFilter>("OWED");
  const [minAge, setMinAge] = useState(0);
  const [alertOnly, setAlertOnly] = useState(() => params.get("alerta") === "1");
  const [page, setPage] = useState(0);
  const [collecting, setCollecting] = useState<number | null>(null);

  const filters: PendingFilters = {
    state: statesOf(state),
    customer: customer.trim() || undefined,
    driver_id: driverId,
    responsible_employee_id: responsibleId,
    min_age_hours: minAge || undefined,
    alert: alertOnly || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const list = useQuery({
    queryKey: [...pendingQueryKey, "list", filters],
    queryFn: () => pendingApi.list(filters),
    placeholderData: keepPreviousData,
  });
  const employees = useQuery({ queryKey: [...deliveryQueryKey, "employees"], queryFn: deliveryApi.employees });
  const filtered = Boolean(customer.trim() || driverId || responsibleId || state !== "OWED" || minAge || alertOnly);
  const filter =
    <T,>(set: (value: T) => void) =>
    (value: T) => {
      set(value);
      setPage(0);
    };
  const people = employees.data ?? [];

  return (
    <>
      <PageHeader
        title="Pendientes de cobro"
        description="Lo que los clientes quedaron debiendo en las rendiciones cerradas, cuánto se cobró y cuánto falta."
      />

      <Card>
        <CardContent className="grid gap-3 pt-5 sm:grid-cols-2 lg:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor={ids.customer}>Cliente</Label>
            <Input
              id={ids.customer}
              type="search"
              placeholder="Buscar cliente…"
              value={customer}
              onChange={(event) => filter(setCustomer)(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.driver}>Repartidor</Label>
            <select
              id={ids.driver}
              className={selectClass}
              value={driverId ?? ""}
              onChange={(event) => filter(setDriverId)(event.target.value ? Number(event.target.value) : undefined)}
            >
              <option value="">Todos</option>
              {people.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.full_name}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.responsible}>Responsable</Label>
            <select
              id={ids.responsible}
              className={selectClass}
              value={responsibleId ?? ""}
              onChange={(event) =>
                filter(setResponsibleId)(event.target.value ? Number(event.target.value) : undefined)
              }
            >
              <option value="">Todos</option>
              {people.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.full_name}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.state}>Estado</Label>
            <select
              id={ids.state}
              className={selectClass}
              value={state}
              onChange={(event) => filter(setState)(event.target.value as StateFilter)}
            >
              {STATE_FILTERS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.age}>Antigüedad</Label>
            <select
              id={ids.age}
              className={selectClass}
              value={minAge}
              onChange={(event) => filter(setMinAge)(Number(event.target.value))}
            >
              {AGE_FILTERS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-end pb-2">
            <label htmlFor={ids.alert} className="flex items-center gap-2 text-sm">
              <input
                id={ids.alert}
                type="checkbox"
                checked={alertOnly}
                onChange={(event) => filter(setAlertOnly)(event.target.checked)}
              />
              Solo en alerta
            </label>
          </div>
        </CardContent>
      </Card>

      {list.isPending ? (
        <div className="space-y-2" aria-label="Cargando pendientes">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.data.total === 0 ? (
        <EmptyState
          icon={HandCoins}
          title={filtered ? "No hay pendientes con esos filtros" : "No hay pendientes con saldo"}
          description={
            filtered
              ? "Probá con otro cliente, otro estado o sin el filtro de alerta."
              : "Cuando una rendición se cierre con pendientes, van a aparecer acá hasta que se cobren o se resuelvan."
          }
        />
      ) : (
        <Card>
          <CardContent className="pt-3">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Cliente</TableHead>
                  <TableHead>Rendición</TableHead>
                  <TableHead>Repartidor</TableHead>
                  <TableHead>Responsable</TableHead>
                  <TableHead className="text-right">Importe vigente</TableHead>
                  <TableHead className="text-right">Cobrado</TableHead>
                  <TableHead className="text-right">Resuelto</TableHead>
                  <TableHead className="text-right">Saldo</TableHead>
                  <TableHead>Antigüedad</TableHead>
                  <TableHead>Estado</TableHead>
                  <TableHead>Fecha esperada</TableHead>
                  <TableHead>
                    <span className="sr-only">Acciones</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.data.items.map((item) => {
                  const owes = item.state === "OPEN" || item.state === "PARTIAL";
                  return (
                    <TableRow key={item.id} data-testid={`pending-item-${item.id}`}>
                      <TableCell>
                        <Link href={pendingHref(item.id)} className="font-medium text-primary hover:underline">
                          {item.customer_in_effect ?? item.customer_name}
                        </Link>
                        <span className="block text-xs text-muted-foreground">{pendingTitle(item.id)}</span>
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        <Link href={`/rendiciones/repartos/${item.settlement_id}`} className="hover:underline">
                          {settlementTitle(item.settlement_sequence_number)} ·{" "}
                          {formatDate(item.settlement_business_date)}
                        </Link>
                      </TableCell>
                      <TableCell className="whitespace-nowrap">{item.driver_name}</TableCell>
                      <TableCell className="whitespace-nowrap">{item.responsible_name}</TableCell>
                      <TableCell className="text-right whitespace-nowrap tabular-nums">
                        {item.amount_in_effect ? formatMoney(item.amount_in_effect) : "—"}
                      </TableCell>
                      <TableCell className="text-right whitespace-nowrap tabular-nums">
                        {formatMoney(item.collected)}
                      </TableCell>
                      <TableCell className="text-right whitespace-nowrap tabular-nums">
                        {formatMoney(item.resolved)}
                      </TableCell>
                      <TableCell className="text-right font-medium whitespace-nowrap tabular-nums">
                        {item.balance !== null ? formatMoney(item.balance) : "—"}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        {ageText(item.age_hours)}
                        <AlertMarks pending={item} />
                      </TableCell>
                      <TableCell>
                        <Badge tone={STATE_TONES[item.state]}>{STATE_LABELS[item.state]}</Badge>
                        {item.state === "PARTIAL" && <AppliedBreakdown pending={item} className="whitespace-nowrap" />}
                      </TableCell>
                      <TableCell className="whitespace-nowrap tabular-nums">{formatDate(item.expected_date)}</TableCell>
                      <TableCell className="whitespace-nowrap">
                        {canOperate && owes && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setCollecting(item.id)}
                            aria-label={`Registrar cobro de ${item.customer_in_effect ?? item.customer_name}`}
                          >
                            Registrar cobro
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
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

      {collecting !== null && <ReceiptDialog pendingId={collecting} onClose={() => setCollecting(null)} />}
    </>
  );
}
