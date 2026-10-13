import type { Metadata } from "next";

import { OutletSettlementList } from "@/modules/settlements/outlet-settlements/outlet-list";

export const metadata: Metadata = { title: "Rendiciones de boca · Rendiciones" };

export default async function OutletSettlementsPage({ searchParams }: PageProps<"/rendiciones/boca">) {
  const { boca } = await searchParams;
  const outletId = typeof boca === "string" ? Number(boca) : NaN;
  return (
    <OutletSettlementList initialOutletId={Number.isSafeInteger(outletId) && outletId > 0 ? outletId : undefined} />
  );
}
