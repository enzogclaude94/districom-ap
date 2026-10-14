"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { cn } from "@/lib/cn";

import { NewOutletSettlementDialog } from "./new-outlet-settlement-dialog";
import { OutletBoard } from "./outlet-board";
import { OutletSettlementList } from "./outlet-list";

export type OutletsView = "board" | "all";

/**
 * Rendiciones › Bocas (`/rendiciones/boca`, R7-4): the daily board of every boca by default, and every boca
 * settlement with filters under «Todas las rendiciones».
 */
export function OutletsHome({ view, outletId, date }: { view: OutletsView; outletId?: number; date?: string }) {
  const router = useRouter();
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const canOperate = roleAtLeast(moduleRole(me.data, "settlements"), "operator");
  const [creating, setCreating] = useState(false);
  const tabs: [OutletsView, string, string][] = [
    ["board", "Tablero del día", "/rendiciones/boca"],
    ["all", "Todas las rendiciones", "/rendiciones/boca?vista=todas"],
  ];
  return (
    <>
      <PageHeader
        title="Bocas"
        description="Qué tiene que rendir cada boca según Store Sys, qué ya entregó, qué justificó y qué resta."
        actions={
          canOperate && (
            <Button onClick={() => setCreating(true)}>
              <Plus aria-hidden />
              Nueva rendición
            </Button>
          )
        }
      />
      <nav aria-label="Vistas de bocas" className="flex gap-1 overflow-x-auto border-b border-border">
        {tabs.map(([value, label, href]) => (
          <Link
            key={value}
            href={href}
            aria-current={view === value ? "page" : undefined}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm font-medium whitespace-nowrap",
              view === value ? "border-primary text-foreground" : "border-transparent text-muted-foreground",
            )}
          >
            {label}
          </Link>
        ))}
      </nav>
      {view === "board" ? <OutletBoard initialDate={date} /> : <OutletSettlementList initialOutletId={outletId} />}
      {creating && (
        <NewOutletSettlementDialog
          initialOutletId={outletId}
          onClose={() => setCreating(false)}
          onCreated={(settlement) => router.push(`/rendiciones/boca/${settlement.id}`)}
        />
      )}
    </>
  );
}
