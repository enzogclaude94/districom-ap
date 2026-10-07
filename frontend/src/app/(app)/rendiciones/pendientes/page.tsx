import type { Metadata } from "next";
import { Suspense } from "react";

import { PendingList } from "@/modules/settlements/pending/pending-list";

export const metadata: Metadata = { title: "Pendientes · Rendiciones" };

export default function PendingCollectionsPage() {
  // The list reads its initial filter from the URL (`?alerta=1`, from the home card).
  return (
    <Suspense>
      <PendingList />
    </Suspense>
  );
}
