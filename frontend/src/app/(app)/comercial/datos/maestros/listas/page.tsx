import type { Metadata } from "next";

import { PriceListsView } from "@/modules/commercial/masters/price-lists";

export const metadata: Metadata = { title: "Listas de precios · Maestros" };

export default function Page() {
  return <PriceListsView />;
}
