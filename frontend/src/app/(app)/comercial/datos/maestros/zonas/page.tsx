import type { Metadata } from "next";

import { ZoneList } from "@/modules/commercial/masters/zones";

export const metadata: Metadata = { title: "Zonas · Maestros" };

export default function Page() {
  return <ZoneList />;
}
