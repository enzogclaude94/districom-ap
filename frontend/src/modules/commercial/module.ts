import type { AppModule } from "@/lib/modules";
import { COMMERCIAL_NAV } from "@/modules/commercial/nav";

export const commercialModule: AppModule = {
  key: "commercial",
  name: "Inteligencia Comercial",
  basePath: "/comercial",
  nav: COMMERCIAL_NAV,
};
