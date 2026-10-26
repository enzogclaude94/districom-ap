"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useId, useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatDate, formatMoney, todayIso } from "@/lib/format";
import { parseAmount } from "@/modules/settlements/cash/amount";

import { deliveryApi, deliveryQueryKey, type Settlement } from "./api";
import { ActionDialog, AmountField, SelectField, TextField } from "./form";
import { settlementTitle } from "./labels";
import { useDeliveryAction } from "./use-delivery-action";

/**
 * Nueva rendición: date, driver, expected amount and reference. The number is never typed: the backend
 * gives the n-th settlement of that driver and day. A second one the same day needs an explicit "yes". The
 * expected amount may stay empty (R9-1): Store Sys or a manual load fills it before the close.
 */
export function NewSettlementDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (settlement: Settlement) => void;
}) {
  const dateId = useId();
  const confirmId = useId();
  const [businessDate, setBusinessDate] = useState(todayIso);
  const [employeeId, setEmployeeId] = useState<number | null>(null);
  const [expected, setExpected] = useState("");
  const [reference, setReference] = useState("");
  const [notes, setNotes] = useState("");
  const [sameDayConfirmed, setSameDayConfirmed] = useState(false);
  const action = useDeliveryAction("new-settlement", onCreated);

  const drivers = useQuery({
    queryKey: [...deliveryQueryKey, "drivers", businessDate],
    queryFn: () => deliveryApi.drivers(businessDate),
    enabled: Boolean(businessDate),
  });
  const sameDay = useQuery({
    queryKey: [...deliveryQueryKey, "same-day", businessDate, employeeId],
    queryFn: () =>
      deliveryApi.list({
        date_from: businessDate,
        date_to: businessDate,
        employee_id: employeeId!,
        limit: 50,
        offset: 0,
      }),
    enabled: employeeId !== null && Boolean(businessDate),
  });
  const existing = employeeId !== null ? (sameDay.data?.items ?? []) : [];
  const driverName = drivers.data?.find((driver) => driver.id === employeeId)?.full_name;

  const validate = () => {
    if (!businessDate) return "Indicá la fecha.";
    if (businessDate > todayIso()) return "La fecha no puede ser posterior a hoy.";
    if (employeeId === null) return "Elegí el repartidor.";
    if (expected.trim() && !parseAmount(expected)) return "Ingresá un importe esperado mayor a 0 o dejalo vacío.";
    if (existing.length > 0 && !sameDayConfirmed) return "Confirmá que es otra rendición del mismo día.";
    return null;
  };

  return (
    <ActionDialog
      title="Nueva rendición"
      description="El número se asigna solo, por repartidor y día."
      submitLabel="Crear rendición"
      action={action}
      onClose={onClose}
      validate={validate}
      build={(requestId) => {
        const amount = parseAmount(expected);
        return {
          method: "POST",
          path: "/deliveries",
          body: {
            client_request_id: requestId,
            business_date: businessDate,
            employee_id: employeeId,
            expected_amount: amount,
            external_reference: reference.trim() || null,
            notes: notes.trim() || null,
          },
          summary: `Rendición de ${driverName ?? "repartidor"} del ${formatDate(businessDate)} ${amount ? `por ${formatMoney(amount)}` : "sin esperado cargado"}`,
        };
      }}
    >
      <div className="space-y-1.5">
        <Label htmlFor={dateId}>Fecha</Label>
        <Input
          id={dateId}
          type="date"
          max={todayIso()}
          value={businessDate}
          onChange={(event) => {
            setBusinessDate(event.target.value);
            setEmployeeId(null);
            setSameDayConfirmed(false);
          }}
        />
      </div>
      <SelectField
        label="Repartidor"
        value={employeeId}
        onChange={(id) => {
          setEmployeeId(id);
          setSameDayConfirmed(false);
        }}
        placeholder={drivers.isPending ? "Cargando repartidores…" : "Elegí un repartidor…"}
        options={(drivers.data ?? []).map((driver) => ({ id: driver.id, name: driver.full_name }))}
      />
      {drivers.data?.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No hay repartidores con la función vigente ese día. Se asigna desde Empleados.
        </p>
      )}
      {existing.length > 0 && (
        <div
          role="alert"
          className="space-y-2 rounded-md border border-warning/40 bg-warning-soft px-3 py-2 text-sm"
          data-testid="same-day-warning"
        >
          <p className="flex items-center gap-2 font-medium">
            <AlertTriangle className="size-4 text-warning" aria-hidden />
            {driverName} ya tiene {existing.length === 1 ? "una rendición" : `${existing.length} rendiciones`} el{" "}
            {formatDate(businessDate)}
          </p>
          <ul className="list-inside list-disc">
            {existing.map((item) => (
              <li key={item.id}>
                {settlementTitle(item.sequence_number)} · esperado{" "}
                {item.expected_amount ? formatMoney(item.expected_amount) : "sin cargar"}
              </li>
            ))}
          </ul>
          <label htmlFor={confirmId} className="flex items-center gap-2">
            <input
              id={confirmId}
              type="checkbox"
              checked={sameDayConfirmed}
              onChange={(event) => setSameDayConfirmed(event.target.checked)}
            />
            Sí, es otra rendición del mismo día
          </label>
        </div>
      )}
      <AmountField label="Importe esperado" value={expected} onChange={setExpected} />
      <p className="text-xs text-muted-foreground">
        Si lo dejás vacío, lo completa Store Sys o lo cargás después. Hace falta para cerrar.
      </p>
      <TextField label="Referencia (opcional)" value={reference} onChange={setReference} maxLength={100} />
      <TextField label="Notas (opcional)" value={notes} onChange={setNotes} multiline />
    </ActionDialog>
  );
}
