import type { Metadata } from "next";

import { PageHeader } from "@/components/common/page-header";
import { ExpenseCategories } from "@/modules/settlements/config/expense-categories";

export const metadata: Metadata = { title: "Configuración · Rendiciones" };

export default function SettlementsConfigPage() {
  return (
    <>
      <PageHeader title="Configuración de Rendiciones" description="Datos que usa la operación diaria del módulo." />
      <ExpenseCategories />
    </>
  );
}
