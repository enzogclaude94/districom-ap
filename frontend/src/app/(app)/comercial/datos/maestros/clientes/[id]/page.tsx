import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { CustomerDetailView } from "@/modules/commercial/masters/customers";

export const metadata: Metadata = { title: "Cliente · Maestros" };

export default async function Page({ params }: PageProps<"/comercial/datos/maestros/clientes/[id]">) {
  const { id } = await params;
  const entityId = Number(id);
  if (!Number.isSafeInteger(entityId) || entityId <= 0) notFound();
  return <CustomerDetailView customerId={entityId} />;
}
