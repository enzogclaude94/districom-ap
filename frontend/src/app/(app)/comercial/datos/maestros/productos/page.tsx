import type { Metadata } from "next";

import { ProductList } from "@/modules/commercial/masters/products";

export const metadata: Metadata = { title: "Productos · Maestros" };

export default function Page() {
  return <ProductList />;
}
