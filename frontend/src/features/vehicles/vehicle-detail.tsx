"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { vehiclesApi, vehiclesQueryKey } from "@/lib/api/vehicles";

import { VehicleHistory } from "./vehicle-history";

export const vehicleHref = (id: number) => `/vehiculos/${id}`;

/**
 * A vehicle's page: its master data and "Cambios del maestro". What modules record about it (fuel, in
 * Rendiciones) is composed by the route below this, never mixed with the master's own changes.
 */
export function VehicleDetail({ vehicleId }: { vehicleId: number }) {
  const detail = useQuery({ queryKey: [...vehiclesQueryKey, vehicleId], queryFn: () => vehiclesApi.get(vehicleId) });
  return (
    <>
      <Link href="/vehiculos" className="inline-flex items-center gap-1 text-sm text-primary hover:underline">
        <ArrowLeft className="size-4" aria-hidden />
        Vehículos
      </Link>
      {detail.isPending ? (
        <Skeleton className="h-24 w-full" aria-label="Cargando vehículo" />
      ) : detail.isError ? (
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      ) : (
        <>
          <PageHeader
            title={detail.data.plate_display}
            description={detail.data.description}
            actions={detail.data.active ? <Badge tone="success">Activo</Badge> : <Badge>Desactivado</Badge>}
          />
          <Card>
            <CardHeader>
              <CardTitle>Cambios del maestro</CardTitle>
            </CardHeader>
            <CardContent>
              <VehicleHistory history={detail.data.history} />
            </CardContent>
          </Card>
        </>
      )}
    </>
  );
}
