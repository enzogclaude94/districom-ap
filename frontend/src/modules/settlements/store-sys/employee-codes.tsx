"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Users } from "lucide-react";
import { useId, useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { areaRole, authApi, currentUserQueryKey, roleAtLeast } from "@/lib/api/auth";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import { deliveryErrorView } from "@/modules/settlements/delivery/errors";
import { ActionDialog, TextField } from "@/modules/settlements/delivery/form";
import { useWriteAction } from "@/modules/settlements/delivery/use-delivery-action";

import { STORE_SYS_AREA, storeSysApi, storeSysQueryKey, type EmployeeCode } from "./api";

const PAGE_SIZE = 25;
export const CODES_NOTICE =
  "El código vincula a cada empleado con lo que informa Store Sys. Cómo lo identifica Store Sys todavía no está confirmado.";

type Open = { kind: "edit" | "history"; row: EmployeeCode } | null;

/** `/rendiciones/integracion-store-sys`: each employee's Store Sys code, its changes and who made them (R9-1). */
export function EmployeeCodes() {
  const searchId = useId();
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const canEdit = roleAtLeast(areaRole(me.data, STORE_SYS_AREA), "admin");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState<Open>(null);
  const params = { q: q.trim() || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE };
  const list = useQuery({
    queryKey: [...storeSysQueryKey, "codes", params],
    queryFn: () => storeSysApi.codes(params),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHeader
        title="Integración Store Sys"
        description="Códigos con los que Store Sys informa el importe esperado de cada repartidor. Área reservada."
      />
      <p className="text-sm text-muted-foreground">{CODES_NOTICE}</p>

      <Card>
        <CardContent className="pt-5">
          <div className="max-w-sm space-y-1.5">
            <Label htmlFor={searchId}>Empleado</Label>
            <Input
              id={searchId}
              type="search"
              placeholder="Buscar por nombre…"
              value={q}
              onChange={(event) => {
                setQ(event.target.value);
                setPage(0);
              }}
            />
          </div>
        </CardContent>
      </Card>

      {list.isPending ? (
        <div className="space-y-2" aria-label="Cargando códigos">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.data.total === 0 ? (
        <EmptyState
          icon={Users}
          title={q.trim() ? "No hay empleados con ese nombre" : "Todavía no hay empleados"}
          description={
            q.trim() ? "Probá con otra parte del nombre." : "Los empleados se cargan en Empleados de la plataforma."
          }
        />
      ) : (
        <Card>
          <CardContent className="pt-3">
            <Table stacked>
              <TableHeader>
                <TableRow>
                  <TableHead>Empleado</TableHead>
                  <TableHead>Código Store Sys</TableHead>
                  <TableHead className="text-right">Acciones</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.data.items.map((row) => (
                  <TableRow key={row.employee_id} data-testid={`store-code-${row.employee_id}`}>
                    <TableCell data-label="Empleado">
                      <span className="font-medium">{row.full_name}</span>
                      {!row.active && <span className="ml-2 text-xs text-muted-foreground">(inactivo)</span>}
                    </TableCell>
                    <TableCell data-label="Código">
                      {row.store_code ?? <span className="text-muted-foreground">Sin código</span>}
                    </TableCell>
                    <TableCell data-label="Acciones" className="text-right">
                      <div className="flex flex-wrap justify-end gap-2">
                        <Button variant="ghost" size="sm" onClick={() => setOpen({ kind: "history", row })}>
                          Historia
                        </Button>
                        {canEdit && (
                          <Button variant="secondary" size="sm" onClick={() => setOpen({ kind: "edit", row })}>
                            {row.store_code ? "Cambiar código" : "Cargar código"}
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <div
              className={cn(
                "mt-3 flex items-center justify-between gap-2 text-sm text-muted-foreground",
                list.isPlaceholderData && "opacity-60",
              )}
            >
              <span className="tabular-nums">
                {page * PAGE_SIZE + 1}–{page * PAGE_SIZE + list.data.items.length} de {list.data.total}
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
          </CardContent>
        </Card>
      )}

      {open?.kind === "edit" && <CodeDialog row={open.row} onClose={() => setOpen(null)} />}
      {open?.kind === "history" && <CodeHistory row={open.row} onClose={() => setOpen(null)} />}
    </>
  );
}

/** Load, correct or remove a code; a code already loaded changes only with a reason. Nothing is deleted silently. */
function CodeDialog({ row, onClose }: { row: EmployeeCode; onClose: () => void }) {
  const [code, setCode] = useState(row.store_code ?? "");
  const [reason, setReason] = useState("");
  const action = useWriteAction<EmployeeCode>({
    slot: `store-code:${row.employee_id}`,
    write: (request) =>
      storeSysApi.setCode(
        row.employee_id,
        (request.body.store_code as string | null) ?? null,
        (request.body.reason as string | null) ?? null,
      ),
    invalidate: [storeSysQueryKey],
    isUncertain: (error) => Boolean(deliveryErrorView(error).uncertain),
    onDone: onClose,
  });
  const loaded = row.store_code !== null;
  return (
    <ActionDialog
      title={loaded ? "Cambiar código Store Sys" : "Cargar código Store Sys"}
      description={`${row.full_name}. ${loaded ? "Dejalo vacío para quitarlo. Queda registrado el cambio y su motivo." : "Queda registrado quién lo cargó y cuándo."}`}
      submitLabel="Guardar"
      action={action}
      onClose={onClose}
      validate={() => {
        if (!loaded && !code.trim()) return "Ingresá el código.";
        if (loaded && code.trim() === row.store_code) return "El código es el mismo.";
        if (loaded && !reason.trim()) return "Indicá el motivo del cambio.";
        return null;
      }}
      build={() => ({
        method: "PUT",
        path: `/store-sys/employee-codes/${row.employee_id}`,
        body: { store_code: code.trim() || null, reason: reason.trim() || null },
        summary: `Código Store Sys de ${row.full_name}`,
      })}
    >
      <TextField label="Código" value={code} onChange={setCode} maxLength={100} />
      {loaded && <TextField label="Motivo" value={reason} onChange={setReason} multiline />}
    </ActionDialog>
  );
}

function CodeHistory({ row, onClose }: { row: EmployeeCode; onClose: () => void }) {
  const history = useQuery({
    queryKey: [...storeSysQueryKey, "history", row.employee_id],
    queryFn: () => storeSysApi.history(row.employee_id),
  });
  return (
    <Dialog title={`Historia del código · ${row.full_name}`} onClose={onClose}>
      {history.isPending ? (
        <Skeleton className="h-20 w-full" />
      ) : history.isError ? (
        <ErrorState error={history.error} onRetry={() => void history.refetch()} />
      ) : history.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nunca se cargó un código.</p>
      ) : (
        <ol className="space-y-2 text-sm" data-testid="store-code-history">
          {history.data.map((change, i) => (
            <li key={i} className="rounded-md border border-border px-3 py-2">
              <p className="font-medium">
                {change.before ?? "sin código"} → {change.after ?? "sin código"}
              </p>
              <p className="text-xs text-muted-foreground">
                {formatDateTime(change.changed_at)}
                {change.changed_by_name && ` · ${change.changed_by_name}`}
                {change.reason && ` · Motivo: ${change.reason}`}
              </p>
            </li>
          ))}
        </ol>
      )}
    </Dialog>
  );
}
