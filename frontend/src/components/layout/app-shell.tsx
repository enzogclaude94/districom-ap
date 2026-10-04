"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, LogOut, Menu, X } from "lucide-react";
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
import { authApi, currentUserQueryKey } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/cn";
import { isInModule, type AppModule } from "@/lib/modules";

const ROLE_LABELS = { admin: "Administrador", viewer: "Lectura" } as const;

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
          "fixed inset-y-0 left-0 z-40 w-(--sidebar-width) overflow-y-auto border-r border-border bg-surface transition-transform md:sticky md:top-0 md:h-screen md:translate-x-0",
          menuOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-16 flex-col justify-center gap-1 border-b border-border px-5">
          <Wordmark height={20} priority />
          <p className="text-xs font-medium text-muted-foreground">{activeModule.name}</p>
        </div>
        <Sidebar sections={activeModule.nav} platformAdmin={user.role === "admin"} />
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
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-surface/95 px-4 backdrop-blur md:px-8">
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
              <Badge className="mt-0.5">{ROLE_LABELS[user.role]}</Badge>
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
        <main className="mx-auto w-full max-w-7xl flex-1 space-y-6 p-4 md:p-8">{children}</main>
      </div>
    </div>
  );
}
