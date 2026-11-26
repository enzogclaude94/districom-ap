import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ZoneDetailView } from "@/modules/commercial/masters/zones";

export const metadata: Metadata = { title: "Zona · Maestros" };

export default async function Page({ params }: PageProps<"/comercial/datos/maestros/zonas/[id]">) {
  const { id } = await params;
  const entityId = Number(id);
  if (!Number.isSafeInteger(entityId) || entityId <= 0) notFound();
  return <ZoneDetailView zoneId={entityId} />;
}
