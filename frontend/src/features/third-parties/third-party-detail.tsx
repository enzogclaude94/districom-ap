"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { thirdPartiesApi, thirdPartiesQueryKey } from "@/lib/api/third-parties";

import { ThirdPartyHistory } from "./third-party-history";

/**
 * A third party's ficha: its master data and "Cambios del maestro". What a module records with it (payments,
 * in Rendiciones) is composed by the route below this, never mixed with the master's own changes.
 */
export function ThirdPartyDetailView({ thirdPartyId }: { thirdPartyId: number }) {
  const detail = useQuery({
    queryKey: [...thirdPartiesQueryKey, thirdPartyId],
    queryFn: () => thirdPartiesApi.get(thirdPartyId),
  });
  return (
    <>
      <Link href="/terceros" className="inline-flex items-center gap-1 text-sm text-primary hover:underline">
        <ArrowLeft className="size-4" aria-hidden />
        Terceros
      </Link>
      {detail.isPending ? (
        <Skeleton className="h-24 w-full" aria-label="Cargando tercero" />
      ) : detail.isError ? (
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      ) : (
        <>
          <PageHeader
            title={detail.data.name}
            description={detail.data.tax_id_display ? `CUIT ${detail.data.tax_id_display}` : "Sin CUIT"}
            actions={detail.data.active ? <Badge tone="success">Activo</Badge> : <Badge>Desactivado</Badge>}
          />
          {detail.data.notes && (
            <Card>
              <CardHeader>
                <CardTitle>Notas</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm break-words whitespace-pre-line">{detail.data.notes}</p>
              </CardContent>
            </Card>
          )}
          <Card>
            <CardHeader>
              <CardTitle>Cambios del maestro</CardTitle>
            </CardHeader>
            <CardContent>
              <ThirdPartyHistory history={detail.data.history} />
            </CardContent>
          </Card>
        </>
      )}
    </>
  );
}
