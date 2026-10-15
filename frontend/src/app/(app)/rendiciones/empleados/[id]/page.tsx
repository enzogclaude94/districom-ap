import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { EmployeeFinanceGate } from "@/modules/settlements/employees/area-gate";
import { EmployeeFinanceDetailView } from "@/modules/settlements/employees/employee-detail";

export const metadata: Metadata = { title: "Empleado · Rendiciones" };

export default async function EmployeeFinanceDetailPage({ params }: PageProps<"/rendiciones/empleados/[id]">) {
  const { id } = await params;
  const employeeId = Number(id);
  if (!Number.isSafeInteger(employeeId) || employeeId <= 0) notFound();
  return (
    <EmployeeFinanceGate>
      <EmployeeFinanceDetailView employeeId={employeeId} />
    </EmployeeFinanceGate>
  );
}
