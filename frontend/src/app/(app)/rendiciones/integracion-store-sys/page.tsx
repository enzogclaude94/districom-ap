import type { Metadata } from "next";

import { StoreSysGate } from "@/modules/settlements/store-sys/area-gate";
import { EmployeeCodes } from "@/modules/settlements/store-sys/employee-codes";

export const metadata: Metadata = { title: "Integración Store Sys · Rendiciones" };

export default function StoreSysPage() {
  return (
    <StoreSysGate>
      <EmployeeCodes />
    </StoreSysGate>
  );
}
