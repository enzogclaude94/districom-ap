"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { authApi, currentUserQueryKey, moduleRole } from "@/lib/api/auth";

import { outletSettlementApi, outletSettlementQueryKey } from "./api";

/** «N rendiciones abiertas» on a boca's page (R7-2), for users with access to Rendiciones only. */
export function OutletOpenSettlements({ outletId }: { outletId: number }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const role = moduleRole(me.data, "settlements");
  const summary = useQuery({
    queryKey: [...outletSettlementQueryKey, "open-count", outletId],
    queryFn: () => outletSettlementApi.openCount(outletId),
    enabled: role !== null,
  });
  if (role === null || !summary.data) return null;
  const count = summary.data.open_count;
  return (
    <p className="text-sm" data-testid="outlet-open-settlements">
      {count === 0
        ? "Sin rendiciones abiertas."
        : count === 1
          ? "1 rendición abierta."
          : `${count} rendiciones abiertas.`}{" "}
      <Link href={`/rendiciones/boca?boca=${outletId}`} className="text-primary hover:underline">
        Ver rendiciones de la boca
      </Link>
    </p>
  );
}
