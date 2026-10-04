import type { Metadata } from "next";

import { PageHeader } from "@/components/common/page-header";
import { EmployeeList, NewEmployeeButton } from "@/features/employees/employee-list";

export const metadata: Metadata = { title: "Empleados" };

export default function EmployeesPage() {
  return (
    <>
      <PageHeader
        title="Empleados"
        description="Personas que trabajan en la empresa y sus funciones. Las usan todos los módulos."
        actions={<NewEmployeeButton />}
      />
      <EmployeeList />
    </>
  );
}
