import { ChartColumn } from "lucide-react";

import type { AppModule } from "@/lib/modules";
import { COMMERCIAL_NAV } from "@/modules/commercial/nav";

export const commercialModule: AppModule = {
  key: "commercial",
  name: "Inteligencia Comercial",
  description: "Ventas, rutas, preventistas, clientes y oportunidades sobre AXUM y STORE SYS.",
  icon: ChartColumn,
  basePath: "/comercial",
  phasePrefix: "F",
  nav: COMMERCIAL_NAV,
};
