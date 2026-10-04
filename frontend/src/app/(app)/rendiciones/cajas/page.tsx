import type { Metadata } from "next";

import { CashBoard } from "@/modules/settlements/cash/cash-board";

export const metadata: Metadata = { title: "Cajas · Rendiciones" };

export default function CashPage() {
  return <CashBoard />;
}
