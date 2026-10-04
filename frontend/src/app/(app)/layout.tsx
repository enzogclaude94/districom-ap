"use client";

// Client layout: the module registry holds icon components, which cannot cross the server/client boundary.
import { AppShell } from "@/components/layout/app-shell";
import { MODULES } from "@/modules/registry";

export default function AppLayout({ children }: LayoutProps<"/">) {
  return <AppShell modules={MODULES}>{children}</AppShell>;
}
