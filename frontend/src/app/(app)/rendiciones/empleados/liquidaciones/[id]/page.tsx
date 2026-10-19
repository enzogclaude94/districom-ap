import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { EmployeeFinanceGate } from "@/modules/settlements/employees/area-gate";
import { PayrollView } from "@/modules/settlements/employees/payroll-detail";

export const metadata: Metadata = { title: "Liquidación interna · Rendiciones" };

export default async function PayrollPage({ params }: PageProps<"/rendiciones/empleados/liquidaciones/[id]">) {
  const { id } = await params;
  const payrollId = Number(id);
  if (!Number.isSafeInteger(payrollId) || payrollId <= 0) notFound();
  return (
    <EmployeeFinanceGate>
      <PayrollView payrollId={payrollId} />
    </EmployeeFinanceGate>
  );
}
