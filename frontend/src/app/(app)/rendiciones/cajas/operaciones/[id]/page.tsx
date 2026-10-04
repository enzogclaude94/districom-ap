import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { OperationDetail } from "@/modules/settlements/cash/operation-detail";

export const metadata: Metadata = { title: "Operación · Cajas" };

export default async function OperationPage({ params }: PageProps<"/rendiciones/cajas/operaciones/[id]">) {
  const { id } = await params;
  const operationId = Number(id);
  if (!Number.isSafeInteger(operationId) || operationId <= 0) notFound();
  return <OperationDetail operationId={operationId} />;
}
