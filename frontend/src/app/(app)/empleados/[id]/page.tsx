import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { EmployeeDetail } from "@/features/employees/employee-detail";

export const metadata: Metadata = { title: "Empleado" };

export default async function EmployeePage({ params }: PageProps<"/empleados/[id]">) {
  const { id } = await params;
  const employeeId = Number(id);
  if (!Number.isSafeInteger(employeeId) || employeeId <= 0) notFound();
  return <EmployeeDetail employeeId={employeeId} />;
}
