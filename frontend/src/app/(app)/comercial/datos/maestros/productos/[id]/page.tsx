import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ProductDetailView } from "@/modules/commercial/masters/products";

export const metadata: Metadata = { title: "Producto · Maestros" };

export default async function Page({ params }: PageProps<"/comercial/datos/maestros/productos/[id]">) {
  const { id } = await params;
  const entityId = Number(id);
  if (!Number.isSafeInteger(entityId) || entityId <= 0) notFound();
  return <ProductDetailView productId={entityId} />;
}
