"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, UserPlus, Users } from "lucide-react";
import { useId, useState, type FormEvent } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState } from "@/components/common/error-state";
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
import { outletsApi, outletsQueryKey } from "@/lib/api/outlets";
import { formatDate, formatDateTime, todayIso } from "@/lib/format";
import { FormError, SelectField, TextField } from "@/modules/settlements/delivery/form";
import { Pager } from "@/modules/settlements/fuel/fuel-loads-view";

import { responsibilitiesApi, responsibilitiesQueryKey, type Responsibility } from "./api";

const PAGE_SIZE = 20;

function errorText(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError && error.status > 0 && error.status < 500) return error.message;
  return "No se pudo guardar. Revisá la conexión y volvé a intentar.";
}

function DateField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type="date" value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}

function AddDialog({ outletId, onClose, onSaved }: { outletId: number; onClose: () => void; onSaved: () => void }) {
  const employees = useQuery({
    queryKey: [...responsibilitiesQueryKey, "employees"],
    queryFn: responsibilitiesApi.employees,
  });
  const [employeeId, setEmployeeId] = useState<number | null>(null);
  const [validFrom, setValidFrom] = useState(todayIso());
  const [problem, setProblem] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (body: { employee_id: number; valid_from: string }) => responsibilitiesApi.create(outletId, body),
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (employeeId === null) return setProblem("Elegí el empleado responsable.");
    if (!validFrom) return setProblem("Indicá desde qué día es responsable.");
    setProblem(null);
    save.mutate({ employee_id: employeeId, valid_from: validFrom }, { onSuccess: onSaved });
  };
  return (
    <Dialog
      title="Sumar responsable"
      description="Puede haber varios responsables a la vez. Vale desde el primer día indicado, incluido, hasta que se termine."
      onClose={onClose}
    >
      <form className="space-y-4" onSubmit={submit} noValidate>
        {employees.isError ? (
          <ErrorState error={employees.error} onRetry={() => void employees.refetch()} />
        ) : (
          <SelectField
            label="Empleado"
            value={employeeId}
            onChange={setEmployeeId}
            placeholder={employees.isPending ? "Cargando empleados…" : "Elegí un empleado activo"}
            options={(employees.data ?? []).map((e) => ({ id: e.id, name: e.full_name }))}
          />
        )}
        <DateField label="Desde" value={validFrom} onChange={setValidFrom} />
        <FormError message={problem ?? errorText(save.error)} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" disabled={save.isPending}>
            {save.isPending ? "Guardando…" : "Sumar responsable"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function EndDialog({ row, onClose, onSaved }: { row: Responsibility; onClose: () => void; onSaved: () => void }) {
  const [validTo, setValidTo] = useState(todayIso());
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (body: { valid_to: string; reason: string }) => responsibilitiesApi.end(row.id, body),
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!validTo) return setProblem("Indicá el último día de la responsabilidad.");
    if (validTo < row.valid_from)
      return setProblem(`El último día no puede ser anterior al inicio (${formatDate(row.valid_from)}).`);
    if (!reason.trim()) return setProblem("Indicá el motivo del fin de la responsabilidad.");
    setProblem(null);
    save.mutate({ valid_to: validTo, reason: reason.trim() }, { onSuccess: onSaved });
  };
  return (
    <Dialog
      title={`Terminar la responsabilidad de ${row.employee_name}`}
      description={`Desde el ${formatDate(row.valid_from)}. Se termina una sola vez y no se puede reescribir.`}
      onClose={onClose}
    >
      <form className="space-y-4" onSubmit={submit} noValidate>
        <DateField label="Último día (incluido)" value={validTo} onChange={setValidTo} />
        <TextField label="Motivo" value={reason} onChange={setReason} multiline />
        <FormError message={problem ?? errorText(save.error)} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" variant="danger" disabled={save.isPending}>
            {save.isPending ? "Guardando…" : "Terminar"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function Period({ row }: { row: Responsibility }) {
  return (
    <span className="tabular-nums">
      {formatDate(row.valid_from)} → {row.valid_to ? formatDate(row.valid_to) : "sin fecha de fin"}
    </span>
  );
}

function Employee({ row }: { row: Responsibility }) {
  return (
    <span>
      {row.employee_name}
      {!row.employee_active && <span className="text-muted-foreground"> (empleado inactivo)</span>}
    </span>
  );
}

function Ending({ row }: { row: Responsibility }) {
  if (!row.ended_at) return <Badge tone="success">Abierta</Badge>;
  return (
    <span className="text-sm text-muted-foreground">
      Terminada: {row.end_reason} · {row.ended_by_name ?? "—"} · {formatDateTime(row.ended_at)}
    </span>
  );
}

function Rows({
  rows,
  canEdit,
  onEnd,
  label,
}: {
  rows: Responsibility[];
  canEdit: boolean;
  onEnd: (row: Responsibility) => void;
  label: string;
}) {
  const endButton = (row: Responsibility) =>
    canEdit && !row.valid_to ? (
      <Button variant="ghost" size="sm" aria-label={`Terminar ${row.employee_name}`} onClick={() => onEnd(row)}>
        Terminar
      </Button>
    ) : null;
  return (
    <>
      <div className="hidden md:block">
        <Table aria-label={label}>
          <TableHeader>
            <TableRow>
              <TableHead>Responsable</TableHead>
              <TableHead>Período</TableHead>
              <TableHead>Estado</TableHead>
              <TableHead>
                <span className="sr-only">Acciones</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.id}>
                <TableCell className="font-medium">
                  <Employee row={row} />
                </TableCell>
                <TableCell>
                  <Period row={row} />
                </TableCell>
                <TableCell>
                  <Ending row={row} />
                </TableCell>
                <TableCell className="text-right">{endButton(row)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <ul className="space-y-2 md:hidden" aria-label={label}>
        {rows.map((row) => (
          <li key={row.id} className="space-y-1 rounded-md border border-border p-3 text-sm">
            <p className="font-medium">
              <Employee row={row} />
            </p>
            <p>
              <Period row={row} />
            </p>
            <Ending row={row} />
            {endButton(row) && <div className="-mx-2">{endButton(row)}</div>}
          </li>
        ))}
      </ul>
    </>
  );
}

/**
 * "Responsables" of a boca's page (R7-1), for users with access to Rendiciones: who answers for it on a day
 * and its whole history. Admins of Rendiciones add one (any active employee, several at once) or end one
 * (once, with a reason). Everyone else sees just the boca.
 */
export function OutletResponsibilities({ outletId }: { outletId: number }) {
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const role = moduleRole(me.data, "settlements");
  const canEdit = roleAtLeast(role, "admin");
  const outlet = useQuery({ queryKey: [...outletsQueryKey, outletId], queryFn: () => outletsApi.get(outletId) });
  const [day, setDay] = useState(todayIso());
  const [page, setPage] = useState(0);
  const [adding, setAdding] = useState(false);
  const [ending, setEnding] = useState<Responsibility | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const onDay = useQuery({
    queryKey: [...responsibilitiesQueryKey, outletId, "on", day],
    queryFn: () => responsibilitiesApi.ofOutlet(outletId, { on: day, limit: 200 }),
    enabled: role !== null && Boolean(day),
  });
  const history = useQuery({
    queryKey: [...responsibilitiesQueryKey, outletId, "history", page],
    queryFn: () => responsibilitiesApi.ofOutlet(outletId, { limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    enabled: role !== null,
    placeholderData: keepPreviousData,
  });
  if (role === null) return null;

  const saved = (text: string) => {
    setAdding(false);
    setEnding(null);
    setNotice(text);
    void queryClient.invalidateQueries({ queryKey: responsibilitiesQueryKey });
  };
  const outletActive = outlet.data?.active ?? true;

  return (
    <Card data-testid="outlet-responsibilities">
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>Responsables</CardTitle>
        {canEdit && outletActive && (
          <Button size="sm" onClick={() => setAdding(true)}>
            <UserPlus aria-hidden />
            Sumar responsable
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="text-sm text-muted-foreground">
          Una responsabilidad vale desde su primer día hasta su último día, los dos incluidos. Puede haber varios
          responsables a la vez y un empleado puede serlo de varias bocas.
        </p>
        {!outletActive && (
          <p className="text-sm text-muted-foreground">
            La boca está desactivada: no suma responsables nuevos. Los abiertos se pueden terminar.
          </p>
        )}
        {notice && (
          <p role="status" className="flex items-center gap-2 text-sm text-primary">
            <CheckCircle2 className="size-4" aria-hidden />
            {notice}
          </p>
        )}

        <section className="space-y-3" aria-label="Responsables del día">
          <div className="max-w-xs">
            <DateField label="Responsables del día" value={day} onChange={setDay} />
          </div>
          {onDay.isPending ? (
            <Skeleton className="h-16 w-full" aria-label="Cargando responsables" />
          ) : onDay.isError ? (
            <ErrorState error={onDay.error} onRetry={() => void onDay.refetch()} />
          ) : onDay.data.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nadie es responsable de esta boca el {formatDate(day)}.</p>
          ) : (
            <Rows rows={onDay.data.items} canEdit={canEdit} onEnd={setEnding} label="Responsables del día" />
          )}
        </section>

        <section className="space-y-3" aria-label="Historia de responsables">
          <h3 className="text-sm font-medium">Historia</h3>
          {history.isPending ? (
            <Skeleton className="h-16 w-full" aria-label="Cargando historia de responsables" />
          ) : history.isError ? (
            <ErrorState error={history.error} onRetry={() => void history.refetch()} />
          ) : history.data.total === 0 ? (
            <EmptyState
              icon={Users}
              title="Todavía no hay responsables"
              description={
                canEdit
                  ? "Sumá a los empleados que responden por esta boca."
                  : "Un administrador de Rendiciones los suma."
              }
            />
          ) : (
            <>
              <Rows rows={history.data.items} canEdit={canEdit} onEnd={setEnding} label="Historia de responsables" />
              {history.data.total > PAGE_SIZE && (
                <Pager
                  page={page}
                  size={PAGE_SIZE}
                  shown={history.data.items.length}
                  total={history.data.total}
                  onPage={setPage}
                />
              )}
            </>
          )}
        </section>
      </CardContent>
      {adding && (
        <AddDialog outletId={outletId} onClose={() => setAdding(false)} onSaved={() => saved("Responsable sumado.")} />
      )}
      {ending && (
        <EndDialog
          row={ending}
          onClose={() => setEnding(null)}
          onSaved={() => saved(`La responsabilidad de ${ending.employee_name} quedó terminada.`)}
        />
      )}
    </Card>
  );
}
