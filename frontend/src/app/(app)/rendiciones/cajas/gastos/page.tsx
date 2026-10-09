import type { Metadata } from "next";

import { ExpenseList } from "@/modules/settlements/expenses/expense-list";

export const metadata: Metadata = { title: "Gastos directos · Rendiciones" };

export default function DirectExpensesPage() {
  return <ExpenseList />;
}
