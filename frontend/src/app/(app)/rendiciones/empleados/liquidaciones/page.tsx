import type { Metadata } from "next";

import { EmployeeFinanceGate } from "@/modules/settlements/employees/area-gate";
import { PayrollList } from "@/modules/settlements/employees/payroll-list";

export const metadata: Metadata = { title: "Liquidaciones internas · Rendiciones" };

export default function PayrollsPage() {
  return (
    <EmployeeFinanceGate>
      <PayrollList />
    </EmployeeFinanceGate>
  );
}
