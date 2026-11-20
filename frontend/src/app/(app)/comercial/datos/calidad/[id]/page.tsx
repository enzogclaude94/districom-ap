import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { QualityDetailView } from "@/modules/commercial/quality/quality-detail";

export const metadata: Metadata = { title: "Problema de calidad · Inteligencia Comercial" };

export default async function QualityIssuePage({ params }: PageProps<"/comercial/datos/calidad/[id]">) {
  const { id } = await params;
  const issueId = Number(id);
  if (!Number.isSafeInteger(issueId) || issueId <= 0) notFound();
  return <QualityDetailView issueId={issueId} />;
}
