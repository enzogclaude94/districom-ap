import { redirect } from "next/navigation";

import { MODULES } from "@/modules/registry";

// A DistriCom home with one card per module arrives when there is more than one module.
export default function PlatformHome() {
  redirect(MODULES[0].basePath);
}
