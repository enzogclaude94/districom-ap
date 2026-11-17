"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2 } from "lucide-react";
import Link from "next/link";
import { useId, useState, type ReactNode } from "react";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { mappingsApi, mappingsQueryKey, type LinkTarget, type Mapping } from "@/lib/api/mappings";
import { formatDateTime } from "@/lib/format";

import {
  appearsIn,
  factsSentence,
  historyNote,
  kindLabel,
  METHOD_LABELS,
  n,
  OPEN,
  STATUS_LABELS,
  STATUS_TONES,
  subjectOf,
  SYSTEM_LABELS,
  totalFacts,
} from "./labels";

type Action = "link" | "reject" | "exclude";

const textareaClass =
  "w-full rounded-md border border-border bg-surface px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

function targetDetail(target: LinkTarget) {
  const parts = [
    target.store_sys_code && `Código STORE SYS ${target.store_sys_code}`,
    target.legal_name && target.legal_name !== target.name && target.legal_name,
    target.zone,
    target.functions.length ? target.functions.join(", ") : null,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

function TargetRow({ target, onPick, picked }: { target: LinkTarget; onPick?: () => void; picked?: boolean }) {
  const detail = targetDetail(target);
  return (
    <li className="flex items-center justify-between gap-3 rounded-md border border-border p-2 text-sm">
      <span>
        <span className="font-medium">{target.name}</span>
        {!target.active && <Badge className="ml-2">Inactivo</Badge>}
        {detail && <span className="block text-xs text-muted-foreground">{detail}</span>}
      </span>
      {onPick && (
        <Button size="sm" variant={picked ? "primary" : "secondary"} onClick={onPick} aria-pressed={picked}>
          {picked ? "Elegido" : "Elegir"}
        </Button>
      )}
    </li>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

/** What a link (confirm, reassign or relink) would complete, as the backend counts it for the chosen entity. */
function LinkImpact({ mapping, target }: { mapping: Mapping; target: LinkTarget }) {
  const impact = useQuery({
    queryKey: [...mappingsQueryKey, "impact", mapping.id, target.id],
    queryFn: () => mappingsApi.impact(mapping.id, target.id),
  });
  if (impact.isPending) return <Skeleton className="h-5 w-72" />;
  if (impact.isError) return <ErrorState error={impact.error} onRetry={() => void impact.refetch()} />;
  const sentence = factsSentence(impact.data.facts.map((f) => ({ label: f.label, count: f.current_changes_on_link })));
  const history = impact.data.on_link - impact.data.current_on_link;
  return (
    <p className="text-sm" role="status">
      {sentence
        ? `${sentence} históricos quedarán asociados a ${subjectOf(mapping)}.`
        : "Todavía no hay datos vigentes con este código: los próximos que lleguen quedarán asociados."}
      {historyNote(history)}
    </p>
  );
}

function UnlinkImpact({ mapping }: { mapping: Mapping }) {
  const impact = useQuery({
    queryKey: [...mappingsQueryKey, "impact", mapping.id],
    queryFn: () => mappingsApi.impact(mapping.id),
  });
  if (!impact.data) return null;
  const sentence = factsSentence(impact.data.facts.map((f) => ({ label: f.label, count: f.changes_on_unlink })));
  return sentence ? (
    <p className="text-sm">
      {sentence} quedarán sin {mapping.entity_type === "customer" ? "cliente" : "vendedor"}, con el código de origen. No
      se borra nada.
    </p>
  ) : null;
}

/**
 * `/comercial/datos/mapeos/[id]` (F1.5): where an external code comes from, its candidates (never chosen for the
 * person), what a decision would complete, and the decision. A decision taken meanwhile in another tab is never
 * overwritten: the backend refuses it and the page shows the current state.
 */
export function MappingDetailView({ mappingId }: { mappingId: number }) {
  const ids = { search: useId(), reason: useId() };
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const role = moduleRole(me.data, "commercial");
  const isOperator = roleAtLeast(role, "operator");
  const isAdmin = roleAtLeast(role, "admin");
  const mapping = useQuery({
    queryKey: [...mappingsQueryKey, "detail", mappingId],
    queryFn: () => mappingsApi.get(mappingId),
  });
  const [action, setAction] = useState<Action | null>(null);
  const [search, setSearch] = useState("");
  const [q, setQ] = useState<string | null>(null);
  const [target, setTarget] = useState<LinkTarget | null>(null);
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [changed, setChanged] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  const targets = useQuery({
    queryKey: [...mappingsQueryKey, "targets", mappingId, q],
    queryFn: () => mappingsApi.targets(mappingId, q ?? ""),
    enabled: action === "link" && q !== null,
  });

  const close = () => {
    setAction(null);
    setTarget(null);
    setReason("");
    setProblem(null);
    setQ(null);
    setSearch("");
  };
  const decide = useMutation({
    mutationFn: (current: Mapping) => {
      const expected_status = current.status;
      const clean = reason.trim() || undefined;
      if (action === "reject") return mappingsApi.reject(current.id, { reason: clean, expected_status });
      if (action === "exclude") return mappingsApi.exclude(current.id, { reason: clean ?? "", expected_status });
      const entity_id = (target as LinkTarget).id;
      if (current.status === "confirmed")
        return mappingsApi.reassign(current.id, { entity_id, reason: clean ?? "", expected_status });
      return mappingsApi.confirm(current.id, { entity_id, reason: clean, expected_status });
    },
    onMutate: () => {
      setProblem(null);
      setChanged(null);
    },
    onSuccess: (updated) => {
      queryClient.setQueryData([...mappingsQueryKey, "detail", mappingId], updated);
      void queryClient.invalidateQueries({ queryKey: mappingsQueryKey });
      const written = Object.values(updated.resolution?.facts_updated ?? {}).reduce((a, b) => a + b, 0);
      setResult(
        `Listo: el código quedó ${STATUS_LABELS[updated.status].toLowerCase()}.` +
          (written ? ` ${n(written)} datos históricos se actualizaron.` : ""),
      );
      close();
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        setChanged(error.message);
        close();
        void queryClient.invalidateQueries({ queryKey: mappingsQueryKey });
      } else setProblem(error instanceof Error ? error.message : "No se pudo guardar la decisión.");
    },
  });

  if (mapping.isPending) {
    return (
      <div className="space-y-3" aria-label="Cargando el código">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-40" />
      </div>
    );
  }
  if (mapping.isError) return <ErrorState error={mapping.error} onRetry={() => void mapping.refetch()} />;

  const m = mapping.data;
  const open = OPEN.includes(m.status);
  const total = totalFacts(m);
  const appears = appearsIn(m);
  // Who may do what (the backend checks it again).
  const canLink =
    (isOperator && (open || m.status === "rejected")) ||
    (isAdmin && (m.status === "confirmed" || m.status === "excluded"));
  const linkLabel =
    m.status === "confirmed" ? "Reasignar" : m.status === "excluded" ? "Vincular código excluido" : "Vincular";
  const canReject = isOperator && open;
  const canExclude = isOperator && m.status !== "excluded" && (m.status !== "confirmed" || isAdmin);
  const reasonRequired =
    action === "exclude" || (action === "link" && (m.status === "confirmed" || m.status === "excluded"));
  const ready = action === "link" ? !!target : true;
  const reasonMissing = reasonRequired && !reason.trim();

  return (
    <>
      <Link
        href="/comercial/datos/mapeos"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden /> Mapeos
      </Link>
      <PageHeader title={`${kindLabel(m)} ${m.external_id}`} description={m.external_label ?? undefined} />

      {changed && (
        <p role="alert" className="rounded-md bg-warning-soft p-3 text-sm text-warning">
          {changed} Ahora ves el estado actual.
        </p>
      )}
      {result && (
        <p role="status" className="rounded-md bg-success-soft p-3 text-sm text-success">
          {result}
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Origen</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-3 sm:grid-cols-3">
            <Fact label="Sistema">{SYSTEM_LABELS[m.source_system] ?? m.source_system}</Fact>
            <Fact label="Código fuente">{m.external_id}</Fact>
            <Fact label="Nombre en el origen">{m.external_label ?? "—"}</Fact>
            <Fact label="Estado">
              <Badge tone={STATUS_TONES[m.status]}>{STATUS_LABELS[m.status]}</Badge>
            </Fact>
            <Fact label="Aparece en">{appears ?? "Ningún dato cargado todavía"}</Fact>
            <Fact label="Vinculado con">{m.entity?.name ?? "—"}</Fact>
            <Fact label="Primera importación">
              {m.first_seen_job_id ? (
                <Link
                  href={`/comercial/datos/importaciones/${m.first_seen_job_id}`}
                  className="underline-offset-2 hover:underline"
                >
                  {m.first_seen_at ? formatDateTime(m.first_seen_at) : "Ver"}
                </Link>
              ) : (
                "—"
              )}
            </Fact>
            <Fact label="Última importación">
              {m.last_seen_job_id ? (
                <Link
                  href={`/comercial/datos/importaciones/${m.last_seen_job_id}`}
                  className="underline-offset-2 hover:underline"
                >
                  {m.last_seen_at ? formatDateTime(m.last_seen_at) : "Ver"}
                </Link>
              ) : (
                "—"
              )}
            </Fact>
            <Fact label="Cómo se vinculó">{m.method ? (METHOD_LABELS[m.method] ?? m.method) : "—"}</Fact>
            {m.decided_at && (
              <Fact label="Última decisión">
                {m.decided_by_name ?? "—"} · {formatDateTime(m.decided_at)}
                {m.decision_reason && <span className="block text-muted-foreground">Motivo: {m.decision_reason}</span>}
              </Fact>
            )}
          </dl>
          <p className="mt-3 text-xs text-muted-foreground">
            Referencia técnica: {m.entity_type} / {m.source_system} / {m.namespace}
          </p>
        </CardContent>
      </Card>

      {m.candidate_entities.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>{m.candidate_entities.length === 1 ? "Candidato propuesto" : "Candidatos"}</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="mb-2 text-sm text-muted-foreground">
              La importación los propuso por coincidencia exacta; ninguno se vincula sin que lo elijas.
            </p>
            <ul className="space-y-2">
              {m.candidate_entities.map((c) => (
                <TargetRow
                  key={c.id}
                  target={c}
                  picked={target?.id === c.id}
                  onPick={
                    canLink
                      ? () => {
                          setAction("link");
                          setTarget(c);
                        }
                      : undefined
                  }
                />
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {isOperator && (
        <Card>
          <CardHeader>
            <CardTitle>Decisión</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {action === null ? (
              <div className="flex flex-wrap gap-2">
                {canLink && <Button onClick={() => setAction("link")}>{linkLabel}</Button>}
                {canReject && (
                  <Button variant="secondary" onClick={() => setAction("reject")}>
                    Rechazar propuesta
                  </Button>
                )}
                {canExclude && (
                  <Button variant="secondary" onClick={() => setAction("exclude")}>
                    Excluir código
                  </Button>
                )}
                {!canLink && !canReject && !canExclude && (
                  <p className="text-sm text-muted-foreground">Solo un admin de Comercial puede cambiar este código.</p>
                )}
              </div>
            ) : (
              <form
                className="space-y-4"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!ready) return setProblem("Elegí con quién vincularlo.");
                  if (reasonMissing) return setProblem("Indicá el motivo de la decisión.");
                  decide.mutate(m);
                }}
              >
                {action === "link" && (
                  <>
                    <div className="space-y-1.5">
                      <Label htmlFor={ids.search}>
                        Buscar{" "}
                        {m.entity_type === "customer"
                          ? "cliente (nombre, razón social o código STORE SYS)"
                          : "empleado"}
                      </Label>
                      <div className="flex gap-2">
                        <Input
                          id={ids.search}
                          value={search}
                          maxLength={100}
                          onChange={(e) => setSearch(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.preventDefault();
                              setQ(search);
                            }
                          }}
                        />
                        <Button variant="secondary" onClick={() => setQ(search)}>
                          Buscar
                        </Button>
                      </div>
                      {m.entity_type === "employee" && (
                        <p className="text-xs text-muted-foreground">
                          Solo empleados activos; primero los preventistas. Acá no se crean empleados.
                        </p>
                      )}
                    </div>
                    {targets.isFetching && <Skeleton className="h-10" />}
                    {targets.data &&
                      (targets.data.items.length === 0 ? (
                        <p className="text-sm text-muted-foreground">No se encontró ninguno activo.</p>
                      ) : (
                        <ul className="space-y-2" aria-label="Resultados">
                          {targets.data.items.map((t) => (
                            <TargetRow key={t.id} target={t} picked={target?.id === t.id} onPick={() => setTarget(t)} />
                          ))}
                        </ul>
                      ))}
                    {target && (
                      <div className="rounded-md border border-border p-3">
                        <p className="text-sm">
                          Vincular con <strong>{target.name}</strong>
                        </p>
                        <LinkImpact mapping={m} target={target} />
                      </div>
                    )}
                  </>
                )}
                {action === "exclude" && (
                  <div className="space-y-1">
                    <p className="text-sm">
                      El código queda marcado como «nunca se vincula» (por ejemplo, BOCA DE EXPENDIO).
                    </p>
                    <UnlinkImpact mapping={m} />
                  </div>
                )}
                {action === "reject" && (
                  <p className="text-sm">
                    La propuesta queda rechazada. El código sigue sin vincular y se puede decidir más adelante;
                    {total > 0 ? ` sus ${n(total)} datos siguen pendientes.` : " no cambia ningún dato."}
                  </p>
                )}
                <div className="space-y-1.5">
                  <Label htmlFor={ids.reason}>Motivo {reasonRequired ? "(obligatorio)" : "(opcional)"}</Label>
                  <textarea
                    id={ids.reason}
                    rows={2}
                    maxLength={500}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    className={textareaClass}
                  />
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Button type="submit" disabled={decide.isPending}>
                    {decide.isPending && <Loader2 className="animate-spin" aria-hidden />}
                    {action === "link"
                      ? m.status === "confirmed"
                        ? "Confirmar reasignación"
                        : "Confirmar vínculo"
                      : action === "reject"
                        ? "Rechazar"
                        : "Excluir"}
                  </Button>
                  <Button variant="ghost" onClick={close} disabled={decide.isPending}>
                    Cancelar
                  </Button>
                  {problem && (
                    <span role="alert" className="text-sm text-danger">
                      {problem}
                    </span>
                  )}
                </div>
              </form>
            )}
          </CardContent>
        </Card>
      )}
    </>
  );
}
