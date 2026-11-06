"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { FileSpreadsheet } from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { importsApi, importsQueryKey, type ImportJobItem, type JobFilters } from "@/lib/api/imports";
import { formatDateTime } from "@/lib/format";

import { STATUS_LABELS, STATUS_TONES, STORE_SYS_TYPES, typeLabel } from "./labels";
import { importHref, UploadForm } from "./upload-form";

const PAGE_SIZE = 20;
const selectClass =
  "flex h-9 w-full rounded-md border border-border bg-surface px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

function result(job: ImportJobItem) {
  if (job.status === "failed") return "Nada cargado";
  if (job.status === "duplicate") return "Mismo archivo que una importación anterior";
  if (job.status === "cancelled") return "Nada cargado";
  if (job.status === "pending") return "Esperando confirmación";
  if (job.status === "processing") return "Cargando…";
  return job.rows_rejected ? `${job.rows_rejected} filas rechazadas` : "Todo cargado";
}

/**
 * `/comercial/datos/importaciones` (F1.3b): upload a STORE SYS export and the history of what was imported, newest
 * first. Operators see the upload; viewers only the history.
 */
export function ImportList() {
  const typeFilterId = useId();
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const canImport = roleAtLeast(moduleRole(me.data, "commercial"), "operator");
  const [importType, setImportType] = useState("");
  const [page, setPage] = useState(0);
  const filters: JobFilters = {
    module: "commercial",
    import_type: importType || undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };
  const list = useQuery({
    queryKey: [...importsQueryKey, "list", filters],
    queryFn: () => importsApi.list(filters),
    placeholderData: keepPreviousData,
  });
  const items = list.data?.items ?? [];

  return (
    <>
      <PageHeader
        title="Importaciones"
        description="Carga de los reportes de STORE SYS: clientes, artículos, comprobantes y ventas."
      />

      {canImport && <UploadForm />}

      <Card>
        <CardHeader className="flex-row flex-wrap items-end justify-between gap-3">
          <CardTitle>Historial</CardTitle>
          <div className="w-full space-y-1.5 sm:w-56">
            <Label htmlFor={typeFilterId}>Reporte</Label>
            <select
              id={typeFilterId}
              className={selectClass}
              value={importType}
              onChange={(e) => {
                setImportType(e.target.value);
                setPage(0);
              }}
            >
              <option value="">Todos</option>
              {STORE_SYS_TYPES.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
          </div>
        </CardHeader>
        <CardContent>
          {list.isPending ? (
            <div className="space-y-2" aria-label="Cargando el historial">
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
            </div>
          ) : list.isError ? (
            <ErrorState error={list.error} onRetry={() => void list.refetch()} />
          ) : items.length === 0 ? (
            <EmptyState
              icon={FileSpreadsheet}
              title="Todavía no hay importaciones"
              description={
                canImport
                  ? "Subí el primer archivo de STORE SYS con el formulario de arriba."
                  : "Cuando alguien importe un archivo de STORE SYS, va a aparecer acá."
              }
            />
          ) : (
            <>
              <Table stacked>
                <TableHeader>
                  <TableRow>
                    <TableHead>Fecha</TableHead>
                    <TableHead>Archivo</TableHead>
                    <TableHead>Reporte</TableHead>
                    <TableHead>Usuario</TableHead>
                    <TableHead>Estado</TableHead>
                    <TableHead className="text-right">Filas con datos</TableHead>
                    <TableHead>Resultado</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((job) => (
                    <TableRow key={job.id}>
                      <TableCell data-label="Fecha" className="tabular-nums">
                        {formatDateTime(job.created_at)}
                      </TableCell>
                      <TableCell data-label="Archivo" className="max-w-56 truncate">
                        <Link href={importHref(job.id)} className="font-medium underline-offset-2 hover:underline">
                          {job.original_filename}
                        </Link>
                      </TableCell>
                      <TableCell data-label="Reporte">{typeLabel(job.import_type, job.import_type_name)}</TableCell>
                      <TableCell data-label="Usuario">{job.created_by_name}</TableCell>
                      <TableCell data-label="Estado">
                        <Badge tone={STATUS_TONES[job.status]}>{STATUS_LABELS[job.status]}</Badge>
                      </TableCell>
                      <TableCell data-label="Filas con datos" className="text-right tabular-nums">
                        {job.rows_processed.toLocaleString("es-AR")}
                      </TableCell>
                      <TableCell data-label="Resultado" className="text-muted-foreground">
                        {result(job)}
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
