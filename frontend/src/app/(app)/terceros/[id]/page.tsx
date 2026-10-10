import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ThirdPartyDetailView } from "@/features/third-parties/third-party-detail";
import { ThirdPartyPayments } from "@/modules/settlements/payments/third-party-payments";

export const metadata: Metadata = { title: "Tercero" };

/** The third party (platform) and, for Rendiciones users, its payments (module): composed here, not imported. */
export default async function ThirdPartyPage({ params }: PageProps<"/terceros/[id]">) {
  const { id } = await params;
  const thirdPartyId = Number(id);
  if (!Number.isSafeInteger(thirdPartyId) || thirdPartyId <= 0) notFound();
  return (
    <>
      <ThirdPartyDetailView thirdPartyId={thirdPartyId} />
      <ThirdPartyPayments thirdPartyId={thirdPartyId} />
    </>
  );
}
