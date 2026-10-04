import { ClipboardCheck } from "lucide-react";

import type { AppModule } from "@/lib/modules";
import { SettlementsLogo } from "@/modules/settlements/logo";
import { SETTLEMENTS_NAV } from "@/modules/settlements/nav";

/** Rendiciones (ADR 007). Backend key `settlements`; screens under `/rendiciones`. */
export const settlementsModule: AppModule = {
  key: "settlements",
  name: "Rendiciones",
  description: "Rendiciones de reparto y boca, cajas, cheques, pendientes, gastos, pagos y liquidaciones.",
  icon: ClipboardCheck,
  Logo: SettlementsLogo,
  basePath: "/rendiciones",
  phasePrefix: "R",
  nav: SETTLEMENTS_NAV,
};
