import type { Metadata } from "next";
import { Suspense } from "react";

import { DailyCloseView } from "@/modules/settlements/daily-close/daily-close";

export const metadata: Metadata = { title: "Cierre del día · Rendiciones" };

export default function DailyClosePage() {
  return (
    <Suspense>
      <DailyCloseView />
    </Suspense>
  );
}
