import type { Metadata } from "next";

import { CustomerList } from "@/modules/commercial/masters/customers";

export const metadata: Metadata = { title: "Clientes · Maestros" };

export default function Page() {
  return <CustomerList />;
}
