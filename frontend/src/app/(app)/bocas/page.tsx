import type { Metadata } from "next";

import { PageHeader } from "@/components/common/page-header";
import { OutletList } from "@/features/outlets/outlet-list";

export const metadata: Metadata = { title: "Bocas de expendio" };

export default function OutletsPage() {
  return (
    <>
      <PageHeader
        title="Bocas de expendio"
        description="Los puntos de venta que rinden. Cada boca existe una sola vez para todos los módulos."
      />
      <OutletList />
    </>
  );
}
