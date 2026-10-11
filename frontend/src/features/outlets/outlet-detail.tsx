"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { outletsApi, outletsQueryKey } from "@/lib/api/outlets";

import { OutletHistory } from "./outlet-history";

export const outletHref = (id: number) => `/bocas/${id}`;

/**
 * A boca's page: its master data and "Cambios del maestro". Who answers for it (Rendiciones) is composed by
 * the route below this, never mixed with the master's own changes.
 */
export function OutletDetail({ outletId }: { outletId: number }) {
  const detail = useQuery({ queryKey: [...outletsQueryKey, outletId], queryFn: () => outletsApi.get(outletId) });
  return (
    <>
      <Link href="/bocas" className="inline-flex items-center gap-1 text-sm text-primary hover:underline">
        <ArrowLeft className="size-4" aria-hidden />
        Bocas de expendio
      </Link>
      {detail.isPending ? (
        <Skeleton className="h-24 w-full" aria-label="Cargando boca" />
      ) : detail.isError ? (
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      ) : (
        <>
          <PageHeader
            title={`${detail.data.code} · ${detail.data.name}`}
            description={detail.data.description ?? undefined}
            actions={detail.data.active ? <Badge tone="success">Activa</Badge> : <Badge>Desactivada</Badge>}
          />
          <Card>
            <CardHeader>
              <CardTitle>Cambios del maestro</CardTitle>
            </CardHeader>
            <CardContent>
              <OutletHistory history={detail.data.history} />
            </CardContent>
          </Card>
        </>
      )}
    </>
  );
}
