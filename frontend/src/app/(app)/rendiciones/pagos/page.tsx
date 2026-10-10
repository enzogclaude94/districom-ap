import type { Metadata } from "next";

import { PaymentList } from "@/modules/settlements/payments/payment-list";

export const metadata: Metadata = { title: "Pagos · Rendiciones" };

const positive = (value: string | string[] | undefined) => {
  const id = Number(typeof value === "string" ? value : undefined);
  return Number.isSafeInteger(id) && id > 0 ? id : undefined;
};

/**
 * `?tercero=<id>` (from a third party's ficha) opens the list filtered by that third party; `?cheque=<id>`
 * («Entregar» on a cheque of the portfolio) opens «Registrar pago» with that cheque preloaded.
 */
export default async function PaymentsPage({ searchParams }: PageProps<"/rendiciones/pagos">) {
  const { tercero, cheque } = await searchParams;
  return <PaymentList initialThirdParty={positive(tercero)} initialCheque={positive(cheque)} />;
}
