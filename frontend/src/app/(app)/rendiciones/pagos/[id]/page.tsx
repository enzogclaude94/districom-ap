import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { PaymentDetailView } from "@/modules/settlements/payments/payment-detail";

export const metadata: Metadata = { title: "Pago · Rendiciones" };

export default async function PaymentPage({ params }: PageProps<"/rendiciones/pagos/[id]">) {
  const { id } = await params;
  const paymentId = Number(id);
  if (!Number.isSafeInteger(paymentId) || paymentId <= 0) notFound();
  return <PaymentDetailView paymentId={paymentId} />;
}
