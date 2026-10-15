"use client";

import { useQuery } from "@tanstack/react-query";
import { SearchX } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { areaRole, authApi, currentUserQueryKey } from "@/lib/api/auth";

import { EMPLOYEE_FINANCE_AREA } from "./api";

/**
 * Renders the area only with an explicit grant (ADR 010). Without it the screen reads as a page that does not
 * exist, like the API's 404: nothing says there is something to ask for.
 */
export function EmployeeFinanceGate({ children }: { children: ReactNode }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  if (me.isPending) return <Skeleton className="h-40 w-full" />;
  if (areaRole(me.data, EMPLOYEE_FINANCE_AREA) === null) {
    return (
      <EmptyState icon={SearchX} title="Página no encontrada" description="La dirección no existe o fue movida." />
    );
  }
  return <>{children}</>;
}
