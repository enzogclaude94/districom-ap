"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { authApi, currentUserQueryKey, moduleRole } from "@/lib/api/auth";

import { fuelApi, fuelHistoryHref, fuelQueryKey, type FuelFilters } from "./api";
import { FuelLoadCards, FuelLoadTable, FuelSummaryBar, Pager } from "./fuel-loads-view";

const PAGE_SIZE = 10;

/**
 * "Cargas de combustible" of a vehicle's page: the same endpoint as the history, filtered by the vehicle.
 * Only for users with access to Rendiciones; everyone else sees just the vehicle.
 */
export function VehicleFuelLoads({ vehicleId }: { vehicleId: number }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const allowed = moduleRole(me.data, "settlements") !== null;
  const [page, setPage] = useState(0);
  const filters: FuelFilters = { vehicle_id: vehicleId, limit: PAGE_SIZE, offset: page * PAGE_SIZE };
  const list = useQuery({
    queryKey: [...fuelQueryKey, "list", filters],
    queryFn: () => fuelApi.list(filters),
    enabled: allowed,
    placeholderData: keepPreviousData,
    staleTime: 0,
    refetchOnMount: "always",
  });
  if (!allowed) return null;

  return (
    <Card data-testid="vehicle-fuel-loads">
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>Cargas de combustible</CardTitle>
        <Link href={fuelHistoryHref(vehicleId)} className="text-sm text-primary hover:underline">
          Ver historial completo de combustible
        </Link>
      </CardHeader>
      <CardContent className="space-y-3">
        {list.isPending ? (
          <Skeleton className="h-24 w-full" aria-label="Cargando cargas de combustible" />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : list.data.total === 0 ? (
          <EmptyState
            title="No existen cargas de este vehículo"
            description="Aparecen acá al cargarlas en una rendición o como gasto directo."
          />
        ) : (
          <>
            <FuelSummaryBar summary={list.data.summary} />
            <FuelLoadTable rows={list.data.items} withVehicle={false} />
            <FuelLoadCards rows={list.data.items} withVehicle={false} />
            <Pager
              page={page}
              size={PAGE_SIZE}
              shown={list.data.items.length}
              total={list.data.total}
              onPage={setPage}
            />
          </>
        )}
      </CardContent>
    </Card>
  );
}
