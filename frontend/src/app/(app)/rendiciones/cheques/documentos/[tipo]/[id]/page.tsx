import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { DOCUMENT_PATHS, type CycleDocumentType } from "@/modules/settlements/cheques/api";
import { DocumentDetailView } from "@/modules/settlements/cheques/document-detail";

export const metadata: Metadata = { title: "Documento de cheques · Rendiciones" };

export default async function ChequeDocumentPage({ params }: PageProps<"/rendiciones/cheques/documentos/[tipo]/[id]">) {
  const { tipo, id } = await params;
  const type = (Object.keys(DOCUMENT_PATHS) as CycleDocumentType[]).find((key) => DOCUMENT_PATHS[key] === tipo);
  const documentId = Number(id);
  if (!type || !Number.isSafeInteger(documentId) || documentId <= 0) notFound();
  return <DocumentDetailView type={type} id={documentId} />;
}
