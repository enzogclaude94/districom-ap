import type { Metadata } from "next";

import { OutletsHome } from "@/modules/settlements/outlet-settlements/outlets-home";

export const metadata: Metadata = { title: "Bocas · Rendiciones" };

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** Rendiciones › Bocas: the daily board (`?fecha=`), or every settlement (`?vista=todas`, `?boca=` filters). */
export default async function OutletSettlementsPage({ searchParams }: PageProps<"/rendiciones/boca">) {
  const { boca, vista, fecha } = await searchParams;
  const outletId = typeof boca === "string" ? Number(boca) : NaN;
  const validOutlet = Number.isSafeInteger(outletId) && outletId > 0 ? outletId : undefined;
  // A link filtered by boca (the ficha's, before R7-4) opens the list.
  const view = vista === "todas" || validOutlet !== undefined ? "all" : "board";
  return (
    <OutletsHome
      view={view}
      outletId={validOutlet}
      date={typeof fecha === "string" && DAY.test(fecha) ? fecha : undefined}
    />
  );
}
