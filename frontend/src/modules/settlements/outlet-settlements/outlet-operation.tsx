"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { authApi, currentUserQueryKey, moduleRole } from "@/lib/api/auth";
import { formatDate, formatMoney } from "@/lib/format";
import { settlementTitle } from "@/modules/settlements/delivery/labels";

import { outletSettlementApi, outletSettlementQueryKey, type OutletSettlementRef } from "./api";
import { BOARD_STATE_LABELS, boardState } from "./outlet-board";

const settlementLink = (item: OutletSettlementRef) => (
  <Link href={`/rendiciones/boca/${item.id}`} className="text-primary hover:underline">
    {settlementTitle(item.sequence_number)} del {formatDate(item.business_date)}
  </Link>
);

/**
 * «Operación de Rendiciones» on a boca's ficha (R7-4), for users with access to Rendiciones only: its open
 * settlements, its last one, the cash delivered that no close consolidated yet and the way to its history.
 * The ficha stays the boca's master record; the daily board is Rendiciones › Bocas.
 */
export function OutletOperation({ outletId }: { outletId: number }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const role = moduleRole(me.data, "settlements");
  const summary = useQuery({
    queryKey: [...outletSettlementQueryKey, "outlet-summary", outletId],
    queryFn: () => outletSettlementApi.summary(outletId),
    enabled: role !== null,
  });
  if (role === null || !summary.data) return null;
  const data = summary.data;
  const count = data.open_count;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Operación de Rendiciones</CardTitle>
        <CardDescription>Rendiciones de esta boca. El tablero del día está en Rendiciones › Bocas.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div>
          <p data-testid="outlet-open-settlements">
            {count === 0
              ? "Sin rendiciones abiertas."
              : count === 1
                ? "1 rendición abierta."
                : `${count} rendiciones abiertas.`}
          </p>
          {data.open_settlements.length > 0 && (
            <ul className="mt-1 space-y-1">
              {data.open_settlements.map((item) => (
                <li key={item.id} className="flex flex-wrap items-center gap-2">
                  {settlementLink(item)}
                  <Badge tone={item.financial_state === "BEING_CORRECTED" ? "warning" : "info"}>
                    {BOARD_STATE_LABELS[boardState(item)]}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
        <p data-testid="outlet-last-settlement">
          Última rendición:{" "}
          {data.last_settlement ? (
            <>
              {settlementLink(data.last_settlement)} · {BOARD_STATE_LABELS[boardState(data.last_settlement)]}
            </>
          ) : (
            "todavía no tiene."
          )}
        </p>
        <p data-testid="outlet-not-consolidated">
          Entregado y todavía no consolidado por un cierre:{" "}
          <span className="font-medium tabular-nums">{formatMoney(data.remitted_not_consolidated)}</span>
        </p>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          <Link href={`/rendiciones/boca?vista=todas&boca=${outletId}`} className="text-primary hover:underline">
            Ver historial de rendiciones
          </Link>
          <Link href="/rendiciones/boca" className="text-primary hover:underline">
            Ir al tablero de bocas
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}
