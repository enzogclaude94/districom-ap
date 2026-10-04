"use client";

// Client page: the module registry holds icon components, which cannot cross the server/client boundary.
import { ModuleSelector } from "@/components/layout/module-selector";
import { MODULES } from "@/modules/registry";

export default function PlatformHome() {
  return <ModuleSelector modules={MODULES} />;
}
