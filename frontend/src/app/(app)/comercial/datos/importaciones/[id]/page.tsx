import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ImportDetailView } from "@/modules/commercial/imports/import-detail";

export const metadata: Metadata = { title: "Importación · Inteligencia Comercial" };

export default async function ImportPage({ params }: PageProps<"/comercial/datos/importaciones/[id]">) {
  const { id } = await params;
  const jobId = Number(id);
  if (!Number.isSafeInteger(jobId) || jobId <= 0) notFound();
  return <ImportDetailView jobId={jobId} />;
}
