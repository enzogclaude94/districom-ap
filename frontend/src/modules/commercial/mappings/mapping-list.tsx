"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2, Loader2 } from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import {
  mappingsApi,
  mappingsQueryKey,
  type BatchResult,
  type Mapping,
  type MappingFilters,
  type MappingStatus,
} from "@/lib/api/mappings";
import { formatDateTime } from "@/lib/format";

import {
  appearsIn,
  factsSentence,
  KINDS,
  kindLabel,
  mappingHref,
  n,
  OPEN,
  STATUS_LABELS,
  STATUS_TONES,
  SUMMARY_ORDER,
  SYSTEM_LABELS,
  totalFacts,
} from "./labels";

const PAGE_SIZE = 25;
const selectClass =
  "flex h-9 w-full rounded-md border border-border bg-surface px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

const written = (result: BatchResult) => Object.values(result.facts_updated).reduce((a, b) => a + b, 0);

/** A mapping that a batch can confirm: open, with exactly one proposed candidate (never chosen for the person). */
function proposal(mapping: Mapping) {
  if (!OPEN.includes(mapping.status) || mapping.candidate_entities.length !== 1) return null;
  return mapping.candidate_entities[0];
}

function BatchReview({
  selected,
  onClose,
  onDone,
}: {
  selected: Mapping[];
  onClose: () => void;
  onDone: (result: BatchResult) => void;
}) {
  const queryClient = useQueryClient();
  const batch = useMutation({
    mutationFn: () =>
      mappingsApi.batchConfirm(
        selected.map((m) => ({
          mapping_id: m.id,
          entity_id: (proposal(m) as NonNullable<ReturnType<typeof proposal>>).id,
        })),
      ),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: mappingsQueryKey });
      onDone(result);
    },
    onError: () => void queryClient.invalidateQueries({ queryKey: mappingsQueryKey }),
  });
  const facts = factsSentence(
    Object.values(
      selected
        .flatMap((m) => m.facts)
        .reduce<Record<string, { label: string; count: number }>>((acc, f) => {
          acc[f.label] = { label: f.label, count: (acc[f.label]?.count ?? 0) + f.current - f.current_linked };
          return acc;
        }, {}),
    ),
  );
  const failed = batch.error instanceof ApiError ? batch.error : null;
  return (
    <Dialog
      title="Revisá los vínculos antes de confirmarlos"
      description="Se confirman todos o ninguno. Cada código queda vinculado con el candidato propuesto que ves acá."
      onClose={onClose}
      className="max-w-2xl"
    >
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Código</TableHead>
            <TableHead>Se vincula con</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {selected.map((m) => (
            <TableRow key={m.id}>
              <TableCell>
                {kindLabel(m)} {m.external_id}
                {m.external_label && m.external_label !== m.external_id && (
                  <span className="block text-xs text-muted-foreground">{m.external_label}</span>
                )}
              </TableCell>
              <TableCell>{proposal(m)?.name}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p className="mt-3 text-sm">
        {facts ? `${facts} históricos quedarán asociados.` : "Estos códigos todavía no tienen hechos cargados."}
      </p>
      {failed && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {failed.message} Revisá el estado actual de la lista.
        </p>
      )}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose} disabled={batch.isPending}>
          Volver
        </Button>
        <Button onClick={() => batch.mutate()} disabled={batch.isPending || !!failed}>
          {batch.isPending && <Loader2 className="animate-spin" aria-hidden />}
          Confirmar {n(selected.length)} {selected.length === 1 ? "vínculo" : "vínculos"}
        </Button>
      </div>
    </Dialog>
  );
}

/**
 * `/comercial/datos/mapeos` (F1.5): the queue of external codes to link with a customer or an employee, the oldest
 * pending first. Only imports create these codes; here a person decides them.
 */
export function MappingList() {
  const ids = { status: useId(), system: useId(), kind: useId(), q: useId() };
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const canDecide = roleAtLeast(moduleRole(me.data, "commercial"), "operator");
  const [status, setStatus] = useState<MappingStatus | "">("pending");
  const [system, setSystem] = useState("");
  const [kind, setKind] = useState("");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const [picked, setPicked] = useState<Map<number, Mapping>>(new Map());
  const [reviewing, setReviewing] = useState(false);
  const [done, setDone] = useState<BatchResult | null>(null);

  const chosenKind = KINDS.find((k) => k.key === kind);
  const filters: MappingFilters = {
    status: status || undefined,
    source_system: chosenKind?.sourceSystem ?? (system || undefined),
    entity_type: chosenKind?.entityType,
    namespace: chosenKind?.namespace,
    q: q || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const list = useQuery({
    queryKey: [...mappingsQueryKey, "list", filters],
    queryFn: () => mappingsApi.list(filters),
    placeholderData: keepPreviousData,
  });
  const summary = useQuery({
    queryKey: [...mappingsQueryKey, "summary", { ...filters, status: undefined, offset: 0 }],
    queryFn: () => mappingsApi.summary(filters),
  });
  const items = list.data?.items ?? [];
  const reset = () => {
    setPage(0);
    setPicked(new Map());
  };
  const toggle = (mapping: Mapping) => {
    const next = new Map(picked);
    if (next.has(mapping.id)) next.delete(mapping.id);
    else next.set(mapping.id, mapping);
    setPicked(next);
  };
  const kindsForSystem = KINDS.filter((k) => !system || k.sourceSystem === system);

  return (
    <>
      <PageHeader
        title="Mapeos"
        description="Códigos de AXUM y STORE SYS que todavía hay que vincular con un cliente o un empleado. Al decidir, se completan los datos históricos que traen ese código."
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5" aria-label="Resumen por estado">
        {SUMMARY_ORDER.map((s) => (
          <button
            key={s}
            type="button"
            aria-pressed={status === s}
            onClick={() => {
              setStatus(status === s ? "" : s);
              reset();
            }}
            className="rounded-lg border border-border bg-surface p-3 text-left hover:bg-muted aria-pressed:border-primary"
          >
            <span className="block text-xs text-muted-foreground">{STATUS_LABELS[s]}s</span>
            <span className="text-2xl font-semibold tabular-nums">
              {summary.data ? n(summary.data.counts[s] ?? 0) : "…"}
            </span>
          </button>
        ))}
      </div>

      <Card>
        <CardHeader className="space-y-3">
          <CardTitle>Códigos</CardTitle>
          <div className="grid gap-3 sm:grid-cols-4">
            <div className="space-y-1.5">
              <Label htmlFor={ids.status}>Estado</Label>
              <select
                id={ids.status}
                className={selectClass}
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value as MappingStatus | "");
                  reset();
                }}
              >
                <option value="">Todos</option>
                {SUMMARY_ORDER.map((s) => (
                  <option key={s} value={s}>
                    {STATUS_LABELS[s]}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={ids.system}>Sistema</Label>
              <select
                id={ids.system}
                className={selectClass}
                value={system}
                onChange={(e) => {
                  setSystem(e.target.value);
                  if (KINDS.find((k) => k.key === kind)?.sourceSystem !== e.target.value) setKind("");
                  reset();
                }}
              >
                <option value="">Todos</option>
                <option value="STORE_SYS">STORE SYS</option>
                <option value="AXUM">AXUM</option>
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={ids.kind}>Tipo de código</Label>
              <select
                id={ids.kind}
                className={selectClass}
                value={kind}
                onChange={(e) => {
                  setKind(e.target.value);
                  reset();
                }}
              >
                <option value="">Todos</option>
                {kindsForSystem.map((k) => (
                  <option key={k.key} value={k.key}>
                    {k.label}
                  </option>
                ))}
              </select>
            </div>
            <form
              className="space-y-1.5"
              onSubmit={(e) => {
                e.preventDefault();
                setQ(search.trim());
                reset();
              }}
            >
              <Label htmlFor={ids.q}>Buscar código o nombre</Label>
              <Input id={ids.q} value={search} maxLength={100} onChange={(e) => setSearch(e.target.value)} />
            </form>
          </div>
        </CardHeader>
        <CardContent>
          {done && (
            <p role="status" className="mb-3 rounded-md bg-success-soft p-3 text-sm text-success">
              {done.confirmed === 1 ? "Se confirmó 1 vínculo." : `Se confirmaron ${n(done.confirmed)} vínculos.`}
              {written(done) > 0 && ` ${n(written(done))} datos históricos quedaron asociados.`}
            </p>
          )}
          {canDecide && picked.size > 0 && (
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-md border border-border p-3 text-sm">
              <span>
                {n(picked.size)} {picked.size === 1 ? "código elegido" : "códigos elegidos"} con candidato propuesto
              </span>
              <div className="flex gap-2">
                <Button variant="ghost" size="sm" onClick={() => setPicked(new Map())}>
                  Quitar selección
                </Button>
                <Button size="sm" onClick={() => setReviewing(true)}>
                  Revisar y confirmar los candidatos propuestos
                </Button>
              </div>
            </div>
          )}
          {list.isPending ? (
            <div className="space-y-2" aria-label="Cargando los códigos">
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
            </div>
          ) : list.isError ? (
            <ErrorState error={list.error} onRetry={() => void list.refetch()} />
          ) : items.length === 0 ? (
            <EmptyState
              icon={Link2}
              title={status === "pending" ? "No hay códigos pendientes" : "No hay códigos con estos filtros"}
              description="Los códigos aparecen cuando una importación trae un cliente o un vendedor que todavía no está vinculado."
            />
          ) : (
            <>
              <Table stacked>
                <TableHeader>
                  <TableRow>
                    {canDecide && (
                      <TableHead className="w-8">
                        <span className="sr-only">Elegir</span>
                      </TableHead>
                    )}
                    <TableHead>Código</TableHead>
                    <TableHead>Tipo</TableHead>
                    <TableHead>Estado</TableHead>
                    <TableHead>Vinculado con</TableHead>
                    <TableHead>Aparece en</TableHead>
                    <TableHead>Última importación</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((m) => {
                    const proposed = proposal(m);
                    return (
                      <TableRow key={m.id}>
                        {canDecide && (
                          <TableCell data-label="Elegir">
                            <input
                              type="checkbox"
                              aria-label={`Elegir ${kindLabel(m)} ${m.external_id}`}
                              disabled={!proposed}
                              title={proposed ? undefined : "Sin un único candidato propuesto: decidilo en su detalle"}
                              checked={picked.has(m.id)}
                              onChange={() => toggle(m)}
                            />
                          </TableCell>
                        )}
                        <TableCell data-label="Código">
                          <Link href={mappingHref(m.id)} className="font-medium underline-offset-2 hover:underline">
                            {m.external_id}
                          </Link>
                          {m.external_label && m.external_label !== m.external_id && (
                            <span className="block text-xs text-muted-foreground">{m.external_label}</span>
                          )}
                        </TableCell>
                        <TableCell data-label="Tipo">{kindLabel(m)}</TableCell>
                        <TableCell data-label="Estado">
                          <Badge tone={STATUS_TONES[m.status]}>{STATUS_LABELS[m.status]}</Badge>
                        </TableCell>
                        <TableCell data-label="Vinculado con">
                          {m.entity?.name ??
                            (proposed ? (
                              <span className="text-muted-foreground">Propuesto: {proposed.name}</span>
                            ) : m.candidate_entities.length > 1 ? (
                              <span className="text-muted-foreground">{n(m.candidate_entities.length)} candidatos</span>
                            ) : (
                              "—"
                            ))}
                        </TableCell>
                        <TableCell data-label="Aparece en" className="tabular-nums">
                          {totalFacts(m) > 0 ? appearsIn(m) : "—"}
                        </TableCell>
                        <TableCell data-label="Última importación" className="tabular-nums">
                          {m.last_seen_at ? formatDateTime(m.last_seen_at) : "—"}
                          <span className="block text-xs text-muted-foreground">{SYSTEM_LABELS[m.source_system]}</span>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
              {list.data && list.data.total > PAGE_SIZE && (
                <div className="mt-3 flex items-center justify-between gap-2 text-sm text-muted-foreground">
                  <span className="tabular-nums">
                    Página {page + 1} de {Math.ceil(list.data.total / PAGE_SIZE)}
                  </span>
                  <div className="flex gap-2">
                    <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
                      Anterior
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={(page + 1) * PAGE_SIZE >= list.data.total}
                      onClick={() => setPage(page + 1)}
                    >
                      Siguiente
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {reviewing && (
        <BatchReview
          selected={[...picked.values()]}
          onClose={() => setReviewing(false)}
          onDone={(result) => {
            setReviewing(false);
            setPicked(new Map());
            setDone(result);
          }}
        />
      )}
    </>
  );
}
