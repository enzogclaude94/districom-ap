"use client";

import type { LucideIcon } from "lucide-react";
import { Settings } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import type { NavItem, NavSection } from "@/lib/modules";
import { cn } from "@/lib/cn";

/** Platform pages, shown under every module's navigation. */
const PLATFORM_NAV: NavSection[] = [{ label: "Configuración", icon: Settings, href: "/configuracion" }];

function isActive(pathname: string, item: NavItem) {
  return item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(`${item.href}/`);
}

interface NavLinkProps {
  item: NavItem;
  pathname: string;
  nested?: boolean;
  icon?: LucideIcon;
}

function NavLink({ item, pathname, nested, icon: Icon }: NavLinkProps) {
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
        <Badge className="px-1.5 text-[10px]" title={`Disponible en la fase ${item.phase}`}>
          F{item.phase}
        </Badge>
      </span>
    );
  }
  const active = isActive(pathname, item);
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={cn(base, active ? "bg-primary-soft font-medium text-primary" : "text-foreground hover:bg-muted")}
    >
      {label}
    </Link>
  );
}

function Sections({ sections, pathname }: { sections: NavSection[]; pathname: string }) {
  return sections.map((section) => {
    const Icon = section.icon;
    if (section.href) {
      return (
        <NavLink
          key={section.label}
          pathname={pathname}
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
          <NavLink key={item.href} item={item} pathname={pathname} nested />
        ))}
      </div>
    );
  });
}

/** Navigation of the active module followed by the platform pages. */
export function Sidebar({ sections }: { sections: NavSection[] }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Navegación principal" className="flex flex-col gap-4 p-3">
      <Sections sections={sections} pathname={pathname} />
      <div className="border-t border-border pt-3">
        <Sections sections={PLATFORM_NAV} pathname={pathname} />
      </div>
    </nav>
  );
}
