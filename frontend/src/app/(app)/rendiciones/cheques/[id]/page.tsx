import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ChequeDetailView } from "@/modules/settlements/cheques/cheque-detail";

export const metadata: Metadata = { title: "Cheque · Rendiciones" };

export default async function ChequePage({ params }: PageProps<"/rendiciones/cheques/[id]">) {
  const { id } = await params;
  const chequeId = Number(id);
  if (!Number.isSafeInteger(chequeId) || chequeId <= 0) notFound();
  return <ChequeDetailView chequeId={chequeId} />;
}
