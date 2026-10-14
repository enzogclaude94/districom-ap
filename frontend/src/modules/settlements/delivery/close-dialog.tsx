"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Plus, Trash2 } from "lucide-react";
import { useId, useRef, useState } from "react";

import { ErrorState } from "@/components/common/error-state";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/cn";
import { formatMoney } from "@/lib/format";
import { parseAmount } from "@/modules/settlements/cash/amount";

import {
  deliveryApi,
  settlementPath,
  deliveryQueryKey,
  type AllocationInput,
  type ClosingPreview,
  type Person,
  type Settlement,
} from "./api";
import { deliveryErrorView } from "./errors";
import { FormError, PendingAttempt, selectClass, textareaClass } from "./form";
import { differenceText } from "./labels";
import { abs, fromCents, toCents } from "./money";
import { useDeliveryAction } from "./use-delivery-action";

export const previewQueryKey = (id: number) => [...deliveryQueryKey, "preview", id] as const;

interface Row {
  key: number;
  target: "EMPLOYEE" | "COMPANY";
  employeeId: number | null;
  amount: string;
  reason: string;
}

function Line({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className={cn("flex justify-between gap-4 py-1", strong && "border-t border-border pt-2 font-semibold")}>
      <dt>{label}</dt>
      <dd className="tabular-nums">{formatMoney(value)}</dd>
    </div>
  );
}

function Amounts({ preview, outlet }: { preview: ClosingPreview; outlet: boolean }) {
  const s = preview.summary;
  if (outlet) {
    // A boca (R7-4): expected, what justifies it line by line, and the difference.
    return (
      <dl className="rounded-md border border-border px-3 py-2 text-sm" aria-label="Importes del cierre">
        <Line label="Esperado (Store Sys)" value={s.expected} strong />
        <p className="pt-2 text-xs font-medium text-muted-foreground">Justificado</p>
        <Line label="Entregas parciales" value={s.remitted ?? "0.00"} />
        <Line label="Efectivo final" value={s.cash_total} />
        <Line label="Transferencias verificadas" value={s.transfers_verified} />
        <Line label="Cheques" value={s.cheques} />
        <Line label="Gastos" value={s.expenses} />
        <Line label="Total justificado" value={s.justified} strong />
        <div className="flex justify-between gap-4 border-t border-border pt-2 font-semibold">
          <dt>Diferencia</dt>
          <dd data-testid="preview-difference">{differenceText(preview.difference)}</dd>
        </div>
      </dl>
    );
  }
  return (
    <dl className="rounded-md border border-border px-3 py-2 text-sm" aria-label="Importes del cierre">
      <Line label="Esperado" value={s.expected} />
      {outlet && <Line label="Entregas parciales" value={s.remitted ?? "0.00"} />}
      {s.cash_by_box.map((box) => (
        <Line key={box.account_id} label={`Efectivo en ${box.account_name}`} value={box.total} />
      ))}
      <Line label="Transferencias verificadas" value={s.transfers_verified} />
      <Line label="Cheques" value={s.cheques} />
      {!outlet && <Line label="Pendiente" value={s.pending} />}
      <Line label="Gastos" value={s.expenses} />
      <Line label="Justificado" value={s.justified} strong />
    </dl>
  );
}

/**
 * CERRAR RENDICIÓN, in the order Enzo set for R2-3: ask the backend for the preview, show its exact
 * amounts, keep its token, let the user allocate the difference (if any), require an explicit "Confirmar
 * cierre" and send the same token. If the settlement changed since the preview the backend refuses the
 * close as stale: nothing is retried automatically, a new preview is loaded and confirmed again.
 */
export function CloseDialog({
  settlement,
  isAdmin,
  onClose,
  onClosed,
}: {
  settlement: Settlement;
  isAdmin: boolean;
  onClose: () => void;
  onClosed: (settlement: Settlement) => void;
}) {
  const nextKey = useRef(1);
  const preview = useQuery({
    queryKey: previewQueryKey(settlement.id),
    queryFn: () => deliveryApi.preview(settlement),
    // The token on screen must be the one of the amounts on screen: never refreshed behind the user's back.
    refetchOnWindowFocus: false,
    staleTime: 0,
  });
  const employees = useQuery({ queryKey: [...deliveryQueryKey, "employees"], queryFn: deliveryApi.employees });
  const [rows, setRows] = useState<Row[]>(() => [
    // A delivery's driver is proposed; a boca's responsables are only suggestions, never preloaded (R7-2).
    { key: 0, target: "EMPLOYEE", employeeId: settlement.employee_id ?? null, amount: "", reason: "" },
  ]);
  const action = useDeliveryAction(`close:${settlement.id}`, (closed) => {
    onClosed(closed);
    onClose();
  });
  const error = action.mutation.error ? deliveryErrorView(action.mutation.error) : null;
  const pending = action.mutation.isPending;

  if (action.stored) {
    return (
      <Dialog title="Cerrar rendición" onClose={onClose}>
        <PendingAttempt action={action} onClose={onClose} />
      </Dialog>
    );
  }

  const data = preview.data;
  const outlet = settlement.type === "OUTLET";
  const zero = Boolean(data?.administrative_zero);
  const balanced = data?.difference_kind === "BALANCED";
  const toAllocate = data ? toCents(data.amount_to_allocate) : 0;
  const assigned = rows.reduce(
    (sum, row) => sum + (parseAmount(row.amount) ? toCents(parseAmount(row.amount)!) : 0),
    0,
  );
  const remaining = toAllocate - assigned;
  const problem = (() => {
    if (!data || balanced) return null;
    for (const row of rows) {
      if (row.target === "EMPLOYEE" && row.employeeId === null) return "Elegí el empleado de cada parte.";
      if (!parseAmount(row.amount)) return "Ingresá el importe de cada parte.";
      if (row.target === "COMPANY" && !row.reason.trim())
        return "Indicá el motivo por el que la empresa absorbe su parte.";
    }
    const targets = rows.map((row) => (row.target === "COMPANY" ? "company" : `e${row.employeeId}`));
    if (new Set(targets).size !== targets.length) return "Cada empleado y la empresa pueden aparecer una sola vez.";
    return null; // an inexact sum is shown in the totals and blocks the confirmation
  })();
  const blocked = !data || data.blockers.length > 0 || (!balanced && (problem !== null || remaining !== 0));

  const update = (key: number, change: Partial<Row>) =>
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...change } : row)));

  const confirm = () => {
    if (!data || blocked || preview.isFetching) return;
    const allocations: AllocationInput[] = balanced
      ? []
      : rows.map((row) => ({
          target: row.target,
          employee_id: row.target === "EMPLOYEE" ? row.employeeId : null,
          amount: parseAmount(row.amount)!,
          reason: row.target === "COMPANY" ? row.reason.trim() : null,
        }));
    action.submit(() => ({
      method: "POST",
      path: settlementPath(settlement, "/close"),
      body: { version: data.version, preview_token: data.preview_token, allocations, confirm: true },
      summary: zero ? "Cierre administrativo cero" : `Cierre de la rendición (${differenceText(data.difference)})`,
    }));
  };

  return (
    <Dialog
      title="Cerrar rendición"
      description={
        outlet
          ? "Revisá los importes. Al confirmar, se descuenta el esperado de Cobranzas de boca y el efectivo, las transferencias verificadas, los cheques y los gastos pasan a Cajas."
          : "Revisá los importes. Al confirmar, el efectivo y las transferencias verificadas pasan a Cajas."
      }
      onClose={onClose}
      className="sm:max-w-2xl"
    >
      {preview.isPending ? (
        <div className="space-y-2" aria-label="Cargando vista previa">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : preview.isError ? (
        <ErrorState error={preview.error} onRetry={() => void preview.refetch()} />
      ) : (
        <div className={cn("space-y-4", preview.isFetching && "opacity-60")} aria-busy={preview.isFetching}>
          {error?.stale && (
            <div
              role="alert"
              className="flex gap-2 rounded-md border border-warning/40 bg-warning-soft px-3 py-2 text-sm"
            >
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
              <p>{error.message}</p>
            </div>
          )}

          <Amounts preview={data!} outlet={outlet} />

          {data!.blockers.length > 0 && (
            <div role="alert" className="space-y-1 rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm">
              <p className="font-medium text-danger">No se puede cerrar todavía</p>
              <ul className="list-inside list-disc">
                {data!.blockers.map((blocker) => (
                  <li key={blocker.code}>{blocker.message}</li>
                ))}
              </ul>
            </div>
          )}

          {data!.close_in_effect && (
            <p className="rounded-md border border-border bg-muted px-3 py-2 text-sm">
              {data!.close_in_effect.operation_id === null
                ? "Al confirmar, este cierre reemplaza al cierre en cero vigente; no había nada que revertir en Cajas."
                : zero
                  ? "Al confirmar, el cierre anterior se revierte en Cajas y la rendición queda cerrada en cero, sin una operación nueva."
                  : "Al confirmar, el cierre anterior se revierte y este se publica en su lugar, juntos y con la misma fecha de negocio. Hasta entonces, el cierre anterior sigue vigente en Cajas."}
            </p>
          )}

          {zero ? (
            <p
              className="flex items-center gap-2 rounded-md border border-success/30 bg-success-soft px-3 py-2 text-sm font-medium text-success"
              data-testid="administrative-zero"
            >
              <CheckCircle2 className="size-4" aria-hidden />
              {outlet
                ? "Sin movimientos financieros. La rendición se cierra en $0."
                : "Rendición de $0 sin movimientos: se cierra sin operación en Cajas (cierre administrativo cero)."}
            </p>
          ) : balanced ? (
            <p
              className="flex items-center gap-2 rounded-md border border-success/30 bg-success-soft px-3 py-2 text-sm font-medium text-success"
              data-testid="balanced"
            >
              <CheckCircle2 className="size-4" aria-hidden />
              Rendición balanceada — Diferencia $0
            </p>
          ) : (
            <Allocations
              preview={data!}
              outlet={outlet}
              rows={rows}
              employees={employees.data ?? []}
              isAdmin={isAdmin}
              assigned={assigned}
              remaining={remaining}
              update={update}
              add={() =>
                setRows((current) => [
                  ...current,
                  { key: nextKey.current++, target: "EMPLOYEE", employeeId: null, amount: "", reason: "" },
                ])
              }
              remove={(key) => setRows((current) => current.filter((row) => row.key !== key))}
            />
          )}

          <FormError message={problem ?? (error && !error.stale ? error.message : null)} />

          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>
              Cancelar
            </Button>
            <Button disabled={blocked || pending || preview.isFetching} onClick={confirm}>
              {pending ? "Cerrando…" : zero && outlet ? "Cerrar rendición en $0" : "Confirmar cierre"}
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}

function Allocations({
  preview,
  outlet,
  rows,
  employees,
  isAdmin,
  assigned,
  remaining,
  update,
  add,
  remove,
}: {
  preview: ClosingPreview;
  outlet: boolean;
  rows: Row[];
  employees: Person[];
  isAdmin: boolean;
  assigned: number;
  remaining: number;
  update: (key: number, change: Partial<Row>) => void;
  add: () => void;
  remove: (key: number) => void;
}) {
  const id = useId();
  const shortage = preview.difference_kind === "SHORTAGE";
  return (
    <section aria-labelledby={`${id}-title`} className="space-y-3">
      <div className="space-y-1">
        <h3 id={`${id}-title`} className="font-medium">
          {shortage ? "Faltante" : "Sobrante"} de {formatMoney(abs(preview.difference))}: ¿quién lo absorbe?
        </h3>
        <p className="text-sm text-muted-foreground">
          {shortage
            ? "Repartilo entre uno o varios empleados: queda como deuda en su cuenta interna."
            : "Repartilo entre uno o varios empleados: queda a su favor en su cuenta interna."}
          {isAdmin && " Como administrador, también podés asignar una parte a la empresa, con motivo."}
        </p>
        {outlet && (
          <p className="text-sm text-muted-foreground" data-testid="close-responsible-suggestions">
            {preview.responsible_suggestions?.length
              ? `Responsables de la Boca en esta fecha (sugerencia): ${preview.responsible_suggestions.map((p) => p.full_name).join(", ")}. Nada se asigna solo: podés elegir a cualquier empleado.`
              : "La Boca no tenía responsables asignados en esta fecha. Podés elegir a cualquier empleado."}
          </p>
        )}
      </div>
      <ul className="space-y-3">
        {rows.map((row, index) => (
          <li key={row.key} className="space-y-2 rounded-md border border-border p-3">
            <div className="grid gap-2 sm:grid-cols-[1fr_10rem_auto] sm:items-end">
              <div className="space-y-1.5">
                <Label htmlFor={`${id}-who-${row.key}`}>Parte {index + 1}: quién</Label>
                <select
                  id={`${id}-who-${row.key}`}
                  className={selectClass}
                  value={row.target === "COMPANY" ? "company" : (row.employeeId ?? "")}
                  onChange={(event) =>
                    event.target.value === "company"
                      ? update(row.key, { target: "COMPANY", employeeId: null })
                      : update(row.key, {
                          target: "EMPLOYEE",
                          employeeId: event.target.value ? Number(event.target.value) : null,
                        })
                  }
                >
                  <option value="">Elegí un empleado…</option>
                  {employees.map((employee) => (
                    <option key={employee.id} value={employee.id}>
                      {employee.full_name}
                    </option>
                  ))}
                  {isAdmin && <option value="company">La empresa</option>}
                </select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={`${id}-amount-${row.key}`}>Importe de la parte {index + 1}</Label>
                <Input
                  id={`${id}-amount-${row.key}`}
                  inputMode="decimal"
                  autoComplete="off"
                  placeholder="0,00"
                  className="tabular-nums"
                  value={row.amount}
                  onChange={(event) => update(row.key, { amount: event.target.value })}
                />
              </div>
              <div className="flex gap-1">
                {remaining > 0 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      const current = parseAmount(row.amount) ? toCents(parseAmount(row.amount)!) : 0;
                      update(row.key, { amount: fromCents(current + remaining).replace(".", ",") });
                    }}
                  >
                    Completar
                  </Button>
                )}
                {rows.length > 1 && (
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Quitar la parte ${index + 1}`}
                    onClick={() => remove(row.key)}
                  >
                    <Trash2 aria-hidden />
                  </Button>
                )}
              </div>
            </div>
            {row.target === "COMPANY" && (
              <div className="space-y-1.5">
                <Label htmlFor={`${id}-reason-${row.key}`}>Motivo por el que la empresa la absorbe</Label>
                <textarea
                  id={`${id}-reason-${row.key}`}
                  className={textareaClass}
                  maxLength={500}
                  value={row.reason}
                  onChange={(event) => update(row.key, { reason: event.target.value })}
                />
              </div>
            )}
          </li>
        ))}
      </ul>
      <Button variant="secondary" size="sm" onClick={add}>
        <Plus aria-hidden />
        Agregar otra parte
      </Button>
      <dl className="grid grid-cols-3 gap-2 rounded-md border border-border px-3 py-2 text-sm" aria-label="Asignación">
        <div>
          <dt className="text-muted-foreground">Diferencia total</dt>
          <dd className="font-medium tabular-nums">{formatMoney(abs(preview.difference))}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Asignado</dt>
          <dd className="font-medium tabular-nums" data-testid="assigned">
            {formatMoney(fromCents(assigned))}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">{remaining < 0 ? "Asignado de más" : "Falta asignar"}</dt>
          <dd className={cn("font-medium tabular-nums", remaining !== 0 && "text-danger")} data-testid="remaining">
            {formatMoney(fromCents(remaining < 0 ? -remaining : remaining))}
          </dd>
        </div>
      </dl>
    </section>
  );
}
