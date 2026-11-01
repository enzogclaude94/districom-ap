"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useId, useState, type FormEvent } from "react";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { deviceLabel, originLabel } from "@/features/auth/own-sessions";
import { authApi, currentUserQueryKey, type ModuleRole, type Role } from "@/lib/api/auth";
import {
  usersApi,
  usersQueryKey,
  type AccessCatalog,
  type TemporaryPassword,
  type UserActivity,
  type UserDetail as UserDetailData,
} from "@/lib/api/users";
import { formatDateTime } from "@/lib/format";

import { TemporaryPasswordNotice } from "./temporary-password";
import { PLATFORM_ROLE_LABELS, userError } from "./user-list";

const MODULE_ROLE_OPTIONS: { value: ModuleRole | ""; label: string }[] = [
  { value: "", label: "Sin acceso" },
  { value: "viewer", label: "Lector" },
  { value: "operator", label: "Operador" },
  { value: "admin", label: "Administrador" },
];

const SELECT = "flex h-9 w-full rounded-md border border-border bg-surface px-3 text-sm disabled:opacity-60 sm:w-48";

const ACTIVITY_LABELS: Record<string, string> = {
  LOGIN: "Ingresó",
  "platform.login.failed": "Ingreso rechazado",
  "platform.user.created": "Usuario creado",
  "platform.user.updated": "Datos modificados",
  "platform.user.activated": "Usuario activado",
  "platform.user.deactivated": "Usuario desactivado",
  "platform.password.reset": "Contraseña temporal generada",
  "platform.password.changed": "Cambió su contraseña",
  "platform.module_role.granted": "Acceso asignado",
  "platform.module_role.revoked": "Acceso quitado",
  "platform.user.sessions_revoked": "Sesiones cerradas por un administrador",
  "platform.session.revoked": "Cerró una sesión",
  "platform.sessions.revoked": "Cerró sus otras sesiones",
};

function activityDetail(event: UserActivity): string | null {
  const payload = event.payload;
  const after = (payload.after ?? null) as Record<string, unknown> | null;
  const before = (payload.before ?? null) as Record<string, unknown> | null;
  if (event.event_type.startsWith("platform.module_role.")) {
    const grant = after ?? before;
    return grant ? `${String(grant.module)}${after ? `: ${String(after.role)}` : ""}` : null;
  }
  if (event.event_type === "platform.user.updated" && after) {
    const parts = [];
    if (after.role) parts.push(`rol ${String(before?.role)} → ${String(after.role)}`);
    if (after.email_changed) parts.push("email");
    if (after.full_name_changed) parts.push("nombre");
    return parts.join(", ");
  }
  if (typeof payload.access_origin === "string") {
    const where = originLabel({ access_origin: payload.access_origin as never });
    return `${where}${payload.ip ? ` (${String(payload.ip)})` : ""}${payload.reason ? ` · ${String(payload.reason)}` : ""}`;
  }
  return null;
}

function EditUserDialog({ user, own, onClose }: { user: UserDetailData; own: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const nameId = useId();
  const emailId = useId();
  const roleId = useId();
  const [fullName, setFullName] = useState(user.full_name);
  const [email, setEmail] = useState(user.email);
  const [role, setRole] = useState<Role>(user.role);
  const save = useMutation({
    mutationFn: () =>
      usersApi.update(user.id, {
        ...(fullName.trim() !== user.full_name && { full_name: fullName.trim() }),
        ...(email.trim() !== user.email && { email: email.trim() }),
        ...(role !== user.role && { role }),
      }),
    onSuccess: (detail) => {
      queryClient.setQueryData([...usersQueryKey, user.id], detail);
      void queryClient.invalidateQueries({ queryKey: usersQueryKey });
      if (own) void queryClient.invalidateQueries({ queryKey: currentUserQueryKey });
      onClose();
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };
  return (
    <Dialog title="Editar usuario" onClose={onClose}>
      <form noValidate className="space-y-4" onSubmit={submit}>
        <div className="space-y-1.5">
          <Label htmlFor={nameId}>Nombre y apellido</Label>
          <Input id={nameId} value={fullName} onChange={(e) => setFullName(e.target.value)} maxLength={200} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={emailId}>Email</Label>
          <Input id={emailId} type="email" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={254} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={roleId}>Rol de plataforma</Label>
          <select
            id={roleId}
            className="flex h-9 w-full rounded-md border border-border bg-surface px-3 text-sm disabled:opacity-60"
            value={role}
            disabled={own}
            onChange={(e) => setRole(e.target.value as Role)}
          >
            <option value="viewer">{PLATFORM_ROLE_LABELS.viewer}</option>
            <option value="admin">{PLATFORM_ROLE_LABELS.admin}</option>
          </select>
          {own && <p className="text-xs text-muted-foreground">Tu propio rol lo cambia otro administrador.</p>}
        </div>
        {save.error && (
          <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
            {userError(save.error)}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" disabled={save.isPending || !fullName.trim() || !email.trim()}>
            {save.isPending ? "Guardando…" : "Guardar"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

type Confirmation = "deactivate" | "reset";

function ConfirmDialog({ user, kind, onClose }: { user: UserDetailData; kind: Confirmation; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [result, setResult] = useState<TemporaryPassword | null>(null);
  const action = useMutation({
    mutationFn: async () => {
      if (kind === "reset") return usersApi.resetPassword(user.id);
      await usersApi.deactivate(user.id);
      return null;
    },
    onSuccess: (temporary) => {
      void queryClient.invalidateQueries({ queryKey: usersQueryKey });
      if (temporary) setResult(temporary);
      else onClose();
    },
  });
  const texts =
    kind === "reset"
      ? {
          title: "Resetear contraseña",
          body: `Se genera una contraseña temporal para ${user.full_name} y se cierran todas sus sesiones abiertas. Su contraseña actual deja de funcionar.`,
          confirm: "Generar contraseña temporal",
        }
      : {
          title: "Desactivar usuario",
          body: `${user.full_name} no podrá ingresar y se cierran todas sus sesiones abiertas. Su historia y sus accesos se conservan; podés activarlo de nuevo.`,
          confirm: "Desactivar",
        };
  return (
    <Dialog title={texts.title} onClose={onClose}>
      {result ? (
        <div className="space-y-4">
          <TemporaryPasswordNotice result={result} />
          <div className="flex justify-end">
            <Button onClick={onClose}>Listo</Button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <p className="text-sm">{texts.body}</p>
          {action.error && (
            <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
              {userError(action.error)}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={onClose}>
              Cancelar
            </Button>
            <Button variant="danger" disabled={action.isPending} onClick={() => action.mutate()}>
              {texts.confirm}
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}

function AccessEditor({ user, catalog }: { user: UserDetailData; catalog: AccessCatalog }) {
  const queryClient = useQueryClient();
  const change = useMutation({
    mutationFn: ({ key, role }: { key: string; role: ModuleRole | null }) => usersApi.setAccess(user.id, key, role),
    onSuccess: (detail) => {
      queryClient.setQueryData([...usersQueryKey, user.id], detail);
      void queryClient.invalidateQueries({ queryKey: [...usersQueryKey, "list"] });
      void queryClient.invalidateQueries({ queryKey: currentUserQueryKey });
    },
  });
  const platformAdmin = user.role === "admin";
  const roleOf = (key: string) =>
    user.modules.find((grant) => grant.key === key)?.role ?? user.areas.find((grant) => grant.key === key)?.role ?? "";

  return (
    <div className="space-y-5">
      {catalog.modules.map((module) => {
        const hasModule = platformAdmin || roleOf(module.key) !== "";
        const areas = catalog.areas.filter((area) => area.module_key === module.key);
        return (
          <div key={module.key} className="space-y-3 rounded-md border border-border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <label htmlFor={`access-${module.key}`} className="font-medium">
                {module.name}
              </label>
              {platformAdmin ? (
                <Badge>Administrador (por rol de plataforma)</Badge>
              ) : (
                <select
                  id={`access-${module.key}`}
                  className={SELECT}
                  value={roleOf(module.key)}
                  disabled={change.isPending || !user.active}
                  onChange={(e) =>
                    change.mutate({ key: module.key, role: (e.target.value || null) as ModuleRole | null })
                  }
                >
                  {MODULE_ROLE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              )}
            </div>
            {areas.length > 0 && (
              <div className="space-y-2 border-t border-border pt-3">
                <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <ShieldAlert className="size-3.5" aria-hidden />
                  Áreas protegidas: el acceso al módulo no las incluye; cada una se da aparte
                  {platformAdmin ? ", también a un administrador de plataforma" : ""}.
                </p>
                {areas.map((area) => (
                  <div key={area.key} className="flex flex-wrap items-center justify-between gap-2 pl-3">
                    <label htmlFor={`access-${area.key}`} className="text-sm">
                      {area.name}
                    </label>
                    <select
                      id={`access-${area.key}`}
                      className={SELECT}
                      value={roleOf(area.key)}
                      disabled={change.isPending || !hasModule || !user.active}
                      title={hasModule ? undefined : `Primero dale un rol en ${module.name}`}
                      onChange={(e) =>
                        change.mutate({ key: area.key, role: (e.target.value || null) as ModuleRole | null })
                      }
                    >
                      {MODULE_ROLE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
      {change.error && (
        <p role="alert" className="text-sm text-danger">
          {userError(change.error)}
        </p>
      )}
      {!user.active && <p className="text-sm text-muted-foreground">Activá el usuario para cambiar sus accesos.</p>}
    </div>
  );
}

function UserSessions({ user }: { user: UserDetailData }) {
  const queryClient = useQueryClient();
  const refresh = () => void queryClient.invalidateQueries({ queryKey: [...usersQueryKey, user.id] });
  const revoke = useMutation({ mutationFn: (id: number) => usersApi.revokeSession(user.id, id), onSuccess: refresh });
  const revokeAll = useMutation({ mutationFn: () => usersApi.revokeAllSessions(user.id), onSuccess: refresh });
  if (user.sessions.length === 0) return <p className="text-sm text-muted-foreground">Sin sesiones abiertas.</p>;
  const failure = revoke.error ?? revokeAll.error;
  return (
    <div className="space-y-3">
      <Table stacked aria-label="Sesiones del usuario">
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
          {user.sessions.map((session) => (
            <TableRow key={session.id}>
              <TableCell data-label="Dispositivo">{deviceLabel(session.user_agent)}</TableCell>
              <TableCell data-label="Conexión">{originLabel(session)}</TableCell>
              <TableCell data-label="IP" className="font-mono text-xs">
                {session.ip ?? "—"}
              </TableCell>
              <TableCell data-label="Inicio">{formatDateTime(session.created_at)}</TableCell>
              <TableCell data-label="Última actividad">{formatDateTime(session.last_seen_at)}</TableCell>
              <TableCell data-label="">
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={revoke.isPending}
                  aria-label={`Cerrar la sesión de ${deviceLabel(session.user_agent)} iniciada el ${formatDateTime(session.created_at)}`}
                  onClick={() => revoke.mutate(session.id)}
                >
                  Cerrar
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {failure && (
        <p role="alert" className="text-sm text-danger">
          {userError(failure)}
        </p>
      )}
      <Button variant="secondary" disabled={revokeAll.isPending} onClick={() => revokeAll.mutate()}>
        Cerrar todas sus sesiones
      </Button>
    </div>
  );
}

export function UserDetail({ userId }: { userId: number }) {
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const detail = useQuery({ queryKey: [...usersQueryKey, userId], queryFn: () => usersApi.get(userId) });
  const catalog = useQuery({ queryKey: [...usersQueryKey, "catalog"], queryFn: usersApi.catalog });
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState<Confirmation | null>(null);
  const activate = useMutation({
    mutationFn: () => usersApi.activate(userId),
    onSuccess: (data) => {
      queryClient.setQueryData([...usersQueryKey, userId], data);
      void queryClient.invalidateQueries({ queryKey: [...usersQueryKey, "list"] });
    },
  });

  return (
    <>
      <Link href="/usuarios" className="inline-flex items-center gap-1 text-sm text-primary hover:underline">
        <ArrowLeft className="size-4" aria-hidden />
        Usuarios
      </Link>
      {detail.isPending ? (
        <Skeleton className="h-24 w-full" aria-label="Cargando usuario" />
      ) : detail.isError ? (
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      ) : (
        (() => {
          const user = detail.data;
          const own = me.data?.id === user.id;
          return (
            <>
              <PageHeader
                title={user.full_name}
                description={`${user.email} · ${PLATFORM_ROLE_LABELS[user.role]}`}
                actions={
                  <div className="flex flex-wrap items-center gap-2">
                    {user.must_change_password && user.active && <Badge tone="warning">Contraseña temporal</Badge>}
                    <Badge tone={user.active ? "success" : "neutral"}>{user.active ? "Activo" : "Desactivado"}</Badge>
                    <Button variant="secondary" size="sm" onClick={() => setEditing(true)}>
                      Editar
                    </Button>
                    {!own && user.active && (
                      <>
                        <Button variant="secondary" size="sm" onClick={() => setConfirming("reset")}>
                          Resetear contraseña
                        </Button>
                        <Button variant="danger" size="sm" onClick={() => setConfirming("deactivate")}>
                          Desactivar
                        </Button>
                      </>
                    )}
                    {!user.active && (
                      <Button size="sm" disabled={activate.isPending} onClick={() => activate.mutate()}>
                        Activar
                      </Button>
                    )}
                  </div>
                }
              />
              {own && (
                <p className="text-sm text-muted-foreground">
                  Es tu cuenta: tu contraseña y tus sesiones se manejan en{" "}
                  <Link href="/cuenta" className="text-primary hover:underline">
                    Mi cuenta
                  </Link>
                  ; tu rol y tu estado los cambia otro administrador.
                </p>
              )}
              {activate.error && (
                <p role="alert" className="text-sm text-danger">
                  {userError(activate.error)}
                </p>
              )}
              <div className="grid gap-6 lg:grid-cols-2">
                <Card>
                  <CardHeader>
                    <CardTitle>Accesos</CardTitle>
                    <CardDescription>Rol por módulo y permisos sobre áreas protegidas.</CardDescription>
                  </CardHeader>
                  <CardContent>
                    {catalog.isPending ? (
                      <Skeleton className="h-24 w-full" />
                    ) : catalog.isError ? (
                      <ErrorState error={catalog.error} onRetry={() => void catalog.refetch()} />
                    ) : (
                      <AccessEditor user={user} catalog={catalog.data} />
                    )}
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader>
                    <CardTitle>Cuenta</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-1 text-sm">
                    <p>Creado: {formatDateTime(user.created_at)}</p>
                    <p>Último ingreso: {user.last_login_at ? formatDateTime(user.last_login_at) : "nunca"}</p>
                    <p>
                      Contraseña cambiada: {user.password_changed_at ? formatDateTime(user.password_changed_at) : "—"}
                    </p>
                  </CardContent>
                </Card>
              </div>
              <Card>
                <CardHeader>
                  <CardTitle>Sesiones abiertas</CardTitle>
                  <CardDescription>La conexión y la IP son informativas: nunca dan ni quitan permisos.</CardDescription>
                </CardHeader>
                <CardContent>
                  <UserSessions user={user} />
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Actividad</CardTitle>
                  <CardDescription>
                    Ingresos y cambios sobre la cuenta, del más reciente al más antiguo.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  {user.activity.length === 0 ? (
                    <p className="text-sm text-muted-foreground">Sin actividad registrada.</p>
                  ) : (
                    <ul className="space-y-2 text-sm" aria-label="Actividad de la cuenta">
                      {user.activity.map((event, index) => {
                        const extra = activityDetail(event);
                        return (
                          <li key={index} className="flex flex-wrap gap-x-2 border-b border-border pb-2 last:border-0">
                            <span className="text-muted-foreground tabular-nums">
                              {formatDateTime(event.occurred_at)}
                            </span>
                            <span className="font-medium">{ACTIVITY_LABELS[event.event_type] ?? event.event_type}</span>
                            {extra && <span>{extra}</span>}
                            {event.actor_name && event.actor_id !== user.id && (
                              <span className="text-muted-foreground">por {event.actor_name}</span>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </CardContent>
              </Card>
              {editing && <EditUserDialog user={user} own={own} onClose={() => setEditing(false)} />}
              {confirming && <ConfirmDialog user={user} kind={confirming} onClose={() => setConfirming(null)} />}
            </>
          );
        })()
      )}
    </>
  );
}
