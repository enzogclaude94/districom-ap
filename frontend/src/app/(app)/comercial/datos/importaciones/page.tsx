import type { Metadata } from "next";

import { ImportList } from "@/modules/commercial/imports/import-list";

export const metadata: Metadata = { title: "Importaciones · Inteligencia Comercial" };

export default function ImportsPage() {
  return <ImportList />;
}
