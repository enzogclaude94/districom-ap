import { Upload } from "lucide-react";
import type { Metadata } from "next";

import { EmptyState } from "@/components/common/empty-state";
import { PageHeader } from "@/components/common/page-header";

export const metadata: Metadata = { title: "Inicio" };

export default function HomePage() {
  return (
    <>
      <PageHeader title="Inicio" description="Visión ejecutiva del negocio." />
      <EmptyState
        icon={Upload}
        title="Todavía no hay datos comerciales"
        description="El tablero se completa cuando se importen las ventas de STORE SYS y las visitas de AXUM. La importación llega en la Fase 1."
      />
    </>
  );
}
