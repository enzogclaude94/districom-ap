import type { Metadata } from "next";
import { Suspense } from "react";

import { FuelHistory } from "@/modules/settlements/fuel/fuel-history";

export const metadata: Metadata = { title: "Combustible · Rendiciones" };

export default function FuelHistoryPage() {
  return (
    <Suspense>
      <FuelHistory />
    </Suspense>
  );
}
