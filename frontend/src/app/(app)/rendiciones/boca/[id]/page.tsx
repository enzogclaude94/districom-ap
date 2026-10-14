import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { SettlementDetail } from "@/modules/settlements/delivery/settlement-detail";

export const metadata: Metadata = { title: "Rendición de boca" };

/** A boca settlement; `?accion=entregar` (from the board of bocas) opens «Entregar efectivo». */
export default async function OutletSettlementPage({ params, searchParams }: PageProps<"/rendiciones/boca/[id]">) {
  const { id } = await params;
  const { accion } = await searchParams;
  const settlementId = Number(id);
  if (!Number.isSafeInteger(settlementId) || settlementId <= 0) notFound();
  return (
    <SettlementDetail
      settlementId={settlementId}
      type="OUTLET"
      initialAction={accion === "entregar" ? "remit" : undefined}
    />
  );
}
