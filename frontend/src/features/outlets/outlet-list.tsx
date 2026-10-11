"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, History, Plus, Store } from "lucide-react";
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
import { authApi, currentUserQueryKey } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { outletsApi, outletsQueryKey, type Outlet } from "@/lib/api/outlets";
import { cn } from "@/lib/cn";

import { outletHref } from "./outlet-detail";
import { OutletHistory } from "./outlet-history";

const STATUS_FILTERS = [
  { value: "active", label: "Activas" },
  { value: "all", label: "Todas" },
] as const;

type StatusFilter = (typeof STATUS_FILTERS)[number]["value"];

/** Same rule as the backend: upper case, without the spaces around it; anything else is refused, not fixed. */
const normalizeCode = (raw: string) => raw.trim().toUpperCase();
const clean = (text: string) => text.split(/\s+/).filter(Boolean).join(" ");

function outletError(error: unknown): string {
  if (!(error instanceof ApiError)) return "No se pudo guardar. Revisá la conexión y volvé a intentar.";
  if (error.code === "outlet_code_taken" || error.code === "outlet_name_taken") {
    const inactive =
      typeof error.details === "object" && error.details !== null && "active" in error.details
        ? (error.details as { active: unknown }).active === false
        : false;
    return inactive ? `${error.message} Está desactivada: reactivala desde la lista con «Todas».` : error.message;
  }
  if (error.status === 403) return "Solo un administrador de DistriCom administra las bocas.";
  if (error.status === 0 || error.status >= 500) return "No se pudo guardar. Volvé a intentar.";
  return error.message || "No se pudo guardar.";
}

function Problem({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">
      {message}
    </div>
  );
}

function OutletDialog({
  outlet,
  onClose,
  onSaved,
}: {
  outlet: Outlet | null;
  onClose: () => void;
  onSaved: (outlet: Outlet, created: boolean) => void;
}) {
  const codeId = useId();
  const nameId = useId();
  const descriptionId = useId();
  const [code, setCode] = useState(outlet?.code ?? "");
  const [name, setName] = useState(outlet?.name ?? "");
  const [description, setDescription] = useState(outlet?.description ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (input: { code: string; name: string; description: string }) => {
      if (!outlet) return outletsApi.create(input);
      return outletsApi.update(outlet.id, {
        ...(input.name !== outlet.name && { name: input.name }),
        ...(input.description !== (outlet.description ?? "") && { description: input.description }),
      });
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const value = { code: normalizeCode(code), name: clean(name), description: clean(description) };
    if (!outlet) {
      if (!value.code) return setProblem("Ingresá el código de la boca.");
      if (!/^[A-Z0-9-]{2,20}$/.test(value.code))
        return setProblem("El código tiene que tener entre 2 y 20 caracteres: letras, números o guiones.");
    }
    if (!value.name) return setProblem("Indicá el nombre de la boca de expendio.");
    if (outlet && value.name === outlet.name && value.description === (outlet.description ?? "")) return onClose();
    setProblem(null);
    save.mutate(value, { onSuccess: (saved) => onSaved(saved, !outlet) });
  };

  return (
    <Dialog
      title={outlet ? `Corregir ${outlet.code}` : "Nueva boca de expendio"}
      description={
        outlet
          ? "Es la misma boca: el código no cambia y lo ya registrado con ella la sigue nombrando."
          : "El código es la identidad de la boca y no se puede cambiar después. El nombre sí se corrige."
      }
      onClose={onClose}
    >
      <form className="space-y-4" onSubmit={submit} noValidate>
        <div className="space-y-1.5">
          <Label htmlFor={codeId}>Código</Label>
          <Input
            id={codeId}
            maxLength={20}
            autoCapitalize="characters"
            placeholder="BOCA-01"
            value={code}
            disabled={outlet !== null}
            aria-describedby={`${codeId}-hint`}
            onChange={(event) => setCode(event.target.value)}
          />
          <p id={`${codeId}-hint`} className="text-xs text-muted-foreground">
            {outlet ? "El código no se modifica." : "Letras, números o guiones, de 2 a 20. Se guarda en mayúsculas."}
          </p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={nameId}>Nombre</Label>
          <Input
            id={nameId}
            maxLength={100}
            placeholder="Por ejemplo Boca Centro"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={descriptionId}>Descripción (opcional)</Label>
          <Input
            id={descriptionId}
            maxLength={500}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </div>
        <Problem message={problem ?? (save.error ? outletError(save.error) : null)} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" disabled={save.isPending}>
            {save.isPending ? "Guardando…" : outlet ? "Guardar" : "Crear boca"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function ToggleDialog({
  outlet,
  onClose,
  onDone,
}: {
  outlet: Outlet;
  onClose: () => void;
  onDone: (outlet: Outlet) => void;
}) {
  const toggle = useMutation({ mutationFn: () => outletsApi.update(outlet.id, { active: !outlet.active }) });
  const deactivating = outlet.active;
  return (
    <Dialog
      title={`${deactivating ? "Desactivar" : "Reactivar"} ${outlet.code}`}
      description={outlet.name}
      onClose={onClose}
    >
      <div className="space-y-4 text-sm">
        {deactivating ? (
          <>
            <p>Una boca desactivada no puede abrir rendiciones nuevas ni sumar responsables.</p>
            <p>
              Lo que ya esté abierto se termina normalmente, y su historia y sus responsables siguen a la vista. Se
              puede reactivar cuando haga falta.
            </p>
          </>
        ) : (
          <p>La boca vuelve a ofrecerse para rendiciones y responsables nuevos.</p>
        )}
        <Problem message={toggle.error ? outletError(toggle.error) : null} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            variant={deactivating ? "danger" : "primary"}
            disabled={toggle.isPending}
            onClick={() => toggle.mutate(undefined, { onSuccess: onDone })}
          >
            {toggle.isPending ? "Guardando…" : deactivating ? "Desactivar" : "Reactivar"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function HistoryDialog({ outlet, onClose }: { outlet: Outlet; onClose: () => void }) {
  const detail = useQuery({ queryKey: [...outletsQueryKey, outlet.id], queryFn: () => outletsApi.get(outlet.id) });
  return (
    <Dialog title={`Historia de ${outlet.code}`} description={outlet.name} onClose={onClose}>
      {detail.isPending ? (
        <Skeleton className="h-24 w-full" aria-label="Cargando historia" />
      ) : detail.isError ? (
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      ) : (
        <OutletHistory history={detail.data.history} />
      )}
    </Dialog>
  );
}

function StateBadge({ active }: { active: boolean }) {
  return active ? <Badge tone="success">Activa</Badge> : <Badge>Desactivada</Badge>;
}

/**
 * Bocas de expendio (R7-1): every user consults them; only a platform admin creates, corrects and
 * (de)activates one. The code never changes and nothing is deleted.
 */
export function OutletList() {
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const isAdmin = me.data?.role === "admin";
  const [status, setStatus] = useState<StatusFilter>("active");
  const list = useQuery({
    queryKey: [...outletsQueryKey, "list", status],
    queryFn: () => outletsApi.list(status === "all"),
  });
  const [editing, setEditing] = useState<Outlet | "new" | null>(null);
  const [toggling, setToggling] = useState<Outlet | null>(null);
  const [viewing, setViewing] = useState<Outlet | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const done = (text: string) => {
    setNotice(text);
    void queryClient.invalidateQueries({ queryKey: outletsQueryKey });
  };

  const actions = (outlet: Outlet) => (
    <>
      <Link
        href={outletHref(outlet.id)}
        className="px-2 text-sm text-primary hover:underline"
        aria-label={`Ficha de ${outlet.code}`}
      >
        Ficha
      </Link>
      <Button variant="ghost" size="sm" aria-label={`Historia de ${outlet.code}`} onClick={() => setViewing(outlet)}>
        <History aria-hidden />
        Historia
      </Button>
      {isAdmin && (
        <>
          <Button variant="ghost" size="sm" aria-label={`Corregir ${outlet.code}`} onClick={() => setEditing(outlet)}>
            Corregir
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-label={`${outlet.active ? "Desactivar" : "Reactivar"} ${outlet.code}`}
            onClick={() => setToggling(outlet)}
          >
            {outlet.active ? "Desactivar" : "Reactivar"}
          </Button>
        </>
      )}
    </>
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
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
        {isAdmin ? (
          <Button onClick={() => setEditing("new")}>
            <Plus aria-hidden />
            Nueva boca
          </Button>
        ) : (
          me.data && <p className="text-sm text-muted-foreground">Solo un administrador de DistriCom las modifica.</p>
        )}
      </div>

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
          icon={Store}
          title={status === "active" ? "No hay bocas activas" : "Todavía no hay bocas de expendio"}
          description={
            isAdmin
              ? "Registrá las bocas de expendio. Cada una existe una sola vez en DistriCom."
              : "Pedile a un administrador de DistriCom que las registre."
          }
        />
      ) : (
        <>
          <Card className="hidden md:block" data-testid="outlet-table">
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Código</TableHead>
                    <TableHead>Nombre</TableHead>
                    <TableHead>Descripción</TableHead>
                    <TableHead>Estado</TableHead>
                    <TableHead>
                      <span className="sr-only">Acciones</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {list.data.map((outlet) => (
                    <TableRow key={outlet.id}>
                      <TableCell className={cn("font-medium", !outlet.active && "text-muted-foreground")}>
                        {outlet.code}
                      </TableCell>
                      <TableCell className={cn(!outlet.active && "text-muted-foreground")}>{outlet.name}</TableCell>
                      <TableCell className="text-muted-foreground">{outlet.description ?? "—"}</TableCell>
                      <TableCell>
                        <StateBadge active={outlet.active} />
                      </TableCell>
                      <TableCell className="text-right whitespace-nowrap">{actions(outlet)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
          <ul className="space-y-3 md:hidden" data-testid="outlet-cards" aria-label="Bocas de expendio">
            {list.data.map((outlet) => (
              <li key={outlet.id}>
                <Card>
                  <CardContent className="space-y-2 p-4">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className={cn("font-medium", !outlet.active && "text-muted-foreground")}>{outlet.code}</p>
                        <p className="break-words">{outlet.name}</p>
                        {outlet.description && (
                          <p className="text-sm break-words text-muted-foreground">{outlet.description}</p>
                        )}
                      </div>
                      <StateBadge active={outlet.active} />
                    </div>
                    <div className="-mx-2 flex flex-wrap items-center gap-1">{actions(outlet)}</div>
                  </CardContent>
                </Card>
              </li>
            ))}
          </ul>
        </>
      )}

      {editing && (
        <OutletDialog
          outlet={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(outlet, created) => {
            setEditing(null);
            done(created ? `Boca ${outlet.code} registrada.` : `${outlet.code} actualizada.`);
          }}
        />
      )}
      {toggling && (
        <ToggleDialog
          outlet={toggling}
          onClose={() => setToggling(null)}
          onDone={(outlet) => {
            setToggling(null);
            done(
              outlet.active
                ? `${outlet.code} está activa otra vez.`
                : `${outlet.code} quedó desactivada: no abre rendiciones nuevas ni suma responsables.`,
            );
          }}
        />
      )}
      {viewing && <HistoryDialog outlet={viewing} onClose={() => setViewing(null)} />}
    </div>
  );
}
