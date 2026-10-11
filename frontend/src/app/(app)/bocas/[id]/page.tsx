import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { OutletDetail } from "@/features/outlets/outlet-detail";
import { OutletResponsibilities } from "@/modules/settlements/outlets/outlet-responsibilities";

export const metadata: Metadata = { title: "Boca de expendio" };

/** The boca (platform) and, for Rendiciones users, its responsables (module): composed here, not imported. */
export default async function OutletPage({ params }: PageProps<"/bocas/[id]">) {
  const { id } = await params;
  const outletId = Number(id);
  if (!Number.isSafeInteger(outletId) || outletId <= 0) notFound();
  return (
    <>
      <OutletDetail outletId={outletId} />
      <OutletResponsibilities outletId={outletId} />
    </>
  );
}
