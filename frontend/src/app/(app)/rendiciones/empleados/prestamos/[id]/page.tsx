import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { EmployeeFinanceGate } from "@/modules/settlements/employees/area-gate";
import { EmployeeLoanView } from "@/modules/settlements/employees/loan-detail";

export const metadata: Metadata = { title: "Préstamo a empleado · Rendiciones" };

export default async function EmployeeLoanPage({ params }: PageProps<"/rendiciones/empleados/prestamos/[id]">) {
  const { id } = await params;
  const loanId = Number(id);
  if (!Number.isSafeInteger(loanId) || loanId <= 0) notFound();
  return (
    <EmployeeFinanceGate>
      <EmployeeLoanView loanId={loanId} />
    </EmployeeFinanceGate>
  );
}
