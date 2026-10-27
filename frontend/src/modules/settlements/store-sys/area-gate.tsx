"use client";

import { useQuery } from "@tanstack/react-query";
import { SearchX } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { areaRole, authApi, currentUserQueryKey } from "@/lib/api/auth";

import { STORE_SYS_AREA } from "./api";

/** Renders the area only with an explicit grant (R9-1, as ADR 010): without it the page reads as one that does not exist. */
export function StoreSysGate({ children }: { children: ReactNode }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  if (me.isPending) return <Skeleton className="h-40 w-full" />;
  if (areaRole(me.data, STORE_SYS_AREA) === null) {
    return (
      <EmptyState icon={SearchX} title="Página no encontrada" description="La dirección no existe o fue movida." />
    );
  }
  return <>{children}</>;
}
