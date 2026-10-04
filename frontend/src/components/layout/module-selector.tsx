"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { PageHeader } from "@/components/common/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { authApi, currentUserQueryKey } from "@/lib/api/auth";
import type { AppModule } from "@/lib/modules";

const MODULE_ROLE_LABELS = { admin: "Administrador", operator: "Operador", viewer: "Lectura" } as const;

/** DistriCom home: one card per module the user can open; with a single module, go straight to it. */
export function ModuleSelector({ modules }: { modules: AppModule[] }) {
  const router = useRouter();
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const access = new Map(me.data?.modules.map((module) => [module.key, module.role]));
  const accessible = modules.filter((module) => access.has(module.key));
  const only = accessible.length === 1 ? accessible[0] : undefined;

  useEffect(() => {
    if (only) router.replace(only.basePath);
  }, [only, router]);

  // The AppShell renders loading, error and "no modules" states.
  if (!me.data || accessible.length < 2) return <Skeleton className="h-40 w-full" aria-busy="true" />;

  return (
    <>
      <PageHeader title="Módulos" description="Elegí con qué querés trabajar. Podés cambiar de módulo desde el menú." />
      <div className="grid gap-4 md:grid-cols-2">
        {accessible.map((module) => {
          const Icon = module.icon;
          const role = access.get(module.key);
          return (
            <Link
              key={module.key}
              href={module.basePath}
              aria-label={`Entrar a ${module.name}`}
              className="group flex flex-col gap-4 rounded-lg border border-border bg-surface p-6 shadow-card transition-colors hover:border-primary focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              <div className="flex h-12 items-center">
                {module.Logo ? (
                  <module.Logo height={48} />
                ) : (
                  <span className="flex items-center gap-3 font-display text-xl font-semibold text-foreground">
                    <span className="rounded-md bg-primary-soft p-2 text-primary">
                      <Icon className="size-6" aria-hidden />
                    </span>
                    {module.name}
                  </span>
                )}
              </div>
              <p className="text-sm text-muted-foreground">{module.description}</p>
              <span className="mt-auto flex items-center justify-between text-sm">
                <span className="text-muted-foreground">{role ? MODULE_ROLE_LABELS[role] : null}</span>
                <span className="flex items-center gap-1 font-medium text-primary">
                  Entrar
                  <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
                </span>
              </span>
            </Link>
          );
        })}
      </div>
    </>
  );
}
