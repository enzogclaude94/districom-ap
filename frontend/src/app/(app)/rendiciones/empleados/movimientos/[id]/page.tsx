import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { EmployeeFinanceGate } from "@/modules/settlements/employees/area-gate";
import { EmployeeMovementView } from "@/modules/settlements/employees/movement-detail";

export const metadata: Metadata = { title: "Movimiento de empleado · Rendiciones" };

export default async function EmployeeMovementPage({ params }: PageProps<"/rendiciones/empleados/movimientos/[id]">) {
  const { id } = await params;
  const movementId = Number(id);
  if (!Number.isSafeInteger(movementId) || movementId <= 0) notFound();
  return (
    <EmployeeFinanceGate>
      <EmployeeMovementView movementId={movementId} />
    </EmployeeFinanceGate>
  );
}
