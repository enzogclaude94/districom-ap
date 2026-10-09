"use client";

import type { LucideIcon } from "lucide-react";
import { IdCard, Settings, Truck } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import type { NavItem, NavSection } from "@/lib/modules";
import { cn } from "@/lib/cn";

/** Platform pages, shown under every module's navigation. */
const PLATFORM_NAV: NavSection[] = [
  { label: "Vehículos", icon: Truck, href: "/vehiculos" },
  { label: "Configuración", icon: Settings, href: "/configuracion" },
];
/** Platform pages only platform admins can use. */
const PLATFORM_ADMIN_NAV: NavSection[] = [{ label: "Empleados", icon: IdCard, href: "/empleados" }];

function isActive(pathname: string, item: NavItem) {
  return item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(`${item.href}/`);
}

interface NavLinkProps {
  item: NavItem;
  pathname: string;
  phasePrefix: string;
  nested?: boolean;
  icon?: LucideIcon;
}

function NavLink({ item, pathname, phasePrefix, nested, icon: Icon }: NavLinkProps) {
  const base = cn("flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-sm", nested && "pl-7.5");
  const label = (
    <span className="flex items-center gap-2">
      {Icon && <Icon className="size-4" aria-hidden />}
      {item.label}
    </span>
  );
  if (item.phase !== undefined) {
    return (
      <span className={cn(base, "cursor-not-allowed text-muted-foreground/70")} aria-disabled="true">
        {label}
        <Badge className="px-1.5 text-[10px]" title={`Disponible en la fase ${phasePrefix}${item.phase}`}>
          {phasePrefix}
          {item.phase}
        </Badge>
      </span>
    );
  }
  const active = isActive(pathname, item);
  const Indicator = item.indicator;
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={cn(base, active ? "bg-primary-soft font-medium text-primary" : "text-foreground hover:bg-muted")}
    >
      {label}
      {Indicator && <Indicator />}
    </Link>
  );
}

interface SectionsProps {
  sections: NavSection[];
  pathname: string;
  phasePrefix: string;
}

function Sections({ sections, pathname, phasePrefix }: SectionsProps) {
  return sections.map((section) => {
    const Icon = section.icon;
    if (section.href) {
      return (
        <NavLink
          key={section.label}
          pathname={pathname}
          phasePrefix={phasePrefix}
          icon={Icon}
          item={{ label: section.label, href: section.href, phase: section.phase, exact: section.exact }}
        />
      );
    }
    return (
      <div key={section.label} className="space-y-0.5">
        <p className="flex items-center gap-2 px-2 pb-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          <Icon className="size-3.5" aria-hidden />
          {section.label}
        </p>
        {section.items?.map((item) => (
          <NavLink key={item.href} item={item} pathname={pathname} phasePrefix={phasePrefix} nested />
        ))}
      </div>
    );
  });
}

interface SidebarProps {
  sections: NavSection[];
  /** Roadmap phase prefix of the active module ("F", "R"). */
  phasePrefix?: string;
  platformAdmin?: boolean;
}

/** Navigation of the active module followed by the platform pages. */
export function Sidebar({ sections, phasePrefix = "F", platformAdmin = false }: SidebarProps) {
  const pathname = usePathname();
  return (
    <nav aria-label="Navegación principal" className="flex flex-col gap-4 p-3">
      <Sections sections={sections} pathname={pathname} phasePrefix={phasePrefix} />
      <div className="border-t border-border pt-3">
        <Sections
          sections={platformAdmin ? [...PLATFORM_ADMIN_NAV, ...PLATFORM_NAV] : PLATFORM_NAV}
          pathname={pathname}
          phasePrefix={phasePrefix}
        />
      </div>
    </nav>
  );
}
