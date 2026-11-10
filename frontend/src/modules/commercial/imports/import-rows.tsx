"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import { ErrorState } from "@/components/common/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { importsApi, importsQueryKey, type ImportRowStatus, type RowFilters } from "@/lib/api/imports";

import { ROW_STATUS_LABELS, SEVERITY_TONES } from "./labels";

const PAGE_SIZE = 50;
const FILTERS: { value: ImportRowStatus | ""; label: string }[] = [
  { value: "", label: "Todas las filas" },
  { value: "rejected", label: "Rechazadas" },
  { value: "warning", label: "Con avisos" },
  { value: "pending_resolution", label: "Pendientes de vincular" },
  { value: "duplicate", label: "Repetidas" },
];
const selectClass =
  "flex h-9 w-full rounded-md border border-border bg-surface px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";
const TONE: Partial<Record<ImportRowStatus, "danger" | "warning" | "neutral">> = {
  rejected: "danger",
  warning: "warning",
  pending_resolution: "warning",
  duplicate: "neutral",
};

/**
 * The file's rows behind the summary: row number, state, its key (a code, never a name or a CUIT) and what the
 * review said about it. The row's cells are never shown here.
 */
export function ImportRows({ jobId, code }: { jobId: number; code?: string }) {
  const statusId = useId();
  const [status, setStatus] = useState<ImportRowStatus | "">("");
  const [page, setPage] = useState(0);
  const [lastCode, setLastCode] = useState(code);
  if (lastCode !== code) {
    setLastCode(code);
    setPage(0);
  }
  const filters: RowFilters = { status: status || undefined, code, limit: PAGE_SIZE, offset: page * PAGE_SIZE };
  const rows = useQuery({
    queryKey: [...importsQueryKey, "job", jobId, "rows", filters],
    queryFn: () => importsApi.rows(jobId, filters),
    placeholderData: keepPreviousData,
  });
  const items = rows.data?.items ?? [];

  return (
    <div className="space-y-3">
      <div className="w-full space-y-1.5 sm:w-72">
        <Label htmlFor={statusId}>Filas</Label>
        <select
          id={statusId}
          className={selectClass}
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as ImportRowStatus | "");
            setPage(0);
          }}
        >
          {FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
      </div>
      {rows.isPending ? (
        <Skeleton className="h-24" />
      ) : rows.isError ? (
        <ErrorState error={rows.error} onRetry={() => void rows.refetch()} />
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No hay filas para mostrar con este filtro.</p>
      ) : (
        <>
          <Table stacked>
            <TableHeader>
              <TableRow>
                <TableHead className="w-20">Fila</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead>Código</TableHead>
                <TableHead>Observaciones</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((row) => (
                <TableRow key={row.id}>
                  <TableCell data-label="Fila" className="tabular-nums">
                    {row.row_number}
                  </TableCell>
                  <TableCell data-label="Estado">
                    <Badge tone={TONE[row.status] ?? "neutral"}>{ROW_STATUS_LABELS[row.status]}</Badge>
                  </TableCell>
                  <TableCell data-label="Código" className="tabular-nums">
                    {row.business_key ?? "—"}
                  </TableCell>
                  <TableCell data-label="Observaciones">
                    <ul className="space-y-0.5">
                      {row.messages.map((m, index) => (
                        <li key={`${m.code}-${index}`} className="flex items-center gap-1.5 text-sm">
                          <span
                            className={
                              "size-1.5 shrink-0 rounded-full " +
                              (SEVERITY_TONES[m.severity] === "danger"
                                ? "bg-danger"
                                : SEVERITY_TONES[m.severity] === "warning"
                                  ? "bg-warning"
                                  : "bg-muted-foreground")
                            }
                            aria-hidden
                          />
                          {m.text}
                        </li>
                      ))}
                    </ul>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {rows.data.total > PAGE_SIZE && (
            <div className="flex items-center justify-between gap-2 text-sm text-muted-foreground">
              <span className="tabular-nums">
                Página {page + 1} de {Math.ceil(rows.data.total / PAGE_SIZE)}
              </span>
              <div className="flex gap-2">
                <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
                  Anterior
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={(page + 1) * PAGE_SIZE >= rows.data.total}
                  onClick={() => setPage(page + 1)}
                >
                  Siguiente
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
