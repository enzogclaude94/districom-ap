import type { LucideIcon } from "lucide-react";

/** Contract every business module fulfils in the frontend (ARCHITECTURE.md §5, ADR 006). */
export interface NavItem {
  label: string;
  href: string;
  /** Roadmap phase that delivers the screen; disabled until it ships. */
  phase?: number;
  /** Active only on this exact path (module home pages). */
  exact?: boolean;
}

export interface NavSection {
  label: string;
  icon: LucideIcon;
  href?: string;
  phase?: number;
  exact?: boolean;
  items?: NavItem[];
}

export interface AppModule {
  /** Same key as the backend module (`/api/v1/<key>`, settings scope, `/auth/me` modules). */
  key: string;
  name: string;
  basePath: string;
  nav: NavSection[];
}

export function isInModule(pathname: string, module: AppModule) {
  return pathname === module.basePath || pathname.startsWith(`${module.basePath}/`);
}
