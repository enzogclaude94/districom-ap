import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { UserDetail } from "@/features/users/user-detail";

export const metadata: Metadata = { title: "Usuario" };

export default async function UserPage({ params }: PageProps<"/usuarios/[id]">) {
  const { id } = await params;
  const userId = Number(id);
  if (!Number.isSafeInteger(userId) || userId <= 0) notFound();
  return <UserDetail userId={userId} />;
}
