import type { Metadata } from "next";

import { MappingList } from "@/modules/commercial/mappings/mapping-list";

export const metadata: Metadata = { title: "Mapeos · Inteligencia Comercial" };

export default function MappingsPage() {
  return <MappingList />;
}
