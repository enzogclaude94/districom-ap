"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronsUpDown, LayoutGrid, Lock, LogOut, Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { BrandIcon } from "@/components/brand/brand-icon";
import { Wordmark } from "@/components/brand/wordmark";
import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Sidebar } from "@/components/layout/sidebar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { authApi, currentUserQueryKey, moduleRole } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/cn";
import { isInModule, type AppModule } from "@/lib/modules";

const ROLE_LABELS = { admin: "Administrador", operator: "Operador", viewer: "Lector" } as const;

interface ModuleSwitcherProps {
  active: AppModule;
  modules: AppModule[];
}

/** Module name under the wordmark; with more than one module it opens a menu to switch. */
function ModuleSwitcher({ active, modules }: ModuleSwitcherProps) {
  const pathname = usePathname();
  // Remembers the path it was opened on, so navigating closes it.
  const [openedAt, setOpenedAt] = useState<string | null>(null);
  const open = openedAt === pathname;
  if (modules.length < 2) {
    return <p className="text-xs font-medium text-muted-foreground">{active.name}</p>;
  }
  return (
    <div className="relative">
      <button
        type="button"
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={`Módulo actual: ${active.name}. Cambiar de módulo`}
        className="-mx-1 flex items-center gap-1 rounded px-1 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
        onClick={() => setOpenedAt(open ? null : pathname)}
      >
        {active.name}
        <ChevronsUpDown className="size-3" aria-hidden />
      </button>
      {open && (
        <nav
          aria-label="Módulos"
          className="absolute top-6 left-0 z-50 w-60 rounded-md border border-border bg-surface p-1 shadow-card"
        >
          {modules.map((module) => {
            const Icon = module.icon;
            return (
              <Link
                key={module.key}
                href={module.basePath}
                aria-current={module.key === active.key ? "true" : undefined}
                className={cn(
                  "flex items-center gap-2 rounded px-2 py-1.5 text-sm",
                  module.key === active.key ? "bg-primary-soft font-medium text-primary" : "hover:bg-muted",
                )}
              >
                <Icon className="size-4" aria-hidden />
                {module.name}
              </Link>
            );
          })}
          <Link
            href="/"
            className="mt-1 flex items-center gap-2 border-t border-border px-2 pt-2 pb-1.5 text-sm text-muted-foreground hover:text-foreground"
          >
            <LayoutGrid className="size-4" aria-hidden />
            Todos los módulos
          </Link>
        </nav>
      )}
    </div>
  );
}

interface AppShellProps {
  /** Installed modules, passed by the app layout (shared code never imports modules). */
  modules: AppModule[];
  children: ReactNode;
}

export function AppShell({ modules, children }: AppShellProps) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  // The mobile menu remembers the path it was opened on, so navigating closes it.
  const [menuOpenedAt, setMenuOpenedAt] = useState<string | null>(null);
  const menuOpen = menuOpenedAt === pathname;
  const setMenuOpen = (open: boolean) => setMenuOpenedAt(open ? pathname : null);
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const logout = useMutation({
    mutationFn: authApi.logout,
    onSettled: () => {
      queryClient.clear();
      router.replace("/login");
    },
  });

  const unauthorized = me.error instanceof ApiError && me.error.status === 401;
  // After a logout the cleared session query refetches and gets a 401; that must not
  // race the logout redirect and send the user to `/login?next=…`.
  const loggedOut = !logout.isIdle;
  useEffect(() => {
    if (unauthorized && !loggedOut) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [unauthorized, loggedOut, router, pathname]);

  if (me.isPending || unauthorized) {
    return (
      <div className="flex min-h-screen" aria-busy="true" aria-label="Cargando">
        <div className="hidden w-(--sidebar-width) border-r border-border bg-surface p-4 md:block">
          <Skeleton className="h-6 w-32" />
        </div>
        <div className="flex-1 space-y-4 p-8">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-40 w-full" />
        </div>
      </div>
    );
  }
  if (me.isError) {
    return (
      <div className="mx-auto max-w-lg p-8">
        <ErrorState error={me.error} onRetry={() => void me.refetch()} />
      </div>
    );
  }

  const user = me.data;
  const accessible = modules.filter((module) => user.modules.some((access) => access.key === module.key));
  // Platform pages (e.g. Configuración) keep the navigation of the user's first module.
  const activeModule = accessible.find((module) => isInModule(pathname, module)) ?? accessible[0];
  // A module the user has no role in: its screens are never rendered (the API also answers 403).
  const blockedModule = modules.find((module) => isInModule(pathname, module) && !accessible.includes(module));
  // Inside a module the badge shows the effective role there (the platform admin is admin in every module);
  // on platform pages (Empleados, Configuración…) it shows the platform role.
  const insideModule = activeModule !== undefined && isInModule(pathname, activeModule);
  const role = insideModule ? (moduleRole(user, activeModule.key) ?? user.role) : user.role;
  const roleTitle = insideModule ? `Tu rol en ${activeModule.name}` : "Tu rol en la plataforma";

  if (!activeModule) {
    return (
      <div className="mx-auto max-w-lg p-8">
        <EmptyState
          icon={Lock}
          title="Sin módulos habilitados"
          description="Pedile a un administrador que te dé acceso."
        />
      </div>
    );
  }
  return (
    <div className="flex min-h-screen">
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 w-(--sidebar-width) overflow-y-auto border-r border-border bg-surface transition-transform md:sticky md:top-0 md:h-screen md:translate-x-0 print:hidden",
          menuOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-16 flex-col justify-center gap-1 border-b border-border px-5">
          <Wordmark height={20} priority />
          <ModuleSwitcher active={activeModule} modules={accessible} />
        </div>
        <Sidebar
          sections={activeModule.nav}
          phasePrefix={activeModule.phasePrefix}
          platformAdmin={user.role === "admin"}
          areas={(user.areas ?? []).map((area) => area.key)}
        />
      </aside>
      {menuOpen && (
        <button
          type="button"
          aria-label="Cerrar menú"
          className="fixed inset-0 z-30 bg-black/30 md:hidden"
          onClick={() => setMenuOpen(false)}
        />
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-surface/95 px-4 backdrop-blur md:px-8 print:hidden">
          <Button
            variant="ghost"
            size="icon"
            className="md:hidden"
            aria-label={menuOpen ? "Cerrar menú" : "Abrir menú"}
            onClick={() => setMenuOpen(!menuOpen)}
          >
            {menuOpen ? <X /> : <Menu />}
          </Button>
          <BrandIcon size={28} className="rounded-md md:hidden" />
          {/* Global filters (period, salesperson, route…) land here in Phase 2. */}
          <div className="flex-1" />
          <div className="flex items-center gap-3">
            <div className="hidden text-right sm:block">
              <p className="text-sm leading-tight font-medium">{user.full_name}</p>
              <Badge className="mt-0.5" title={roleTitle} aria-label={`${roleTitle}: ${ROLE_LABELS[role]}`}>
                {ROLE_LABELS[role]}
              </Badge>
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Cerrar sesión"
              title="Cerrar sesión"
              disabled={logout.isPending}
              onClick={() => logout.mutate()}
            >
              <LogOut />
            </Button>
          </div>
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 space-y-6 p-4 md:p-8 print:max-w-none print:p-0">
          {blockedModule ? (
            <EmptyState
              icon={Lock}
              title={`No tenés acceso a ${blockedModule.name}`}
              description="Pedile a un administrador de DistriCom que te asigne un rol en este módulo."
              action={
                <Link href="/" className="text-sm font-medium text-primary hover:underline">
                  Ir a mis módulos
                </Link>
              }
            />
          ) : (
            children
          )}
        </main>
      </div>
    </div>
  );
}
