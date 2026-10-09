import type { Metadata } from "next";

import { PageHeader } from "@/components/common/page-header";
import { VehicleList } from "@/features/vehicles/vehicle-list";

export const metadata: Metadata = { title: "Vehículos" };

export default function VehiclesPage() {
  return (
    <>
      <PageHeader title="Vehículos" description="Los vehículos de la empresa. Son uno solo para todos los módulos." />
      <VehicleList />
    </>
  );
}
