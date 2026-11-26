import type { Metadata } from "next";
import { Suspense } from "react";

import { QualityList } from "@/modules/commercial/quality/quality-list";

export const metadata: Metadata = { title: "Calidad · Inteligencia Comercial" };

export default function QualityPage() {
  // The list reads its initial filter from the URL (`?job_id=12`, from an import's detail).
  return (
    <Suspense>
      <QualityList />
    </Suspense>
  );
}
