import type { Metadata } from "next";
import { Suspense } from "react";

import { DocumentTypesView } from "@/modules/commercial/masters/document-types";

export const metadata: Metadata = { title: "Tipos de comprobante · Maestros" };

export default function Page() {
  return (
    // Calidad links to the exact type (`?tipo=7`): the view reads it from the URL.
    <Suspense>
      <DocumentTypesView />
    </Suspense>
  );
}
