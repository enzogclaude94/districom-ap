import type { LucideIcon } from "lucide-react";
import type { ComponentType } from "react";

/** Contract every business module fulfils in the frontend (ARCHITECTURE.md §5, ADR 006). */
export interface NavItem {
  label: string;
  href: string;
  /** Roadmap phase that delivers the screen; disabled until it ships. */
  phase?: number;
  /** Active only on this exact path (module home pages). */
  exact?: boolean;
  /** Small live indicator next to the label, provided by the module (e.g. a count in alert). */
  indicator?: ComponentType;
}

export interface NavSection {
  label: string;
  icon: LucideIcon;
  href?: string;
  phase?: number;
  exact?: boolean;
  items?: NavItem[];
}

export interface ModuleLogoProps {
  height?: number;
  className?: string;
  priority?: boolean;
}

export interface AppModule {
  /** Same key as the backend module (`/api/v1/<key>`, settings scope, `/auth/me` modules). */
  key: string;
  name: string;
  /** One line shown in the module selector. */
  description: string;
  /** Icon in the module selector and switcher (never a brand logo). */
  icon: LucideIcon;
  /** Official module logo, when the module has one (docs/BRAND.md). */
  Logo?: ComponentType<ModuleLogoProps>;
  basePath: string;
  /** Prefix of the roadmap phase badge on disabled items: "F" (Comercial phases) or "R" (Rendiciones). */
  phasePrefix: string;
  nav: NavSection[];
}

export function isInModule(pathname: string, module: AppModule) {
  return pathname === module.basePath || pathname.startsWith(`${module.basePath}/`);
}
