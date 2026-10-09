import type { Metadata } from "next";
import { Suspense } from "react";

import { ChequeList } from "@/modules/settlements/cheques/cheque-list";

export const metadata: Metadata = { title: "Cartera de cheques · Rendiciones" };

export default function ChequesPage() {
  // The list reads its initial filter from the URL (`?state=IN_PORTFOLIO`, from the «Valores» block in Cajas).
  return (
    <Suspense>
      <ChequeList />
    </Suspense>
  );
}
