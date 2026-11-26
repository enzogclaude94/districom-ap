"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2 } from "lucide-react";
import Link from "next/link";
import { useId, useState, type ReactNode } from "react";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { qualityApi, qualityQueryKey, type IssueDetail, type IssueStatus } from "@/lib/api/quality";
import { formatDateTime } from "@/lib/format";

import { importHref } from "../imports/upload-form";
import { fixLink, n, originText, SEVERITY_LABELS, SEVERITY_TONES, STATUS_LABELS, STATUS_TONES } from "./labels";

type Decision = "ignored" | "resolved";

const NOTE_LENGTH = 500;
const textareaClass =
  "w-full rounded-md border border-border bg-surface px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

const list = (value: unknown) => (Array.isArray(value) ? value.map(String).join(", ") : String(value));

/** The safe facts the backend sends, in business words; anything else is not shown. */
function factRows(facts: IssueDetail["facts"]): { label: string; value: ReactNode }[] {
  const rows: { label: string; value: ReactNode }[] = [];
  const number = (key: string) => (typeof facts[key] === "number" ? n(facts[key]) : null);
  if (number("lines")) rows.push({ label: "Líneas afectadas", value: number("lines") });
  if (facts.codes !== undefined) rows.push({ label: "Códigos", value: list(facts.codes) });
  if (facts.missing !== undefined) rows.push({ label: "Columnas que faltan", value: list(facts.missing) });
  if (number("rows_with_price")) rows.push({ label: "Filas con precio", value: number("rows_with_price") });
  for (const [key, label] of [
    ["duplicate_of_job_id", "Mismo archivo que"],
    ["first_job_id", "Primera importación"],
  ] as const) {
    const job = facts[key];
    if (typeof job === "number")
      rows.push({
        label,
        value: (
          <Link href={importHref(job)} className="underline underline-offset-2">
            Importación #{job}
          </Link>
        ),
      });
  }
  return rows;
}

function closingText(issue: IssueDetail) {
  if (issue.status === "open") return null;
  const when = issue.resolved_at ? formatDateTime(issue.resolved_at) : null;
  if (issue.status === "resolved" && issue.resolved_automatically)
    return `Se resolvió solo${when ? ` el ${when}` : ""}: la causa se corrigió.`;
  const who = issue.resolved_by_name ? ` por ${issue.resolved_by_name}` : "";
  return `${STATUS_LABELS[issue.status]} a mano${who}${when ? ` el ${when}` : ""}.`;
}

/** `/comercial/datos/calidad/[id]` (F1.6): what happened, where it came from and how its cause is corrected. */
export function QualityDetailView({ issueId }: { issueId: number }) {
  const ids = { note: useId() };
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const role = moduleRole(me.data, "commercial");
  const detailKey = [...qualityQueryKey, "detail", issueId];
  const issue = useQuery({ queryKey: detailKey, queryFn: () => qualityApi.get(issueId) });
  const [deciding, setDeciding] = useState<Decision | null>(null);
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [changed, setChanged] = useState<IssueStatus | null>(null);

  const close = () => {
    setDeciding(null);
    setNote("");
    setProblem(null);
  };
  const decide = useMutation({
    mutationFn: ({ decision, text }: { decision: Decision; text: string }) =>
      decision === "ignored" ? qualityApi.ignore(issueId, text) : qualityApi.resolve(issueId, text),
    onMutate: () => {
      setProblem(null);
      setChanged(null);
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(detailKey, updated);
      void queryClient.invalidateQueries({ queryKey: qualityQueryKey });
      close();
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        const current = (error.details as { status?: IssueStatus } | null)?.status;
        setChanged(current ?? "open");
        close();
        void queryClient.invalidateQueries({ queryKey: qualityQueryKey });
      } else setProblem(error instanceof Error ? error.message : "No se pudo guardar la decisión.");
    },
  });

  if (issue.isPending) {
    return (
      <div className="space-y-3" aria-label="Cargando el problema">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-40" />
      </div>
    );
  }
  if (issue.isError) return <ErrorState error={issue.error} onRetry={() => void issue.refetch()} />;

  const i = issue.data;
  const open = i.status === "open";
  const canIgnore = open && roleAtLeast(role, "operator");
  const canResolve = open && roleAtLeast(role, "admin");
  const fix = fixLink(i);
  const origin = originText(i.source);
  const facts = factRows(i.facts);
  const closing = closingText(i);
  const submit = () => {
    if (!deciding) return;
    const text = note.trim();
    if (!text) {
      setProblem("Escribí una nota que explique por qué.");
      return;
    }
    decide.mutate({ decision: deciding, text });
  };

  return (
    <>
      <Link
        href="/comercial/datos/calidad"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline"
      >
        <ArrowLeft className="size-4" aria-hidden /> Calidad
      </Link>
      <PageHeader title={i.label} description={i.description} />
      <div className="flex flex-wrap gap-2">
        <Badge tone={SEVERITY_TONES[i.severity]}>{SEVERITY_LABELS[i.severity]}</Badge>
        <Badge tone={STATUS_TONES[i.status]}>{STATUS_LABELS[i.status]}</Badge>
        <Badge>{i.group.label}</Badge>
      </div>

      {changed && (
        <p role="alert" className="rounded-md bg-warning-soft p-3 text-sm text-warning">
          Otra persona ya cambió este problema: ahora está {STATUS_LABELS[changed].toLowerCase()}. No se guardó tu
          decisión.
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Qué pasó</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-3 sm:grid-cols-3">
            <Fact label="Afecta a">{i.subject ?? "—"}</Fact>
            <Fact label="Ocurrencias">
              <span className="tabular-nums">{n(i.occurrences)}</span>
            </Fact>
            <Fact label="Severidad">{SEVERITY_LABELS[i.severity]}</Fact>
            <Fact label="Primera vez">{formatDateTime(i.first_seen_at)}</Fact>
            <Fact label="Última vez">{formatDateTime(i.last_seen_at)}</Fact>
            <Fact label="Origen">
              {origin && i.source.job_id !== null ? (
                <Link href={importHref(i.source.job_id)} className="underline underline-offset-2">
                  {origin}
                </Link>
              ) : (
                "—"
              )}
            </Fact>
            {facts.map((f) => (
              <Fact key={f.label} label={f.label}>
                {f.value}
              </Fact>
            ))}
          </dl>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Cómo se corrige</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p>{i.action}</p>
          <p className="text-muted-foreground">
            {i.closes_when
              ? `Se cierra solo: ${i.closes_when.charAt(0).toLowerCase()}${i.closes_when.slice(1)}`
              : "No se cierra solo: si ya está revisado, se ignora con una nota."}
          </p>
          {fix && (
            <Link href={fix.href} className={buttonVariants()}>
              {fix.label}
            </Link>
          )}
        </CardContent>
      </Card>

      {closing && (
        <Card>
          <CardHeader>
            <CardTitle>Cierre</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <p>{closing}</p>
            {i.resolution_note && (
              <p>
                <span className="text-muted-foreground">Nota: </span>
                {i.resolution_note}
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {(canIgnore || canResolve) && (
        <Card>
          <CardHeader>
            <CardTitle>Cerrar sin corregir la causa</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {!deciding ? (
              <>
                <p className="text-sm text-muted-foreground">
                  Ignorarlo o marcarlo resuelto no cambia ningún dato. Si el problema vuelve a aparecer en otra
                  importación, se abre uno nuevo.
                </p>
                <div className="flex flex-wrap gap-2">
                  {canIgnore && (
                    <Button variant="secondary" onClick={() => setDeciding("ignored")}>
                      Ignorar
                    </Button>
                  )}
                  {canResolve && (
                    <Button variant="secondary" onClick={() => setDeciding("resolved")}>
                      Marcar resuelto
                    </Button>
                  )}
                </div>
              </>
            ) : (
              <form
                className="space-y-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  submit();
                }}
              >
                <p className="text-sm">
                  {deciding === "ignored"
                    ? "El problema queda ignorado. Usalo cuando ya está revisado y es lo esperado."
                    : "El problema queda resuelto a mano. Usalo cuando la causa ya se corrigió por otro camino."}
                </p>
                <div className="space-y-1.5">
                  <Label htmlFor={ids.note}>Nota (obligatoria)</Label>
                  <textarea
                    id={ids.note}
                    rows={2}
                    maxLength={NOTE_LENGTH}
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    className={textareaClass}
                  />
                  <p className="text-xs text-muted-foreground">No escribas teléfonos, CUIT ni direcciones.</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Button type="submit" disabled={decide.isPending}>
                    {decide.isPending && <Loader2 className="animate-spin" aria-hidden />}
                    {deciding === "ignored" ? "Confirmar ignorar" : "Confirmar resuelto"}
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

      {i.dedupe_key && (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">Referencia técnica</summary>
          <p className="mt-1 font-mono">
            {i.code} · {i.dedupe_key}
            {i.entity_type && ` · ${i.entity_type} ${i.entity_id ?? ""}`}
          </p>
        </details>
      )}
    </>
  );
}
