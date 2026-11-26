import type { Metadata } from "next";

import { SalespeopleView } from "@/modules/commercial/masters/salespeople";

export const metadata: Metadata = { title: "Vendedores · Maestros" };

export default function Page() {
  return <SalespeopleView />;
}
