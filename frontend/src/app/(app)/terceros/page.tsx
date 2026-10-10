import type { Metadata } from "next";

import { PageHeader } from "@/components/common/page-header";
import { ThirdPartyList } from "@/features/third-parties/third-party-list";

export const metadata: Metadata = { title: "Terceros" };

export default function ThirdPartiesPage() {
  return (
    <>
      <PageHeader
        title="Terceros"
        description="Proveedores y otros a quienes la empresa les paga. Son uno solo para todos los módulos."
      />
      <ThirdPartyList />
    </>
  );
}
