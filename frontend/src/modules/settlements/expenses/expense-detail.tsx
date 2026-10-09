"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { ErrorState } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { authApi, currentUserQueryKey, moduleRole, roleAtLeast } from "@/lib/api/auth";
import { formatDate, formatDateTime, formatMoney } from "@/lib/format";
import type { Operation } from "@/modules/settlements/cash/api";
import { ActionDialog, TextField } from "@/modules/settlements/delivery/form";
import { formatQuantity, parseOdometer } from "@/modules/settlements/delivery/fuel";

import { expenseApi, expenseDetailKey, expensePath, type ExpenseDetail } from "./api";
import { useExpenseAction } from "./direct-expense-dialog";
import { expenseErrorView } from "./errors";
import { eventLabel, ORIGIN_LABELS, STATE_LABELS, STATE_TONES } from "./labels";

const operationHref = (id: number) => `/rendiciones/cajas/operaciones/${id}`;

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

function Legs({ operation, title }: { operation: Operation; title: string }) {
  return (
    <div className="space-y-1.5 text-sm">
      <p className="flex flex-wrap items-center gap-2 font-medium">
        {title}
        <Link href={operationHref(operation.id)} className="font-normal text-primary hover:underline">
          Operación n.º {operation.id}
        </Link>
        <span className="font-normal text-muted-foreground">
          {formatDate(operation.business_date)} · registrada {formatDateTime(operation.created_at)}
        </span>
      </p>
      <ul className="space-y-0.5">
        {operation.entries.map((entry) => (
          <li key={entry.account_id} className="flex justify-between gap-4 tabular-nums">
            <span>{entry.account_name}</span>
            <span>{formatMoney(entry.amount, { signed: true })}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ReverseDialog({ expense, onClose }: { expense: ExpenseDetail; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const action = useExpenseAction(`expense-reverse:${expense.id}`, onClose);
  return (
    <ActionDialog
      title="Revertir gasto"
      description={
        <>
          Se registra el movimiento inverso con la misma fecha del gasto ({formatDate(expense.business_date)}):{" "}
          {formatMoney(expense.amount)} vuelven a {expense.paid_from_account_name}. El gasto no se borra: queda
          revertido, con el motivo. Para corregir un dato financiero, revertilo y registrá uno nuevo.
        </>
      }
      submitLabel="Revertir gasto"
      action={action}
      errorView={(e) => expenseErrorView(e)}
      where="el gasto"
      onClose={onClose}
      validate={() => (!reason.trim() ? "Indicá el motivo." : null)}
      build={(requestId) => ({
        method: "POST",
        path: expensePath(expense.id, "/reverse"),
        body: { client_request_id: requestId, reason: reason.trim() },
        summary: `Reverso del gasto n.º ${expense.id} por ${formatMoney(expense.amount)}`,
      })}
    >
      <TextField label="Motivo" value={reason} onChange={setReason} multiline />
    </ActionDialog>
  );
}

function CorrectDialog({ expense, onClose }: { expense: ExpenseDetail; onClose: () => void }) {
  const [description, setDescription] = useState(expense.description ?? "");
  const [odometer, setOdometer] = useState(expense.fuel?.odometer_km != null ? String(expense.fuel.odometer_km) : "");
  const [reason, setReason] = useState("");
  const action = useExpenseAction(`expense-correct:${expense.id}`, onClose);
  const changes = (): Record<string, unknown> => {
    const change: Record<string, unknown> = {};
    const text = description.trim() || null;
    if (text !== expense.description) change.description = text;
    const km = parseOdometer(odometer);
    if (expense.fuel && km !== undefined && km !== expense.fuel.odometer_km) change.odometer_km = km;
    return change;
  };
  return (
    <ActionDialog
      title="Corregir datos descriptivos"
      description="Solo la descripción y, en combustible, el odómetro. El importe, la categoría, la cuenta, la fecha y la carga no se corrigen: se revierte el gasto y se registra uno nuevo."
      submitLabel="Guardar corrección"
      action={action}
      errorView={(e) => expenseErrorView(e)}
      where="el gasto"
      onClose={onClose}
      validate={() =>
        (expense.fuel && parseOdometer(odometer) === undefined ? "El odómetro va en kilómetros enteros." : null) ??
        (Object.keys(changes()).length === 0 ? "No cambiaste nada." : null) ??
        (!reason.trim() ? "Indicá el motivo." : null)
      }
      build={() => ({
        method: "POST",
        path: expensePath(expense.id, "/descriptive"),
        body: { reason: reason.trim(), ...changes() },
        summary: `Corrección del gasto n.º ${expense.id}`,
      })}
    >
      <TextField label="Descripción" value={description} onChange={setDescription} multiline />
      {expense.fuel && (
        <TextField label="Odómetro en km (opcional)" value={odometer} onChange={setOdometer} maxLength={12} />
      )}
      <TextField label="Motivo de la corrección" value={reason} onChange={setReason} multiline />
    </ActionDialog>
  );
}

function change(value: unknown) {
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

/** `/rendiciones/cajas/gastos/[id]`: a direct expense, its movement in the ledger, its reversal and history. */
export function ExpenseDetailView({ expenseId }: { expenseId: number }) {
  const me = useQuery({ queryKey: currentUserQueryKey, queryFn: authApi.me });
  const isAdmin = roleAtLeast(moduleRole(me.data, "settlements"), "admin");
  const query = useQuery({ queryKey: expenseDetailKey(expenseId), queryFn: () => expenseApi.get(expenseId) });
  const [open, setOpen] = useState<"reverse" | "correct" | null>(null);

  const back = (
    <Link
      href="/rendiciones/cajas/gastos"
      className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
    >
      <ArrowLeft className="size-4" aria-hidden />
      Gastos directos
    </Link>
  );
  if (query.isPending) {
    return (
      <>
        {back}
        <Skeleton className="h-40 w-full" aria-label="Cargando gasto" />
      </>
    );
  }
  if (query.isError) {
    return (
      <>
        {back}
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      </>
    );
  }
  const expense = query.data;
  const direct = expense.origin === "DIRECT";
  const editable = isAdmin && direct && expense.state === "IN_EFFECT";

  return (
    <>
      {back}
      <PageHeader
        title={`${direct ? "Gasto directo" : "Gasto de rendición"} n.º ${expense.id}`}
        description={`${expense.category_name} · ${formatMoney(expense.amount)} · ${formatDate(expense.business_date)}`}
        actions={
          editable && (
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={() => setOpen("correct")}>
                Corregir descripción
              </Button>
              <Button variant="secondary" onClick={() => setOpen("reverse")}>
                Revertir gasto
              </Button>
            </div>
          )
        }
      />

      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2">
            {expense.category_name}
            <Badge tone={STATE_TONES[expense.state]} data-testid="expense-state">
              {STATE_LABELS[expense.state]}
            </Badge>
          </CardTitle>
          <CardDescription>{ORIGIN_LABELS[expense.origin]}</CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Importe">
              <span className="tabular-nums" data-testid="expense-amount">
                {formatMoney(expense.amount)}
              </span>
            </Field>
            <Field label="Fecha del gasto">{formatDate(expense.business_date)}</Field>
            <Field label="Cuenta pagadora">
              {direct ? (
                expense.paid_from_account_name
              ) : (
                <Link href={`/rendiciones/repartos/${expense.settlement_id}`} className="text-primary hover:underline">
                  Rendición
                </Link>
              )}
            </Field>
            <Field label="Descripción">{expense.description ?? "—"}</Field>
            <Field label="Registrado por">
              {expense.created_by_name} · {formatDateTime(expense.created_at)}
            </Field>
            {expense.ended_at && (
              <Field label={expense.state === "REVERSED" ? "Revertido por" : "Anulado por"}>
                {expense.ended_by_name} · {formatDateTime(expense.ended_at)}
                {expense.end_reason && (
                  <span className="block text-muted-foreground">Motivo: {expense.end_reason}</span>
                )}
              </Field>
            )}
          </dl>
        </CardContent>
      </Card>

      {expense.fuel && (
        <Card>
          <CardHeader>
            <CardTitle>Carga de combustible</CardTitle>
            <CardDescription>Importe = litros × precio por litro, calculado por el sistema.</CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <Field label="Vehículo">
                {expense.fuel.vehicle_plate} · {expense.fuel.vehicle_description}
                {!expense.fuel.vehicle_active && " (desactivado)"}
              </Field>
              <Field label="Empleado">{expense.fuel.employee_name}</Field>
              <Field label="Litros">{formatQuantity(expense.fuel.liters)}</Field>
              <Field label="Precio por litro">$ {formatQuantity(expense.fuel.price_per_liter)}</Field>
              <Field label="Odómetro">
                {expense.fuel.odometer_km != null ? `${expense.fuel.odometer_km} km` : "—"}
              </Field>
            </dl>
          </CardContent>
        </Card>
      )}

      {(expense.operation || expense.reversal) && (
        <Card>
          <CardHeader>
            <CardTitle>Movimiento en Cajas</CardTitle>
            <CardDescription>
              El gasto y su reverso son operaciones del ledger: se corrigen solo desde este gasto.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {expense.operation && <Legs operation={expense.operation} title="Gasto" />}
            {expense.reversal && <Legs operation={expense.reversal} title="Reverso" />}
          </CardContent>
        </Card>
      )}

      {expense.history.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Historial</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2 text-sm">
              {expense.history.map((event) => (
                <li key={event.id} className="space-y-0.5">
                  <p>
                    <span className="font-medium">{eventLabel(event.event_type)}</span>{" "}
                    <span className="text-muted-foreground">
                      {formatDateTime(event.occurred_at)}
                      {event.user_name && ` · ${event.user_name}`}
                    </span>
                  </p>
                  {event.event_type === "settlements.expense.corrected" && event.after && (
                    <ul className="text-muted-foreground">
                      {Object.entries(event.after)
                        .filter(([key]) => key !== "reason")
                        .map(([key, value]) => (
                          <li key={key}>
                            {key === "odometer_km" ? "Odómetro" : "Descripción"}: {change(event.before?.[key])} →{" "}
                            {change(value)}
                          </li>
                        ))}
                      {"reason" in event.after && <li>Motivo: {change(event.after.reason)}</li>}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {open === "reverse" && <ReverseDialog expense={expense} onClose={() => setOpen(null)} />}
      {open === "correct" && <CorrectDialog expense={expense} onClose={() => setOpen(null)} />}
    </>
  );
}
