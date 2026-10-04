import type { Metadata } from "next";

import { PageHeader } from "@/components/common/page-header";
import { NewEmployee } from "@/features/employees/new-employee";

export const metadata: Metadata = { title: "Nuevo empleado" };

export default function NewEmployeePage() {
  return (
    <>
      <PageHeader title="Nuevo empleado" description="Las funciones se asignan después de crearlo." />
      <NewEmployee />
    </>
  );
}
