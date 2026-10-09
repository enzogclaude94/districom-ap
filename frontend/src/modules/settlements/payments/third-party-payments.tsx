"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { authApi, currentUserQueryKey, moduleRole } from "@/lib/api/auth";
import { formatMoney } from "@/lib/format";
import { Pager } from "@/modules/settlements/fuel/fuel-loads-view";

import { paymentApi, paymentQueryKey, type PaymentFilters } from "./api";
import { PaymentCards, PaymentTable } from "./payment-rows";

const PAGE_SIZE = 10;

/**
 * "Pagos" of a third party's ficha: the same endpoint as the list, filtered by the third party. Only for
 * users with access to Rendiciones. A sum of what was paid, never a balance owed.
 */
export function ThirdPartyPayments({ thirdPartyId }: { thirdPartyId: number }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const allowed = moduleRole(me.data, "settlements") !== null;
  const [page, setPage] = useState(0);
  const filters: PaymentFilters = { third_party_id: thirdPartyId, limit: PAGE_SIZE, offset: page * PAGE_SIZE };
  const list = useQuery({
    queryKey: [...paymentQueryKey, "list", filters],
    queryFn: () => paymentApi.list(filters),
    enabled: allowed,
    placeholderData: keepPreviousData,
  });
  if (!allowed) return null;

  return (
    <Card data-testid="third-party-payments">
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <div className="space-y-1">
          <CardTitle>Pagos</CardTitle>
          {list.data && list.data.total > 0 && (
            <CardDescription className="tabular-nums">
              Pagado (vigente): {formatMoney(list.data.amount_in_effect)}. Es lo pagado, no un saldo adeudado.
            </CardDescription>
          )}
        </div>
        <Link href={`/rendiciones/pagos?tercero=${thirdPartyId}`} className="text-sm text-primary hover:underline">
          Ver en Pagos
        </Link>
      </CardHeader>
      <CardContent className="space-y-3">
        {list.isPending ? (
          <Skeleton className="h-24 w-full" aria-label="Cargando pagos" />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : list.data.total === 0 ? (
          <EmptyState
            title="No hay pagos a este tercero"
            description="Aparecen acá al registrarlos en Rendiciones › Pagos."
          />
        ) : (
          <>
            <PaymentTable rows={list.data.items} withThirdParty={false} />
            <PaymentCards rows={list.data.items} withThirdParty={false} />
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
