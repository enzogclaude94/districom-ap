"use client";

import type { LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { NAV_SECTIONS, type NavItem } from "@/features/navigation/nav-items";
import { cn } from "@/lib/cn";

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
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
  const active = isActive(pathname, item.href);
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={cn(base, active ? "bg-primary/10 font-medium text-primary" : "hover:bg-muted")}
    >
      {label}
    </Link>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  return (
    <nav aria-label="Navegación principal" className="flex flex-col gap-4 p-3">
      {NAV_SECTIONS.map((section) => {
        const Icon = section.icon;
        if (section.href) {
          return (
            <NavLink
              key={section.label}
              pathname={pathname}
              icon={Icon}
              item={{ label: section.label, href: section.href, phase: section.phase }}
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
      })}
    </nav>
  );
}
