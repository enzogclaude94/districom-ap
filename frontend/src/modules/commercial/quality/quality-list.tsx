"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useId, useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  qualityApi,
  qualityQueryKey,
  type Counted,
  type IssueFilters,
  type IssueStatus,
  type Severity,
} from "@/lib/api/quality";
import { formatDateTime } from "@/lib/format";

import {
  n,
  originText,
  qualityHref,
  SEVERITIES,
  SEVERITY_LABELS,
  SEVERITY_PLURALS,
  SEVERITY_TONES,
  STATUS_LABELS,
  STATUS_PLURALS,
  STATUS_TONES,
} from "./labels";

const PAGE_SIZE = 25;
const selectClass =
  "flex h-9 w-full rounded-md border border-border bg-surface px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

/** A card is one filter: open + severity, or a closed status. */
interface CardKey {
  status: IssueStatus;
  severity?: Severity;
}

function SummaryCard({
  title,
  counted,
  pressed,
  onClick,
}: {
  title: string;
  counted: Counted | undefined;
  pressed: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className="rounded-lg border border-border bg-surface p-3 text-left hover:bg-muted aria-pressed:border-primary"
    >
      <span className="block text-xs text-muted-foreground">{title}</span>
      <span className="text-2xl font-semibold tabular-nums">{counted ? n(counted.issues) : "…"}</span>
      <span className="block text-xs text-muted-foreground tabular-nums">
        {counted ? `${n(counted.occurrences)} ${counted.occurrences === 1 ? "ocurrencia" : "ocurrencias"}` : " "}
      </span>
    </button>
  );
}

const positiveId = (value: string | null) => {
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : undefined;
};

/**
 * `/comercial/datos/calidad` (F1.6): the problems the imports found in the data, the most serious and oldest first.
 * A problem counts once however many times it was seen (its occurrences). The cause is corrected where it lives.
 */
export function QualityList() {
  const params = useSearchParams();
  const ids = {
    status: useId(),
    severity: useId(),
    group: useId(),
    code: useId(),
    from: useId(),
    to: useId(),
    q: useId(),
  };
  const [status, setStatus] = useState<IssueStatus | "">("open");
  const [severity, setSeverity] = useState<Severity | "">("");
  const [group, setGroup] = useState("");
  const [code, setCode] = useState("");
  const [seenFrom, setSeenFrom] = useState("");
  const [seenTo, setSeenTo] = useState("");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [jobId, setJobId] = useState(positiveId(params.get("job_id")));
  const [page, setPage] = useState(0);

  const codes = useQuery({ queryKey: [...qualityQueryKey, "codes"], queryFn: qualityApi.codes, staleTime: Infinity });
  const groups = [...new Map((codes.data ?? []).map((c) => [c.group.key, c.group])).values()];
  const codesOfGroup = (codes.data ?? []).filter((c) => !group || c.group.key === group);

  // An inverted period is not sent: the list keeps the previous dates and the form says why.
  const badPeriod = !!seenFrom && !!seenTo && seenFrom > seenTo;
  const filters: IssueFilters = {
    status: status || undefined,
    severity: severity || undefined,
    code: code || undefined,
    group: group || undefined,
    job_id: jobId,
    seen_from: (!badPeriod && seenFrom) || undefined,
    seen_to: (!badPeriod && seenTo) || undefined,
    q: q || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const list = useQuery({
    queryKey: [...qualityQueryKey, "list", filters],
    queryFn: () => qualityApi.list(filters),
    placeholderData: keepPreviousData,
  });
  const summary = useQuery({
    queryKey: [...qualityQueryKey, "summary", { ...filters, status: undefined, severity: undefined, offset: 0 }],
    queryFn: () => qualityApi.summary(filters),
  });
  const items = list.data?.items ?? [];

  const pick = (card: CardKey) => {
    const same = status === card.status && severity === (card.severity ?? "");
    setStatus(same ? "" : card.status);
    setSeverity(same ? "" : (card.severity ?? ""));
    setPage(0);
  };
  const cards: { key: string; title: string; card: CardKey; counted: Counted | undefined }[] = [
    ...SEVERITIES.map((s) => ({
      key: s,
      title: `${SEVERITY_PLURALS[s]} abiertos`,
      card: { status: "open" as const, severity: s },
      counted: summary.data ? (summary.data.open[s] ?? { issues: 0, occurrences: 0 }) : undefined,
    })),
    { key: "resolved", title: STATUS_PLURALS.resolved, card: { status: "resolved" }, counted: summary.data?.resolved },
    { key: "ignored", title: STATUS_PLURALS.ignored, card: { status: "ignored" }, counted: summary.data?.ignored },
  ];

  return (
    <>
      <PageHeader
        title="Calidad"
        description="Problemas que las importaciones encontraron en los datos. Cada problema se cuenta una vez; sus ocurrencias son las veces que se repitió. La causa se corrige donde vive (Mapeos, la importación) y el problema se cierra solo."
      />

      <div aria-label="Resumen de problemas" className="space-y-2">
        <p className="text-sm text-muted-foreground tabular-nums">
          {summary.data
            ? `${n(summary.data.open_total.issues)} ${summary.data.open_total.issues === 1 ? "problema abierto" : "problemas abiertos"} · ${n(summary.data.open_total.occurrences)} ocurrencias`
            : " "}
        </p>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          {cards.map((c) => (
            <SummaryCard
              key={c.key}
              title={c.title}
              counted={c.counted}
              pressed={status === c.card.status && severity === (c.card.severity ?? "")}
              onClick={() => pick(c.card)}
            />
          ))}
        </div>
      </div>

      <Card>
        <CardHeader className="space-y-3">
          <CardTitle>Problemas</CardTitle>
          {jobId !== undefined && (
            <p className="flex flex-wrap items-center gap-2 text-sm">
              Solo los de la importación #{jobId}.
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setJobId(undefined);
                  setPage(0);
                }}
              >
                Ver todas las importaciones
              </Button>
            </p>
          )}
          <div className="grid gap-3 sm:grid-cols-4">
            <div className="space-y-1.5">
              <Label htmlFor={ids.status}>Estado</Label>
              <select
                id={ids.status}
                className={selectClass}
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value as IssueStatus | "");
                  setPage(0);
                }}
              >
                <option value="">Todos</option>
                {(["open", "resolved", "ignored"] as const).map((s) => (
                  <option key={s} value={s}>
                    {STATUS_PLURALS[s]}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={ids.severity}>Severidad</Label>
              <select
                id={ids.severity}
                className={selectClass}
                value={severity}
                onChange={(e) => {
                  setSeverity(e.target.value as Severity | "");
                  setPage(0);
                }}
              >
                <option value="">Todas</option>
                {SEVERITIES.map((s) => (
                  <option key={s} value={s}>
                    {SEVERITY_LABELS[s]}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={ids.group}>Afecta a</Label>
              <select
                id={ids.group}
                className={selectClass}
                value={group}
                onChange={(e) => {
                  setGroup(e.target.value);
                  if (codes.data?.find((c) => c.code === code)?.group.key !== e.target.value) setCode("");
                  setPage(0);
                }}
              >
                <option value="">Todo</option>
                {groups.map((g) => (
                  <option key={g.key} value={g.key}>
                    {g.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={ids.code}>Tipo de problema</Label>
              <select
                id={ids.code}
                className={selectClass}
                value={code}
                onChange={(e) => {
                  setCode(e.target.value);
                  setPage(0);
                }}
              >
                <option value="">Todos</option>
                {codesOfGroup.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={ids.from}>Visto desde</Label>
              <Input
                id={ids.from}
                type="date"
                value={seenFrom}
                onChange={(e) => {
                  setSeenFrom(e.target.value);
                  setPage(0);
                }}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={ids.to}>Visto hasta</Label>
              <Input
                id={ids.to}
                type="date"
                value={seenTo}
                onChange={(e) => {
                  setSeenTo(e.target.value);
                  setPage(0);
                }}
              />
            </div>
            <form
              className="space-y-1.5 sm:col-span-2"
              onSubmit={(e) => {
                e.preventDefault();
                setQ(search.trim());
                setPage(0);
              }}
            >
              <Label htmlFor={ids.q}>Buscar por tipo de problema</Label>
              <Input
                id={ids.q}
                value={search}
                maxLength={100}
                placeholder="zona, vendedor, artículo…"
                onChange={(e) => setSearch(e.target.value)}
              />
            </form>
          </div>
          {badPeriod && (
            <p role="alert" className="text-sm text-danger">
              La fecha desde es posterior a la fecha hasta.
            </p>
          )}
        </CardHeader>
        <CardContent>
          {list.isPending ? (
            <div className="space-y-2" aria-label="Cargando los problemas">
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
            </div>
          ) : list.isError ? (
            <ErrorState error={list.error} onRetry={() => void list.refetch()} />
          ) : items.length === 0 ? (
            <EmptyState
              icon={ShieldCheck}
              title={
                status === "open" && !severity ? "No hay problemas abiertos" : "No hay problemas con estos filtros"
              }
              description="Los problemas aparecen cuando una importación encuentra datos incompletos o que no se pueden vincular."
            />
          ) : (
            <>
              <Table stacked>
                <TableHeader>
                  <TableRow>
                    <TableHead>Problema</TableHead>
                    <TableHead>Severidad</TableHead>
                    <TableHead>Estado</TableHead>
                    <TableHead>Afecta a</TableHead>
                    <TableHead className="text-right">Ocurrencias</TableHead>
                    <TableHead>Primera vez</TableHead>
                    <TableHead>Última vez</TableHead>
                    <TableHead>Origen</TableHead>
                    <TableHead>Acción recomendada</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((issue) => (
                    <TableRow key={issue.id}>
                      <TableCell data-label="Problema">
                        <Link href={qualityHref(issue.id)} className="font-medium underline-offset-2 hover:underline">
                          {issue.label}
                        </Link>
                        <span className="block text-xs text-muted-foreground">{issue.group.label}</span>
                      </TableCell>
                      <TableCell data-label="Severidad">
                        <Badge tone={SEVERITY_TONES[issue.severity]}>{SEVERITY_LABELS[issue.severity]}</Badge>
                      </TableCell>
                      <TableCell data-label="Estado">
                        <Badge tone={STATUS_TONES[issue.status]}>{STATUS_LABELS[issue.status]}</Badge>
                      </TableCell>
                      <TableCell data-label="Afecta a">{issue.subject ?? "—"}</TableCell>
                      <TableCell data-label="Ocurrencias" className="text-right tabular-nums">
                        {n(issue.occurrences)}
                      </TableCell>
                      <TableCell data-label="Primera vez" className="tabular-nums">
                        {formatDateTime(issue.first_seen_at)}
                      </TableCell>
                      <TableCell data-label="Última vez" className="tabular-nums">
                        {formatDateTime(issue.last_seen_at)}
                      </TableCell>
                      <TableCell data-label="Origen">{originText(issue.source) ?? "—"}</TableCell>
                      <TableCell data-label="Acción recomendada" className="text-sm">
                        {issue.action}
                      </TableCell>
                    </TableRow>
                  ))}
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
    </>
  );
}
