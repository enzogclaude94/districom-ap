import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { EmployeeFinanceGate } from "@/modules/settlements/employees/area-gate";
import { PayoutView } from "@/modules/settlements/employees/payout-detail";

export const metadata: Metadata = { title: "Pago de liquidación · Rendiciones" };

export default async function PayoutPage({ params }: PageProps<"/rendiciones/empleados/pagos/[id]">) {
  const { id } = await params;
  const payoutId = Number(id);
  if (!Number.isSafeInteger(payoutId) || payoutId <= 0) notFound();
  return (
    <EmployeeFinanceGate>
      <PayoutView payoutId={payoutId} />
    </EmployeeFinanceGate>
  );
}
