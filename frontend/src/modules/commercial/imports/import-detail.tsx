"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, CheckCircle2, Loader2, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useState } from "react";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/client";
import {
  importsApi,
  importsQueryKey,
  type ImportDecision,
  type ImportJobDetail,
  type ImportMessage,
  type RowCounts,
} from "@/lib/api/imports";
import { formatDate, formatDateTime } from "@/lib/format";

import {
  daysText,
  NO_PURCHASE_SUBJECT,
  replacementOf,
  replacementText,
  SALES_SUBJECT,
  typeFacts,
  type Fact,
  type Subject,
} from "./import-summary";
import { ImportRows } from "./import-rows";
import {
  NO_PURCHASE_TYPE,
  SALES_TYPE,
  SEVERITY_LABELS,
  SEVERITY_TONES,
  STATUS_LABELS,
  STATUS_TONES,
  typeLabel,
} from "./labels";
import { importHref } from "./upload-form";

const n = (value: number) => value.toLocaleString("es-AR");
/** No answer from the server, or a proxy that gave up: the confirmation may still have finished. */
const NO_ANSWER = new Set([0, 502, 503, 504]);

function rowsWithWarnings(c: RowCounts) {
  return Math.max(0, c.processed - c.valid - c.rejected - c.duplicate - c.pending_resolution);
}

function useElapsed(running: boolean) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!running) return;
    const started = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => {
      clearInterval(timer);
      setSeconds(0);
    };
  }, [running]);
  return seconds;
}

function FactList({ facts }: { facts: Fact[] }) {
  return (
    <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
      {facts.map((fact) => (
        <div key={fact.label} className="space-y-0.5">
          <dt className="text-xs text-muted-foreground">{fact.label}</dt>
          <dd className={fact.attention ? "font-medium text-warning" : "font-medium"}>{fact.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Numbers({ items }: { items: { label: string; value: number; tone?: "warning" | "danger" }[] }) {
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      {items.map((item) => (
        <div key={item.label} className="rounded-md border border-border px-3 py-2">
          <dt className="text-xs text-muted-foreground">{item.label}</dt>
          <dd
            className={
              "text-lg font-semibold tabular-nums " +
              (item.value && item.tone === "danger" ? "text-danger" : item.value && item.tone ? "text-warning" : "")
            }
          >
            {n(item.value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Attention({
  messages,
  selected,
  onSelect,
}: {
  messages: ImportMessage[];
  selected: string | undefined;
  onSelect: (code: string | undefined) => void;
}) {
  if (messages.length === 0) return <p className="text-sm text-muted-foreground">Ninguna fila tiene observaciones.</p>;
  return (
    <ul className="divide-y divide-border">
      {messages.map((m) => (
        <li key={`${m.code}-${m.field ?? ""}`} className="flex flex-wrap items-center justify-between gap-2 py-2">
          <span className="flex items-center gap-2 text-sm">
            <Badge tone={SEVERITY_TONES[m.severity]}>{SEVERITY_LABELS[m.severity]}</Badge>
            {m.message}
          </span>
          <span className="flex items-center gap-3 text-sm">
            <span className="text-muted-foreground tabular-nums">
              {n(m.rows)} {m.rows === 1 ? "fila" : "filas"}
            </span>
            <Button
              variant="ghost"
              size="sm"
              aria-pressed={selected === m.code}
              onClick={() => onSelect(selected === m.code ? undefined : m.code)}
            >
              {selected === m.code ? "Ver todas" : "Ver filas"}
            </Button>
          </span>
        </li>
      ))}
    </ul>
  );
}

function DecisionForm({
  pending,
  subject,
  onDecide,
}: {
  pending: boolean;
  subject: Subject;
  onDecide: (decision: ImportDecision, reason: string) => void;
}) {
  const ids = { reason: useId(), name: useId() };
  const [decision, setDecision] = useState<ImportDecision | "">("");
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (!decision) return setProblem(`Elegí qué hacer con las ${subject.loaded} ya cargadas.`);
        if (!reason.trim()) return setProblem("Escribí el motivo de la decisión: queda registrado.");
        setProblem(null);
        onDecide(decision, reason.trim());
      }}
    >
      <fieldset disabled={pending} className="space-y-2">
        <legend className="text-sm font-medium">Qué hacer con las {subject.loaded} ya cargadas de esos días</legend>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="radio"
            name={ids.name}
            className="mt-1"
            checked={decision === "replace"}
            onChange={() => setDecision("replace")}
          />
          <span>
            <strong>Reemplazarlas.</strong> Las {subject.loaded} ya cargadas de los días en conflicto quedan como
            reemplazadas y valen las de este archivo.
          </span>
        </label>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="radio"
            name={ids.name}
            className="mt-1"
            checked={decision === "additional"}
            onChange={() => setDecision("additional")}
          />
          <span>
            <strong>Cargar como adicionales.</strong> Se suman a las ya cargadas. Usalo solo si sabés que este archivo
            no repite {subject.loaded} que ya están.
          </span>
        </label>
        <div className="space-y-1.5 pt-1">
          <Label htmlFor={ids.reason}>Motivo (obligatorio)</Label>
          <textarea
            id={ids.reason}
            maxLength={500}
            rows={2}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="w-full rounded-md border border-border bg-surface px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          />
        </div>
      </fieldset>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={pending}>
          {pending && <Loader2 className="animate-spin" aria-hidden />}
          Aplicar decisión y cargar
        </Button>
        {problem && (
          <span role="alert" className="text-sm text-danger">
            {problem}
          </span>
        )}
      </div>
    </form>
  );
}

/**
 * `/comercial/datos/importaciones/[id]` (F1.3b): what DistriCom found in the file, what needs attention, what
 * confirming would do, and the result once loaded. Confirming is one synchronous request: while it runs, the page
 * says so and nothing can be sent twice.
 */
export function ImportDetailView({ jobId }: { jobId: number }) {
  const queryClient = useQueryClient();
  const job = useQuery({
    queryKey: [...importsQueryKey, "job", jobId],
    queryFn: () => importsApi.get(jobId),
    // Another session is loading it: follow it until it ends.
    refetchInterval: (query) => (query.state.data?.status === "processing" ? 2000 : false),
  });
  const messages = useQuery({
    queryKey: [...importsQueryKey, "job", jobId, "messages"],
    queryFn: () => importsApi.messages(jobId),
    enabled: !!job.data,
  });
  const issues = useQuery({
    queryKey: [...importsQueryKey, "job", jobId, "issues"],
    queryFn: () => importsApi.issues(jobId),
    enabled: !!job.data && !["pending", "processing"].includes(job.data.status),
  });
  const [code, setCode] = useState<string | undefined>();
  const [blocked, setBlocked] = useState<string | null>(null);
  const [lost, setLost] = useState(false);
  const [askCancel, setAskCancel] = useState(false);
  const [showRows, setShowRows] = useState(false);

  const refresh = () => void queryClient.invalidateQueries({ queryKey: importsQueryKey });
  const confirm = useMutation({
    mutationFn: (decision?: { decision: ImportDecision; reason: string }) => importsApi.confirm(jobId, decision),
    onMutate: () => {
      setBlocked(null);
      setLost(false);
    },
    onSuccess: (detail) => {
      queryClient.setQueryData([...importsQueryKey, "job", jobId], detail);
      refresh();
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "import_decision_required") setBlocked(error.message);
      if (error instanceof ApiError && NO_ANSWER.has(error.status)) setLost(true);
      refresh();
    },
  });
  const cancel = useMutation({
    mutationFn: () => importsApi.cancel(jobId),
    onSuccess: (detail) => {
      queryClient.setQueryData([...importsQueryKey, "job", jobId], detail);
      setAskCancel(false);
      refresh();
    },
  });
  const working = confirm.isPending || cancel.isPending;
  const elapsed = useElapsed(confirm.isPending);

  if (job.isPending) {
    return (
      <div className="space-y-3" aria-label="Cargando la importación">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-40" />
      </div>
    );
  }
  if (job.isError) return <ErrorState error={job.error} onRetry={() => void job.refetch()} />;

  const detail: ImportJobDetail = job.data;
  const c = detail.counts;
  const isSales = detail.import_type === SALES_TYPE;
  const isNoPurchase = detail.import_type === NO_PURCHASE_TYPE;
  const subject = isNoPurchase ? NO_PURCHASE_SUBJECT : SALES_SUBJECT;
  const plan = replacementOf(detail);
  const days = detail.scope?.days ?? [];
  const needsDecision = detail.status === "pending" && (plan?.action === "blocked" || blocked !== null);
  const done = detail.status === "completed" || detail.status === "completed_with_warnings";
  const superseded = Number(detail.metadata.superseded_lines ?? detail.metadata.superseded_events ?? 0) || c.superseded;
  const declaredScope = detail.scope?.declared;

  const facts: Fact[] = [
    { label: "Reporte", value: typeLabel(detail.import_type, detail.import_type_name) },
    ...(detail.snapshot_date ? [{ label: "Día de la foto", value: formatDate(detail.snapshot_date) }] : []),
    ...(detail.period_from
      ? [
          {
            label: "Período",
            value: `${formatDate(detail.period_from)} al ${formatDate(detail.period_to)}${
              days.length
                ? ` · ${n(days.length)} ${days.length === 1 ? "día" : "días"} con ${isNoPurchase ? "datos" : "ventas"}`
                : ""
            }`,
          },
        ]
      : []),
    ...(isSales
      ? [
          {
            label: "Alcance declarado",
            value:
              declaredScope === "FULL"
                ? "Completo, sin filtros"
                : declaredScope === "FILTERED"
                  ? `Filtrado${detail.scope?.note ? `: ${detail.scope.note}` : ""}`
                  : "Sin declarar (se trata como filtrado)",
            attention: declaredScope !== "FULL",
          },
        ]
      : []),
    { label: "Subido por", value: `${detail.created_by_name} · ${formatDateTime(detail.created_at)}` },
    ...(detail.confirmed_by_name && detail.confirmed_at
      ? [{ label: "Confirmado por", value: `${detail.confirmed_by_name} · ${formatDateTime(detail.confirmed_at)}` }]
      : []),
  ];

  return (
    <>
      <Link
        href="/comercial/datos/importaciones"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden />
        Importaciones
      </Link>
      <PageHeader
        title={detail.file.original_filename}
        description={`${typeLabel(detail.import_type, detail.import_type_name)} · importación #${detail.id}`}
        actions={<Badge tone={STATUS_TONES[detail.status]}>{STATUS_LABELS[detail.status]}</Badge>}
      />

      {detail.status === "failed" && (
        <Card className="border-danger/30">
          <CardContent className="space-y-2 pt-4">
            <p className="flex items-center gap-2 font-medium text-danger">
              <AlertTriangle className="size-4" aria-hidden />
              {detail.failure_detail ?? "No se pudo leer el archivo."}
            </p>
            <p className="text-sm text-muted-foreground">
              No se cargó nada.{" "}
              {detail.failure_reason === "type_not_detected" || detail.failure_reason === "ambiguous_type"
                ? "Volvé a subirlo eligiendo qué reporte es."
                : "Revisá que sea el archivo exportado de STORE SYS, sin modificar, y volvé a subirlo."}
            </p>
            <Link href="/comercial/datos/importaciones" className="text-sm underline">
              Subir otro archivo
            </Link>
          </CardContent>
        </Card>
      )}

      {detail.status === "duplicate" && (
        <Card>
          <CardContent className="space-y-1 pt-4 text-sm">
            <p className="font-medium">Este mismo archivo ya se importó con los mismos datos declarados.</p>
            <p className="text-muted-foreground">
              No se cargó nada de nuevo.{" "}
              {detail.duplicate_of_job_id && (
                <Link href={importHref(detail.duplicate_of_job_id)} className="underline">
                  Ver la importación #{detail.duplicate_of_job_id}
                </Link>
              )}
            </p>
          </CardContent>
        </Card>
      )}

      {detail.status !== "failed" && (
        <Card>
          <CardHeader>
            <CardTitle>Qué encontró DistriCom</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            <FactList facts={facts} />
            {detail.status !== "duplicate" && (
              <>
                <Numbers
                  items={[
                    { label: "Filas con datos", value: c.processed },
                    { label: "Correctas", value: c.valid },
                    { label: "Con avisos", value: rowsWithWarnings(c), tone: "warning" },
                    { label: "Pendientes de vincular", value: c.pending_resolution, tone: "warning" },
                    { label: "Rechazadas", value: c.rejected, tone: "danger" },
                    { label: "Repetidas", value: c.duplicate, tone: "warning" },
                  ]}
                />
                <FactList facts={typeFacts(detail)} />
              </>
            )}
          </CardContent>
        </Card>
      )}

      {detail.status === "pending" && (
        <Card className={needsDecision ? "border-warning/40" : undefined}>
          <CardHeader>
            <CardTitle>Qué pasa si confirmás</CardTitle>
            <CardDescription>
              Se cargan las filas correctas y las que tienen avisos. Las pendientes de vincular se guardan igual y se
              completan cuando se vinculen; las rechazadas no se cargan.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {(isSales || isNoPurchase) && plan && (
              <div className="space-y-1 text-sm">
                <p className="font-medium">{replacementText(plan, days, subject).title}</p>
                {replacementText(plan, days, subject).reasons.map((reason) => (
                  <p key={reason} className="text-muted-foreground">
                    {reason}
                  </p>
                ))}
              </div>
            )}
            {blocked && plan?.action !== "blocked" && <p className="text-sm font-medium">{blocked}</p>}

            {confirm.isPending && (
              <p role="status" className="flex items-center gap-2 rounded-md bg-info-soft px-3 py-2 text-sm text-info">
                <Loader2 className="size-4 animate-spin" aria-hidden />
                Cargando {n(c.processed)} filas… {elapsed > 0 && `(${elapsed} s)`} No cierres esta página.
              </p>
            )}
            {lost && (
              <p role="alert" className="rounded-md bg-warning-soft px-3 py-2 text-sm text-warning">
                No recibimos la respuesta del servidor. La importación puede haberse cargado igual: revisá el estado de
                arriba antes de volver a intentar.
              </p>
            )}
            {confirm.isError && !blocked && !lost && <ErrorState error={confirm.error} />}
            {cancel.isError && <ErrorState error={cancel.error} />}

            {needsDecision ? (
              detail.can_decide ? (
                <div className="space-y-3 rounded-md border border-warning/40 p-3">
                  <p className="flex items-center gap-2 text-sm font-medium">
                    <ShieldAlert className="size-4 text-warning" aria-hidden />
                    Hace falta tu decisión como administrador.
                  </p>
                  <DecisionForm
                    pending={confirm.isPending}
                    subject={subject}
                    onDecide={(decision, reason) => confirm.mutate({ decision, reason })}
                  />
                </div>
              ) : (
                <p className="flex items-center gap-2 text-sm">
                  <ShieldAlert className="size-4 text-warning" aria-hidden />
                  Esta importación necesita la decisión de un administrador de Comercial. Queda esperando, sin cargar
                  nada.
                </p>
              )
            ) : (
              detail.can_confirm && (
                <div className="flex flex-wrap gap-2">
                  <Button onClick={() => confirm.mutate(undefined)} disabled={working}>
                    {confirm.isPending ? (
                      <Loader2 className="animate-spin" aria-hidden />
                    ) : (
                      <CheckCircle2 aria-hidden />
                    )}
                    {confirm.isPending ? "Cargando…" : "Confirmar y cargar"}
                  </Button>
                </div>
              )
            )}

            {detail.can_confirm && (
              <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
                {askCancel ? (
                  <>
                    <span className="text-sm">¿Descartar esta importación? No se carga nada.</span>
                    <Button variant="danger" size="sm" onClick={() => cancel.mutate()} disabled={working}>
                      Sí, descartar
                    </Button>
                    <Button variant="secondary" size="sm" onClick={() => setAskCancel(false)} disabled={working}>
                      No
                    </Button>
                  </>
                ) : (
                  <Button variant="secondary" size="sm" onClick={() => setAskCancel(true)} disabled={working}>
                    Descartar
                  </Button>
                )}
              </div>
            )}
            {!detail.can_confirm && !needsDecision && (
              <p className="text-sm text-muted-foreground">
                Solo un operador o administrador de Comercial puede confirmarla.
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {detail.status === "processing" && (
        <p role="status" className="flex items-center gap-2 rounded-md bg-info-soft px-3 py-2 text-sm text-info">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          Se está cargando. Esta página se actualiza sola.
        </p>
      )}

      {done && (
        <Card>
          <CardHeader>
            <CardTitle>Resultado</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <Numbers
              items={[
                { label: "Nuevas", value: c.inserted },
                { label: "Actualizadas", value: c.updated },
                { label: "Sin cambios", value: c.unchanged },
                {
                  label: isSales
                    ? "Ventas anteriores reemplazadas"
                    : isNoPurchase
                      ? "No compras anteriores reemplazadas"
                      : "Reemplazadas",
                  value: superseded,
                },
                { label: "No cargadas", value: c.rejected, tone: "danger" },
                { label: "Problemas abiertos", value: issues.data?.length ?? 0, tone: "warning" },
              ]}
            />
            {isSales && days.length > 0 && <p className="text-sm text-muted-foreground">Ventas de {daysText(days)}.</p>}
          </CardContent>
        </Card>
      )}

      {detail.status !== "failed" && detail.status !== "duplicate" && (
        <Card>
          <CardHeader>
            <CardTitle>Qué requiere atención</CardTitle>
            <CardDescription>Observaciones de la revisión, por tipo. Elegí una para ver sus filas.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {messages.isPending ? (
              <Skeleton className="h-16" />
            ) : messages.isError ? (
              <ErrorState error={messages.error} onRetry={() => void messages.refetch()} />
            ) : (
              <Attention messages={messages.data} selected={code} onSelect={setCode} />
            )}
            {code || showRows ? (
              <ImportRows jobId={jobId} code={code} />
            ) : (
              <Button variant="secondary" size="sm" onClick={() => setShowRows(true)}>
                Ver todas las filas del archivo
              </Button>
            )}
          </CardContent>
        </Card>
      )}
    </>
  );
}
