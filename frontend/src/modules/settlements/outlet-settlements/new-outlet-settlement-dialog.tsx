"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useId, useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { outletsApi, outletsQueryKey } from "@/lib/api/outlets";
import { formatDate, formatMoney, todayIso } from "@/lib/format";
import type { Settlement } from "@/modules/settlements/delivery/api";
import { ActionDialog, AmountField, SelectField, TextField } from "@/modules/settlements/delivery/form";
import { settlementTitle } from "@/modules/settlements/delivery/labels";
import { useDeliveryAction } from "@/modules/settlements/delivery/use-delivery-action";

import { parseExpected } from "./amount";
import { outletSettlementApi, outletSettlementQueryKey } from "./api";

/**
 * Nueva rendición de boca (R7-2): date, active boca and, if Store Sys already gave it, the expected amount
 * ($0 is valid; empty means "not yet": it is loaded before closing). The number is never typed: the backend
 * gives the n-th settlement of that boca and day, and a second one needs an explicit "yes".
 */
export function NewOutletSettlementDialog({
  initialOutletId,
  onClose,
  onCreated,
}: {
  initialOutletId?: number;
  onClose: () => void;
  onCreated: (settlement: Settlement) => void;
}) {
  const dateId = useId();
  const confirmId = useId();
  const [businessDate, setBusinessDate] = useState(todayIso);
  const [outletId, setOutletId] = useState<number | null>(initialOutletId ?? null);
  const [expected, setExpected] = useState("");
  const [reference, setReference] = useState("");
  const [notes, setNotes] = useState("");
  const [sameDayConfirmed, setSameDayConfirmed] = useState(false);
  const action = useDeliveryAction("new-outlet-settlement", onCreated);

  // Only active bocas start a new settlement.
  const outlets = useQuery({ queryKey: [...outletsQueryKey, "active"], queryFn: () => outletsApi.list() });
  const sameDay = useQuery({
    queryKey: [...outletSettlementQueryKey, "same-day", businessDate, outletId],
    queryFn: () =>
      outletSettlementApi.list({
        date_from: businessDate,
        date_to: businessDate,
        outlet_id: outletId!,
        limit: 50,
        offset: 0,
      }),
    enabled: outletId !== null && Boolean(businessDate),
  });
  const existing = outletId !== null ? (sameDay.data?.items ?? []) : [];
  const outletName = outlets.data?.find((outlet) => outlet.id === outletId)?.name;
  const amount = parseExpected(expected);

  const validate = () => {
    if (!businessDate) return "Indicá la fecha.";
    if (businessDate > todayIso()) return "La fecha no puede ser posterior a hoy.";
    if (outletId === null) return "Elegí la boca.";
    if (expected.trim() && amount === null) return "Ingresá un importe esperado válido o dejalo vacío.";
    if (existing.length > 0 && !sameDayConfirmed) return "Confirmá que es otra rendición del mismo día.";
    return null;
  };

  return (
    <ActionDialog
      title="Nueva rendición de boca"
      description="El número se asigna solo, por boca y día."
      submitLabel="Crear rendición"
      action={action}
      onClose={onClose}
      validate={validate}
      build={(requestId) => ({
        method: "POST",
        path: "/outlet-settlements",
        body: {
          client_request_id: requestId,
          business_date: businessDate,
          outlet_id: outletId,
          expected_amount: amount,
          external_reference: reference.trim() || null,
          notes: notes.trim() || null,
          confirm_additional: sameDayConfirmed,
        },
        summary: `Rendición de ${outletName ?? "la boca"} del ${formatDate(businessDate)}${amount === null ? " sin esperado" : ` por ${formatMoney(amount)}`}`,
      })}
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
            setSameDayConfirmed(false);
          }}
        />
      </div>
      <SelectField
        label="Boca"
        value={outletId}
        onChange={(id) => {
          setOutletId(id);
          setSameDayConfirmed(false);
        }}
        placeholder={outlets.isPending ? "Cargando bocas…" : "Elegí una boca…"}
        options={(outlets.data ?? []).map((outlet) => ({ id: outlet.id, name: `${outlet.name} (${outlet.code})` }))}
      />
      {outlets.data?.length === 0 && (
        <p className="text-sm text-muted-foreground">No hay bocas activas. Se dan de alta desde Bocas de expendio.</p>
      )}
      {existing.length > 0 && (
        <div
          role="alert"
          className="space-y-2 rounded-md border border-warning/40 bg-warning-soft px-3 py-2 text-sm"
          data-testid="same-day-warning"
        >
          <p className="flex items-center gap-2 font-medium">
            <AlertTriangle className="size-4 text-warning" aria-hidden />
            {outletName} ya tiene {existing.length === 1 ? "una rendición" : `${existing.length} rendiciones`} el{" "}
            {formatDate(businessDate)}
          </p>
          <ul className="list-inside list-disc">
            {existing.map((item) => (
              <li key={item.id}>
                {settlementTitle(item.sequence_number)} · esperado{" "}
                {item.expected_amount === null ? "sin cargar" : formatMoney(item.expected_amount)}
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
      <AmountField label="Importe esperado de Store Sys (opcional)" value={expected} onChange={setExpected} />
      <p className="-mt-2 text-xs text-muted-foreground">
        Si todavía no lo tenés, dejalo vacío: se carga antes de cerrar. Un día sin ventas es $0.
      </p>
      <TextField label="Referencia (opcional)" value={reference} onChange={setReference} maxLength={100} />
      <TextField label="Notas (opcional)" value={notes} onChange={setNotes} multiline />
    </ActionDialog>
  );
}
