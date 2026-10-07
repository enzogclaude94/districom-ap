import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { PendingDetailView } from "@/modules/settlements/pending/pending-detail";

export const metadata: Metadata = { title: "Pendiente de cobro · Rendiciones" };

export default async function PendingCollectionPage({ params }: PageProps<"/rendiciones/pendientes/[id]">) {
  const { id } = await params;
  const pendingId = Number(id);
  if (!Number.isSafeInteger(pendingId) || pendingId <= 0) notFound();
  return <PendingDetailView pendingId={pendingId} />;
}
