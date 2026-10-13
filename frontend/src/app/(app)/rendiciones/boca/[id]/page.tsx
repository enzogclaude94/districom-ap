import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { SettlementDetail } from "@/modules/settlements/delivery/settlement-detail";

export const metadata: Metadata = { title: "Rendición de boca" };

export default async function OutletSettlementPage({ params }: PageProps<"/rendiciones/boca/[id]">) {
  const { id } = await params;
  const settlementId = Number(id);
  if (!Number.isSafeInteger(settlementId) || settlementId <= 0) notFound();
  return <SettlementDetail settlementId={settlementId} type="OUTLET" />;
}
