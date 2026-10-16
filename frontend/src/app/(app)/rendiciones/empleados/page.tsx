import type { Metadata } from "next";

import { EmployeeFinanceGate } from "@/modules/settlements/employees/area-gate";
import { EmployeeFinanceList } from "@/modules/settlements/employees/employee-list";

export const metadata: Metadata = { title: "Empleados · Rendiciones" };

export default function EmployeeFinancePage() {
  return (
    <EmployeeFinanceGate>
      <EmployeeFinanceList />
    </EmployeeFinanceGate>
  );
}
