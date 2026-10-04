import type { AppModule } from "@/lib/modules";
import { commercialModule } from "@/modules/commercial/module";
import { settlementsModule } from "@/modules/settlements/module";

/** Installed modules, in display order. Read only by app routes (composition roots). */
export const MODULES: AppModule[] = [commercialModule, settlementsModule];
