import type { Metadata } from "next";

import { PaymentList } from "@/modules/settlements/payments/payment-list";

export const metadata: Metadata = { title: "Pagos · Rendiciones" };

/** `?tercero=<id>` (from a third party's ficha) opens the list filtered by that third party. */
export default async function PaymentsPage({ searchParams }: PageProps<"/rendiciones/pagos">) {
  const { tercero } = await searchParams;
  const thirdPartyId = Number(typeof tercero === "string" ? tercero : undefined);
  return (
    <PaymentList
      initialThirdParty={Number.isSafeInteger(thirdPartyId) && thirdPartyId > 0 ? thirdPartyId : undefined}
    />
  );
}
