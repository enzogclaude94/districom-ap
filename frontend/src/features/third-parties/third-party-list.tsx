"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Plus, Search, Users } from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";

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
import { authApi, currentUserQueryKey } from "@/lib/api/auth";
import {
  normalizeTaxId,
  thirdPartiesApi,
  thirdPartiesQueryKey,
  thirdPartyHref,
  type ThirdParty,
  type ThirdPartyInput,
} from "@/lib/api/third-parties";
import { cn } from "@/lib/cn";

import { thirdPartyError, ThirdPartyForm } from "./third-party-form";

const STATUS_FILTERS = [
  { value: "active", label: "Activos" },
  { value: "all", label: "Todos" },
] as const;

type StatusFilter = (typeof STATUS_FILTERS)[number]["value"];

/** Only what changed travels: an untouched CUIT never triggers the duplicate checks again. */
function patchFrom(original: ThirdParty, body: ThirdPartyInput) {
  const { name, tax_id, notes, ...confirmation } = body;
  return {
    ...confirmation,
    ...(name !== original.name && { name }),
    ...((tax_id ?? "") !== (original.tax_id ?? "") && { tax_id: tax_id ?? "" }),
    ...((notes ?? "") !== (original.notes ?? "") && { notes: notes ?? "" }),
  };
}

function ThirdPartyDialog({
  thirdParty,
  onClose,
  onSaved,
}: {
  thirdParty: ThirdParty | null;
  onClose: () => void;
  onSaved: (thirdParty: ThirdParty, created: boolean) => void;
}) {
  return (
    <Dialog
      title={thirdParty ? `Corregir ${thirdParty.name}` : "Nuevo tercero"}
      description={
        thirdParty
          ? "Es el mismo tercero: los pagos ya registrados lo siguen nombrando, con los datos corregidos."
          : "Proveedores y otros a quienes se les paga. Cada uno existe una sola vez en DistriCom."
      }
      onClose={onClose}
    >
      <ThirdPartyForm
        thirdParty={thirdParty}
        submitLabel={thirdParty ? "Guardar" : "Registrar tercero"}
        save={(body) =>
          thirdParty ? thirdPartiesApi.update(thirdParty.id, patchFrom(thirdParty, body)) : thirdPartiesApi.create(body)
        }
        onSaved={(saved) => onSaved(saved, !thirdParty)}
        onCancel={onClose}
      />
    </Dialog>
  );
}

function Status({ thirdParty }: { thirdParty: ThirdParty }) {
  return thirdParty.active ? <Badge tone="success">Activo</Badge> : <Badge>Desactivado</Badge>;
}

/**
 * Terceros (R6-1): every user consults them; only a platform admin creates, corrects and (de)activates one.
 * Nothing is deleted or merged: a deactivated third party is not offered for new payments and the old ones
 * keep it. A Rendiciones operator can also register one while recording a payment (quick create).
 */
export function ThirdPartyList() {
  const searchId = useId();
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const isAdmin = me.data?.role === "admin";
  const [status, setStatus] = useState<StatusFilter>("active");
  const [search, setSearch] = useState("");
  const q = search.trim();
  const list = useQuery({
    queryKey: [...thirdPartiesQueryKey, "list", status, q],
    queryFn: () => thirdPartiesApi.list(status === "all", q),
    placeholderData: keepPreviousData,
  });
  const [editing, setEditing] = useState<ThirdParty | "new" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const done = (text: string) => {
    setNotice(text);
    void queryClient.invalidateQueries({ queryKey: thirdPartiesQueryKey });
  };
  const toggle = useMutation({
    mutationFn: (row: ThirdParty) => thirdPartiesApi.update(row.id, { active: !row.active }),
    onSuccess: (row) =>
      done(
        row.active
          ? `${row.name} está activo otra vez.`
          : `${row.name} quedó desactivado: no se ofrece para pagos nuevos.`,
      ),
  });

  const actions = (row: ThirdParty) => (
    <>
      <Link
        href={thirdPartyHref(row.id)}
        className="px-2 text-sm text-primary hover:underline"
        aria-label={`Ficha de ${row.name}`}
      >
        Ficha
      </Link>
      {isAdmin && (
        <>
          <Button variant="ghost" size="sm" aria-label={`Corregir ${row.name}`} onClick={() => setEditing(row)}>
            Corregir
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={toggle.isPending}
            aria-label={`${row.active ? "Desactivar" : "Activar"} ${row.name}`}
            onClick={() => toggle.mutate(row)}
          >
            {row.active ? "Desactivar" : "Activar"}
          </Button>
        </>
      )}
    </>
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1.5">
            <Label htmlFor={searchId}>Buscar</Label>
            <div className="relative">
              <Search
                className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                id={searchId}
                type="search"
                className="w-64 max-w-full pl-8"
                placeholder="Nombre o CUIT"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
          </div>
          <div role="group" aria-label="Estado" className="flex gap-1">
            {STATUS_FILTERS.map((filter) => (
              <Button
                key={filter.value}
                size="sm"
                variant={status === filter.value ? "primary" : "secondary"}
                aria-pressed={status === filter.value}
                onClick={() => setStatus(filter.value)}
              >
                {filter.label}
              </Button>
            ))}
          </div>
        </div>
        {isAdmin ? (
          <Button onClick={() => setEditing("new")}>
            <Plus aria-hidden />
            Nuevo tercero
          </Button>
        ) : (
          me.data && <p className="text-sm text-muted-foreground">Solo un administrador de DistriCom los modifica.</p>
        )}
      </div>

      {toggle.error && (
        <div role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
          {thirdPartyError(toggle.error)}
        </div>
      )}
      {notice && (
        <p role="status" className="flex items-center gap-2 text-sm text-primary">
          <CheckCircle2 className="size-4" aria-hidden />
          {notice}
        </p>
      )}

      {list.isPending ? (
        <div className="space-y-2" aria-busy="true">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.data.length === 0 ? (
        <EmptyState
          icon={Users}
          title={
            q
              ? "No hay terceros que coincidan"
              : status === "active"
                ? "No hay terceros activos"
                : "Todavía no hay terceros"
          }
          description={
            q
              ? `Nada coincide con «${q}»${/\d/.test(q) && normalizeTaxId(q).length < 3 ? " (para buscar por CUIT, escribí al menos 3 dígitos)" : ""}.`
              : isAdmin
                ? "Registrá los proveedores y otros terceros a quienes se les paga."
                : "Se registran desde aquí o al cargar un pago en Rendiciones."
          }
        />
      ) : (
        <>
          <ul className="space-y-2 sm:hidden" aria-label="Terceros">
            {list.data.map((row) => (
              <li key={row.id}>
                <Card>
                  <CardContent className="space-y-2 p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className={cn("font-medium break-words", !row.active && "text-muted-foreground")}>
                          {row.name}
                        </p>
                        <p className="text-xs text-muted-foreground tabular-nums">
                          {row.tax_id_display ? `CUIT ${row.tax_id_display}` : "Sin CUIT"}
                        </p>
                      </div>
                      <Status thirdParty={row} />
                    </div>
                    <div className="-ml-2 flex flex-wrap items-center">{actions(row)}</div>
                  </CardContent>
                </Card>
              </li>
            ))}
          </ul>
          <Card className="hidden sm:block">
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Nombre</TableHead>
                    <TableHead>CUIT</TableHead>
                    <TableHead>Estado</TableHead>
                    <TableHead>
                      <span className="sr-only">Acciones</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {list.data.map((row) => (
                    <TableRow key={row.id}>
                      <TableCell className={cn("font-medium", !row.active && "text-muted-foreground")}>
                        {row.name}
                      </TableCell>
                      <TableCell className="whitespace-nowrap tabular-nums">{row.tax_id_display ?? "—"}</TableCell>
                      <TableCell>
                        <Status thirdParty={row} />
                      </TableCell>
                      <TableCell className="text-right whitespace-nowrap">{actions(row)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}

      {editing && (
        <ThirdPartyDialog
          thirdParty={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(row, created) => {
            setEditing(null);
            done(created ? `Tercero ${row.name} registrado.` : `${row.name} actualizado.`);
          }}
        />
      )}
    </div>
  );
}
