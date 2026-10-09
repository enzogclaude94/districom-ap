"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Fuel } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useId, useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { vehiclesApi, vehiclesQueryKey } from "@/lib/api/vehicles";
import { selectClass } from "@/modules/settlements/delivery/form";

import { fuelApi, fuelQueryKey, type FuelFilters, type FuelOrigin, type FuelState } from "./api";
import { FuelLoadCards, FuelLoadTable, FuelSummaryBar, Pager } from "./fuel-loads-view";
import { ORIGIN_LABELS, STATE_LABELS } from "./labels";

const PAGE_SIZE = 25;

function positiveId(raw: string | null): number | undefined {
  const value = Number(raw);
  return raw && Number.isSafeInteger(value) && value > 0 ? value : undefined;
}

/**
 * `/rendiciones/combustible` (R5-4): every fuel load, of a settlement or a direct expense, with its state.
 * Filters, page and summary come from the backend; the screen computes nothing.
 */
export function FuelHistory() {
  const params = useSearchParams();
  const ids = {
    vehicle: useId(),
    employee: useId(),
    origin: useId(),
    state: useId(),
    from: useId(),
    to: useId(),
  };
  const [vehicleId, setVehicleId] = useState<number | undefined>(() => positiveId(params.get("vehiculo")));
  const [employeeId, setEmployeeId] = useState<number | undefined>();
  const [originFilter, setOrigin] = useState<FuelOrigin | "">("");
  const [state, setState] = useState<FuelState | "">("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(0);

  const vehicles = useQuery({ queryKey: [...vehiclesQueryKey, "list", "all"], queryFn: () => vehiclesApi.list(true) });
  const employees = useQuery({
    queryKey: [...fuelQueryKey, "employees"],
    queryFn: fuelApi.employees,
    staleTime: 0,
    refetchOnMount: "always",
  });
  const filters: FuelFilters = {
    vehicle_id: vehicleId,
    employee_id: employeeId,
    origin: originFilter || undefined,
    state: state || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const anyFilter = [vehicleId, employeeId, originFilter, state, dateFrom, dateTo].some(
    (v) => v !== undefined && v !== "",
  );
  const list = useQuery({
    queryKey: [...fuelQueryKey, "list", filters],
    queryFn: () => fuelApi.list(filters),
    placeholderData: keepPreviousData,
    staleTime: 0,
    refetchOnMount: "always",
  });
  const filtered =
    <T,>(set: (value: T) => void) =>
    (value: T) => {
      set(value);
      setPage(0);
    };
  const clear = () => {
    setVehicleId(undefined);
    setEmployeeId(undefined);
    setOrigin("");
    setState("");
    setDateFrom("");
    setDateTo("");
    setPage(0);
  };

  return (
    <>
      <PageHeader
        title="Combustible"
        description="Todas las cargas de combustible: las de rendiciones de reparto y las pagadas como gasto directo desde una caja o el banco."
      />

      <Card>
        <CardContent className="grid gap-3 pt-4 sm:grid-cols-2 lg:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor={ids.vehicle}>Vehículo</Label>
            <select
              id={ids.vehicle}
              className={selectClass}
              value={vehicleId ?? ""}
              onChange={(e) => filtered(setVehicleId)(positiveId(e.target.value))}
            >
              <option value="">Todos</option>
              {(vehicles.data ?? []).map((v) => (
                <option key={v.id} value={v.id}>
                  {v.plate_display} · {v.description}
                  {v.active ? "" : " (desactivado)"}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.employee}>Empleado</Label>
            <select
              id={ids.employee}
              className={selectClass}
              value={employeeId ?? ""}
              onChange={(e) => filtered(setEmployeeId)(positiveId(e.target.value))}
            >
              <option value="">Todos</option>
              {(employees.data ?? []).map((e) => (
                <option key={e.id} value={e.id}>
                  {e.full_name}
                  {e.active ? "" : " (inactivo)"}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.origin}>Origen</Label>
            <select
              id={ids.origin}
              className={selectClass}
              value={originFilter}
              onChange={(e) => filtered(setOrigin)(e.target.value as FuelOrigin | "")}
            >
              <option value="">Todos</option>
              {(Object.keys(ORIGIN_LABELS) as FuelOrigin[]).map((value) => (
                <option key={value} value={value}>
                  {ORIGIN_LABELS[value]}
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
              onChange={(e) => filtered(setState)(e.target.value as FuelState | "")}
            >
              <option value="">Todos</option>
              {(Object.keys(STATE_LABELS) as FuelState[]).map((value) => (
                <option key={value} value={value}>
                  {STATE_LABELS[value]}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.from}>Desde</Label>
            <Input
              id={ids.from}
              type="date"
              className="tabular-nums"
              value={dateFrom}
              onChange={(e) => filtered(setDateFrom)(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.to}>Hasta</Label>
            <Input
              id={ids.to}
              type="date"
              className="tabular-nums"
              value={dateTo}
              onChange={(e) => filtered(setDateTo)(e.target.value)}
            />
          </div>
          {anyFilter && (
            <div className="sm:col-span-2 lg:col-span-3">
              <Button variant="ghost" size="sm" onClick={clear}>
                Limpiar filtros
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {list.isPending ? (
        <div className="space-y-2" aria-label="Cargando combustible">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : (
        <>
          <FuelSummaryBar summary={list.data.summary} />
          {list.data.total === 0 ? (
            anyFilter ? (
              <EmptyState
                icon={Fuel}
                title="No existen cargas con estos filtros"
                description="Probá con otro vehículo, empleado, estado o rango de fechas."
              />
            ) : (
              <EmptyState
                icon={Fuel}
                title="No existen cargas de combustible"
                description="Se registran en una rendición de reparto o como gasto directo desde Cajas."
              />
            )
          ) : (
            <Card>
              <CardContent className="pt-4">
                <FuelLoadTable rows={list.data.items} />
                <FuelLoadCards rows={list.data.items} />
                <Pager
                  page={page}
                  size={PAGE_SIZE}
                  shown={list.data.items.length}
                  total={list.data.total}
                  onPage={setPage}
                />
              </CardContent>
            </Card>
          )}
        </>
      )}
    </>
  );
}
