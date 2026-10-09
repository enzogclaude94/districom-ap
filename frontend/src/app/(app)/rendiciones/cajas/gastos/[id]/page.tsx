import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { ExpenseDetailView } from "@/modules/settlements/expenses/expense-detail";

export const metadata: Metadata = { title: "Gasto directo · Rendiciones" };

export default async function DirectExpensePage({ params }: PageProps<"/rendiciones/cajas/gastos/[id]">) {
  const { id } = await params;
  const expenseId = Number(id);
  if (!Number.isSafeInteger(expenseId) || expenseId <= 0) notFound();
  return <ExpenseDetailView expenseId={expenseId} />;
}
