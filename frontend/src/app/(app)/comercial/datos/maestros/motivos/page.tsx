import type { Metadata } from "next";
import { Suspense } from "react";

import { ReasonsView } from "@/modules/commercial/masters/reasons";

export const metadata: Metadata = { title: "Motivos de no compra · Maestros" };

export default function Page() {
  return (
    // Calidad links to the exact reason (`?motivo=7`): the view reads it from the URL.
    <Suspense>
      <ReasonsView />
    </Suspense>
  );
}
