import type { Metadata } from "next";

import { DeliveryList } from "@/modules/settlements/delivery/delivery-list";

export const metadata: Metadata = { title: "Rendiciones de reparto · Rendiciones" };

export default function DeliveriesPage() {
  return <DeliveryList />;
}
