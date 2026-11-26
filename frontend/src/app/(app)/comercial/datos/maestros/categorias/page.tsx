import type { Metadata } from "next";

import { CategoriesView } from "@/modules/commercial/masters/categories";

export const metadata: Metadata = { title: "Categorías y familias · Maestros" };

export default function Page() {
  return <CategoriesView />;
}
