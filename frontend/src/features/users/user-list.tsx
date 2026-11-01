"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Plus, Users } from "lucide-react";
import Link from "next/link";
import { useId, useState, type FormEvent } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Role } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import {
  usersApi,
  usersQueryKey,
  type TemporaryPassword,
  type UserStatusFilter,
  type UserSummary,
} from "@/lib/api/users";
import { formatDateTime } from "@/lib/format";

import { TemporaryPasswordNotice } from "./temporary-password";

const PAGE_SIZE = 50;
const STATUS_FILTERS: { value: UserStatusFilter; label: string }[] = [
  { value: "active", label: "Activos" },
  { value: "inactive", label: "Desactivados" },
  { value: "all", label: "Todos" },
];

export const PLATFORM_ROLE_LABELS: Record<Role, string> = {
  admin: "Administrador de plataforma",
  viewer: "Usuario",
};

export const userHref = (id: number) => `/usuarios/${id}`;

export function userError(error: unknown): string {
  if (!(error instanceof ApiError)) return "No se pudo guardar. Revisá la conexión y volvé a intentar.";
  if (error.status === 403) return "Solo un administrador de plataforma administra usuarios.";
  if (error.status === 0 || error.status >= 500) return "No se pudo guardar. Volvé a intentar.";
  return error.message || "No se pudo guardar.";
}

export function AccessSummary({ user }: { user: UserSummary }) {
  const modules =
    user.role === "admin"
      ? [<Badge key="all">Todos los módulos</Badge>]
      : user.modules.map((grant) => (
          <Badge key={grant.key} tone="info">
            {grant.name}
          </Badge>
        ));
  const areas = user.areas.map((grant) => (
    <Badge key={grant.key} tone="warning" title="Área protegida: permiso explícito">
      {grant.name}
    </Badge>
  ));
  if (modules.length + areas.length === 0) return <span className="text-muted-foreground">Sin accesos</span>;
  return <div className="flex flex-wrap gap-1">{[...modules, ...areas]}</div>;
}

function NewUserDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const emailId = useId();
  const nameId = useId();
  const roleId = useId();
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [created, setCreated] = useState<TemporaryPassword | null>(null);
  const create = useMutation({
    mutationFn: () => usersApi.create({ email: email.trim(), full_name: fullName.trim(), role }),
    onSuccess: (result) => {
      setCreated(result);
      void queryClient.invalidateQueries({ queryKey: usersQueryKey });
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (email.trim() && fullName.trim()) create.mutate();
  };

  return (
    <Dialog
      title={created ? "Usuario creado" : "Nuevo usuario"}
      description={
        created
          ? undefined
          : "Se crea con una contraseña temporal y sin accesos: después asignale módulos y áreas en su ficha."
      }
      onClose={onClose}
    >
      {created ? (
        <div className="space-y-4">
          <TemporaryPasswordNotice result={created} />
          <div className="flex justify-end gap-2">
            <Link
              href={userHref(created.user.id)}
              className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90"
            >
              Asignar accesos
            </Link>
          </div>
        </div>
      ) : (
        <form noValidate className="space-y-4" onSubmit={submit}>
          <div className="space-y-1.5">
            <Label htmlFor={nameId}>Nombre y apellido</Label>
            <Input id={nameId} value={fullName} onChange={(e) => setFullName(e.target.value)} maxLength={200} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={emailId}>Email</Label>
            <Input
              id={emailId}
              type="email"
              autoComplete="off"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              maxLength={254}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={roleId}>Rol de plataforma</Label>
            <select
              id={roleId}
              className="flex h-9 w-full rounded-md border border-border bg-surface px-3 text-sm"
              value={role}
              onChange={(e) => setRole(e.target.value as Role)}
            >
              <option value="viewer">Usuario (accede a los módulos que se le asignen)</option>
              <option value="admin">Administrador de plataforma (todos los módulos y usuarios)</option>
            </select>
          </div>
          {create.error && (
            <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
              {userError(create.error)}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={onClose}>
              Cancelar
            </Button>
            <Button type="submit" disabled={create.isPending || !email.trim() || !fullName.trim()}>
              {create.isPending ? "Creando…" : "Crear usuario"}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

export function NewUserButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>
        <Plus aria-hidden />
        Nuevo usuario
      </Button>
      {open && <NewUserDialog onClose={() => setOpen(false)} />}
    </>
  );
}

export function UserList() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<UserStatusFilter>("active");
  const [page, setPage] = useState(0);
  const filters = { status, q: search.trim() || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE };
  const query = useQuery({
    queryKey: [...usersQueryKey, "list", filters],
    queryFn: () => usersApi.list(filters),
    placeholderData: keepPreviousData,
  });

  if (query.error instanceof ApiError && query.error.status === 403) {
    return (
      <EmptyState
        icon={Lock}
        title="Sin permisos"
        description="Solo los administradores de plataforma administran usuarios."
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Input
          type="search"
          aria-label="Buscar por nombre o email"
          placeholder="Buscar por nombre o email…"
          className="max-w-xs"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            setPage(0);
          }}
        />
        <div role="group" aria-label="Estado" className="flex gap-1">
          {STATUS_FILTERS.map((filter) => (
            <Button
              key={filter.value}
              size="sm"
              variant={status === filter.value ? "primary" : "secondary"}
              aria-pressed={status === filter.value}
              onClick={() => {
                setStatus(filter.value);
                setPage(0);
              }}
            >
              {filter.label}
            </Button>
          ))}
        </div>
      </div>

      {query.isPending ? (
        <div className="space-y-2" aria-busy="true">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : query.data.items.length === 0 ? (
        <EmptyState
          icon={Users}
          title={filters.q ? "No hay usuarios con esa búsqueda" : "No hay usuarios en este estado"}
          description="Probá con otra búsqueda o cambiá el filtro de estado."
        />
      ) : (
        <Card>
          <CardContent className="p-0">
            <Table stacked aria-label="Usuarios">
              <TableHeader>
                <TableRow>
                  <TableHead>Nombre</TableHead>
                  <TableHead>Rol</TableHead>
                  <TableHead>Accesos</TableHead>
                  <TableHead>Último ingreso</TableHead>
                  <TableHead className="text-right">Estado</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {query.data.items.map((user) => (
                  <TableRow key={user.id}>
                    <TableCell data-label="Nombre">
                      <Link href={userHref(user.id)} className="font-medium text-primary hover:underline">
                        {user.full_name}
                      </Link>
                      <p className="text-xs text-muted-foreground">{user.email}</p>
                    </TableCell>
                    <TableCell data-label="Rol">{PLATFORM_ROLE_LABELS[user.role]}</TableCell>
                    <TableCell data-label="Accesos">
                      <AccessSummary user={user} />
                    </TableCell>
                    <TableCell data-label="Último ingreso" className="tabular-nums">
                      {user.last_login_at ? formatDateTime(user.last_login_at) : "Nunca"}
                    </TableCell>
                    <TableCell data-label="Estado" className="text-right">
                      <div className="flex flex-wrap justify-end gap-1">
                        {user.must_change_password && user.active && <Badge tone="warning">Contraseña temporal</Badge>}
                        <Badge tone={user.active ? "success" : "neutral"}>
                          {user.active ? "Activo" : "Desactivado"}
                        </Badge>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {query.data && query.data.total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span className="tabular-nums">
            {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, query.data.total)} de {query.data.total}
          </span>
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>
              Anterior
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={(page + 1) * PAGE_SIZE >= query.data.total}
              onClick={() => setPage(page + 1)}
            >
              Siguiente
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
