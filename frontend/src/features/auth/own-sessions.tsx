"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MonitorSmartphone } from "lucide-react";
import { useRouter } from "next/navigation";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ACCESS_ORIGIN_LABELS, authApi, ownSessionsQueryKey, type OpenSession } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";

/** A short device description from the browser's User-Agent: enough to recognize it, never parsed for access. */
export function deviceLabel(userAgent: string | null): string {
  if (!userAgent) return "Dispositivo desconocido";
  const browser = /Edg\//.test(userAgent)
    ? "Edge"
    : /Firefox\//.test(userAgent)
      ? "Firefox"
      : /Chrome\//.test(userAgent)
        ? "Chrome"
        : /Safari\//.test(userAgent)
          ? "Safari"
          : null;
  const system = /Android/.test(userAgent)
    ? "Android"
    : /iPhone|iPad/.test(userAgent)
      ? "iOS"
      : /Windows/.test(userAgent)
        ? "Windows"
        : /Mac OS X/.test(userAgent)
          ? "macOS"
          : /Linux/.test(userAgent)
            ? "Linux"
            : null;
  if (browser && system) return `${browser} en ${system}`;
  return browser ?? system ?? userAgent.slice(0, 60);
}

export function originLabel(session: Pick<OpenSession, "access_origin">) {
  return session.access_origin ? ACCESS_ORIGIN_LABELS[session.access_origin] : "Sin dato";
}

/** The user's open sessions, with the IP and network they were opened from, and the way to close them. */
export function OwnSessions() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const sessions = useQuery({ queryKey: ownSessionsQueryKey, queryFn: authApi.sessions });
  const revoke = useMutation({
    mutationFn: (session: OpenSession) => authApi.revokeSession(session.id),
    onSuccess: (_, session) => {
      if (session.current) {
        queryClient.clear();
        router.replace("/login");
        return;
      }
      void queryClient.invalidateQueries({ queryKey: ownSessionsQueryKey });
    },
  });
  const revokeOthers = useMutation({
    mutationFn: authApi.revokeOtherSessions,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ownSessionsQueryKey }),
  });
  const failure = revoke.error ?? revokeOthers.error;

  if (sessions.isPending) return <Skeleton className="h-24 w-full" aria-label="Cargando sesiones" />;
  if (sessions.isError) return <ErrorState error={sessions.error} onRetry={() => void sessions.refetch()} />;
  if (sessions.data.length === 0) {
    return <EmptyState icon={MonitorSmartphone} title="Sin sesiones abiertas" description="No hay sesiones activas." />;
  }
  const others = sessions.data.filter((session) => !session.current).length;
  return (
    <div className="space-y-3">
      <Table stacked aria-label="Sesiones abiertas">
        <TableHeader>
          <TableRow>
            <TableHead>Dispositivo</TableHead>
            <TableHead>Conexión</TableHead>
            <TableHead>IP</TableHead>
            <TableHead>Inicio</TableHead>
            <TableHead>Última actividad</TableHead>
            <TableHead>
              <span className="sr-only">Acciones</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sessions.data.map((session) => (
            <TableRow key={session.id}>
              <TableCell data-label="Dispositivo">
                <span className="font-medium">{deviceLabel(session.user_agent)}</span>
                {session.current && (
                  <Badge tone="info" className="ml-2">
                    Esta sesión
                  </Badge>
                )}
              </TableCell>
              <TableCell data-label="Conexión">{originLabel(session)}</TableCell>
              <TableCell data-label="IP" className="font-mono text-xs">
                {session.ip ?? "—"}
              </TableCell>
              <TableCell data-label="Inicio">{formatDateTime(session.created_at)}</TableCell>
              <TableCell data-label="Última actividad">{formatDateTime(session.last_seen_at)}</TableCell>
              <TableCell data-label="">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={revoke.isPending}
                  aria-label={
                    session.current
                      ? "Cerrar esta sesión"
                      : `Cerrar la sesión de ${deviceLabel(session.user_agent)} iniciada el ${formatDateTime(session.created_at)}`
                  }
                  onClick={() => revoke.mutate(session)}
                >
                  {session.current ? "Cerrar esta sesión" : "Cerrar"}
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {failure && (
        <p role="alert" className="text-sm text-danger">
          {failure instanceof ApiError ? failure.message : "No se pudo cerrar la sesión."}
        </p>
      )}
      {others > 0 && (
        <Button variant="secondary" disabled={revokeOthers.isPending} onClick={() => revokeOthers.mutate()}>
          {others === 1 ? "Cerrar la otra sesión" : `Cerrar las otras ${others} sesiones`}
        </Button>
      )}
      <p className="text-xs text-muted-foreground">
        La conexión y la IP son informativas: se registran para auditoría y nunca dan ni quitan permisos.
      </p>
    </div>
  );
}
