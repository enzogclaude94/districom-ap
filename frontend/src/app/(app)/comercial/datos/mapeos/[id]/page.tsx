import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { MappingDetailView } from "@/modules/commercial/mappings/mapping-detail";

export const metadata: Metadata = { title: "Mapeo · Inteligencia Comercial" };

export default async function MappingPage({ params }: PageProps<"/comercial/datos/mapeos/[id]">) {
  const { id } = await params;
  const mappingId = Number(id);
  if (!Number.isSafeInteger(mappingId) || mappingId <= 0) notFound();
  return <MappingDetailView mappingId={mappingId} />;
}
