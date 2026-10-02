import type { Metadata } from "next";

import { PageHeader } from "@/components/common/page-header";
import { SettingsTable } from "@/features/settings/settings-table";

export const metadata: Metadata = { title: "Configuración" };

export default function SettingsPage() {
  return (
    <>
      <PageHeader title="Configuración" description="Parámetros que gobiernan métricas, estados y oportunidades." />
      <SettingsTable />
    </>
  );
}
