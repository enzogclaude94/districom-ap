import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { VehicleDetail } from "@/features/vehicles/vehicle-detail";
import { VehicleFuelLoads } from "@/modules/settlements/fuel/vehicle-fuel-loads";

export const metadata: Metadata = { title: "Vehículo" };

/** The vehicle (platform) and, for Rendiciones users, its fuel loads (module): composed here, not imported. */
export default async function VehiclePage({ params }: PageProps<"/vehiculos/[id]">) {
  const { id } = await params;
  const vehicleId = Number(id);
  if (!Number.isSafeInteger(vehicleId) || vehicleId <= 0) notFound();
  return (
    <>
      <VehicleDetail vehicleId={vehicleId} />
      <VehicleFuelLoads vehicleId={vehicleId} />
    </>
  );
}
