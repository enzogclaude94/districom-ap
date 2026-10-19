import type { Metadata } from "next";

import { EmployeeFinanceGate } from "@/modules/settlements/employees/area-gate";
import { PayoutList } from "@/modules/settlements/employees/payout-detail";

export const metadata: Metadata = { title: "Pagos de liquidaciones · Rendiciones" };

export default function PayoutsPage() {
  return (
    <EmployeeFinanceGate>
      <PayoutList />
    </EmployeeFinanceGate>
  );
}
